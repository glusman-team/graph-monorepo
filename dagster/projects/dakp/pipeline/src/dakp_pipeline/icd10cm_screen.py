"""The ICD-10-CM non-therapeutic subject screen, derived at build time inside the DAG.

Two screens keep Table-screened poisons out of the graph, and BOTH are derived here from
artifacts the DAG already produces (the acquired ICD-10-CM release, the Drugs@FDA register,
the shaped approved-treats table, and the configured fullmap):

- **Wording screen** (:func:`derive_denylist`): every substance the Table lists in the
  non-therapeutic tiers (T36-T50 / T51-T65 with an empty Adverse-effect column) becomes a
  denylist wording, embedded in the faers table config as ONE Tablassert ``antijoin``
  membership filter on the subject column. Reporter text that literally names a poison never
  reaches reconciliation. Substances whose names occur inside regulator label material
  (approved-treats subjects, Drugs@FDA active ingredients, :data:`MEDICINAL_EXCEPTIONS`)
  are exempt, so lithium/zinc/arsenic-trioxide-style medicines survive.

- **Identifier screen** (:func:`derive_curies`): every screened wording is resolved through
  the production fullmap with the build's own subject settings; a CURIE whose own preferred
  name is a screened, non-exempt substance joins the subject ``exclude_regex`` of EVERY
  table. This closes the two leaks a wording filter cannot see: a reporter spelling that
  resolves to a poison it never names (``LEVOXINE`` -> hydrazine), and label-ingredient
  subjects of the contraindication table. Requires ``$DAKP_FULLMAP``; without one it emits a
  ``deferred`` marker and the configs fall back to the checked-in snapshot
  (:mod:`dakp_pipeline.icd10cm_curies`).

The checked-in snapshots (:mod:`dakp_pipeline.icd10cm_denylist`,
:mod:`dakp_pipeline.icd10cm_curies`) remain the offline default (CI, defs checks, tests)
and the regression baseline; ``tests/eval/icd10cm_{denylist,curies}.py`` are their writers
and share this module's functions, so an eval regeneration and a DAG derivation cannot
diverge in logic -- only in the data they saw.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import polars as pl

from dakp_pipeline.assertions.observed_uses import _pair_key
from dakp_pipeline.extract.icd10cm import (
    ICD10CM_SUBJECT_TIERS_FILENAME,
    NON_THERAPEUTIC_TIERS,
    SubjectTier,
)
from dakp_pipeline.io.artifact_store import ArtifactStore
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.io.manifests import OperationBlock
from dakp_pipeline.paths import Workdir

DENYLIST_OPERATION = "derive_icd10cm_screen"
CURIES_OPERATION = "derive_icd10cm_curies"
DENYLIST_FILENAME = "icd10cm_subject_denylist.json"
CURIES_FILENAME = "icd10cm_screened_curies.json"

#: Substances the corpora-derived exemption cannot see: human medicines licensed in DAKP's
#: jurisdictions but absent from the mined label tables (US-only DailyMed/Drugs@FDA/EMA-narrative
#: coverage). Keyed by normalized name; the rationale ships into the snapshot so a reviewer can see
#: why a Tier-1 "no Adverse effect code" entry survived.
MEDICINAL_EXCEPTIONS: dict[str, str] = {
    "dihydromorphine": "morphine metabolite analgesic licensed in Germany/Austria; DAKP edges are pain indications",
    "tilidine": "opioid analgesic licensed in Germany/Belgium/Israel; DAKP edges are neuralgia",
    "piritramide": "opioid antitussive licensed in Germany/Austria/Switzerland",
    "diamorphine": "pharmaceutical heroin, licensed for pain in the UK/Switzerland; distinct from the street entry 'heroin'",
    "paraffin": "liquid/soft paraffin is a licensed laxative and emollient in UK/EU products; DAKP edges are dry skin and constipation",
    "chromium": "essential trace element dosed in parenteral nutrition and supplements, like the exempted zinc/copper/selenium",
    "phosphorus": "elemental name under which phosphate salts are dosed in supplementation (the dropped edge treats hypophosphataemia, i.e. replacement therapy)",
}


def name_spans(key: str) -> set[str]:
    """Every contiguous whole-word run of a normalized key, including the key itself."""
    words = key.split(" ")
    return {
        " ".join(words[start:end])
        for start in range(len(words))
        for end in range(start + 1, len(words) + 1)
    }


def vocabulary(approved_subjects: list[str], drugsfda_ingredients: list[str]) -> dict[str, str]:
    """``exempt term -> example evidence`` from the corpora DAKP treats as label material."""
    exempt: dict[str, str] = {}
    for subject in sorted(set(approved_subjects)):
        # sorted order: which subject supplies a shared span's evidence must not depend on
        # iteration order.
        for run in name_spans(_pair_key(str(subject))):
            exempt.setdefault(run, f"approved-treats subject: {subject}")
    for cell in sorted(set(drugsfda_ingredients) - {""}):
        for ingredient in cell.split(";"):
            for run in name_spans(_pair_key(ingredient)):
                exempt.setdefault(run, f"Drugs@FDA active ingredient: {cell}")
    for name, rationale in MEDICINAL_EXCEPTIONS.items():
        exempt.setdefault(name, f"medicinal exception: {rationale}")
    return exempt


def label_vocabulary(approved_treats: Path, drugsfda_zip: Path) -> dict[str, str]:
    """:func:`vocabulary` from the published artifacts: the approved-treats TSV + the Drugs@FDA zip."""
    subjects = (
        pl.scan_csv(approved_treats, separator="\t")
        .select("subject_text")
        .unique()
        .collect()
        .get_column("subject_text")
        .to_list()
    )
    with zipfile.ZipFile(drugsfda_zip) as zf:
        member = next(name for name in zf.namelist() if name.endswith("Products.txt"))
        text = io.TextIOWrapper(zf.open(member), encoding="utf-8-sig", errors="replace").read()
    ingredients = [
        (row.get("ActiveIngredient") or "").strip()
        for row in csv.DictReader(io.StringIO(text), delimiter="\t")
    ]
    return vocabulary([str(s) for s in subjects], ingredients)


def screen(
    rows: list[SubjectTier], exempt: dict[str, str]
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """Split classified Table rows into ``(denylist entries, exempt entries)``."""
    denied: dict[str, tuple[str, str]] = {}
    spared: dict[str, str] = {}
    for row in rows:
        key = _pair_key(row.name)
        if len(key) < 4:
            continue  # a class label or fragment (GAS, DUST, OIL), not a screenable substance name
        if row.tier not in NON_THERAPEUTIC_TIERS:
            continue
        if key in exempt:
            spared.setdefault(row.name, exempt[key])
            continue
        previous = denied.get(key)
        # toxic_effect is the stronger claim (a noxious substance rather than merely a drug with no
        # US adverse-effect code), so it wins when one normalized key arrives on several rows.
        if previous is None or (
            previous[0] == "no_accepted_medical_use" and row.tier == "toxic_effect"
        ):
            denied[key] = (row.tier, row.name)
    entries = sorted(
        (name, tier, code) for name, (tier, code) in ((k, v) for k, v in denied.items())
    )
    return entries, sorted(spared.items())


def denied_wordings(entries: list[tuple[str, str, str]]) -> tuple[str, ...]:
    """The names the emitted config denies: the normalized key AND the Table's own spelling.

    A Tablassert ``antijoin`` compares whole cells literally, so the list ships both spellings:
    the key catches reporter text differing only in case ("HEROIN"), the published spelling
    catches punctuation the normalizer folds away ("P-DICHLOROBENZENE" against the key
    "p dichlorobenzene"). The config emitter expands each into its casing variants.
    """
    return tuple(dict.fromkeys(name for row in entries for name in (row[0], row[2])))


def screened_curies(
    wordings: list[str],
    *,
    resolve: Callable[..., object],
    screened_keys: set[str],
    exempt_keys: set[str],
    base_exclude_regex: list[str],
    prioritize: list[str],
    avoid: list[str],
) -> list[tuple[str, str, str]]:
    """Fixed-point CURIE derivation over a fullmap resolver.

    ``resolve(words, exclude_regex)`` must run the build's own subject resolution (level-one/
    two normalization, probe keys, fetch, filter-and-rank with ``prioritize``/``avoid``) and
    return ``[(curie, preferred name), ...]`` per word. Each round records every resolved CURIE
    whose own preferred name normalizes to a screened, non-exempt substance, then re-resolves
    with all found CURIEs excluded: excluding one CURIE can make a wording fall through to
    ANOTHER screened one (``limonene`` -> CHEBI:15384, then CHEBI:15382). The preferred-name
    rule is deliberate: a synonym-only match would exclude real medicines the Table lists
    under street or trade names (warfarin via ``D-Con``, nitroglycerin via ``dynamite``).
    Returns ``(curie, preferred name, screened key)`` sorted by CURIE.
    """
    found: dict[str, tuple[str, str]] = {}
    while True:
        excluded = base_exclude_regex + [f"^{curie}$" for curie in sorted(found)]
        resolved = resolve(sorted(wordings), excluded)
        new: dict[str, tuple[str, str]] = {}
        for curie, name in resolved:
            key = _pair_key(str(name))
            if curie not in found and key in screened_keys and key not in exempt_keys:
                new[str(curie)] = (str(name), key)
        if not new:
            return sorted((curie, name, key) for curie, (name, key) in found.items())
        found.update(new)


def quick_map_resolver(fullmap: str) -> Callable[..., list[tuple[str, str]]]:
    """The production resolver: ``tablassert.quick_map`` against one fullmap redb."""
    from tablassert.fullmap import fullmap_db_path, quick_map

    def resolve(words: list[str], exclude_regex: list[str]) -> list[tuple[str, str]]:
        db = fullmap_db_path(Path(fullmap))
        frames = quick_map(words, db, exclude_regex=exclude_regex)
        return [
            (str(curie), str(name))
            for frame in frames.values()
            for curie, name in zip(
                frame["CURIE"].to_list(), frame["PREFERRED_NAME"].to_list(), strict=True
            )
        ]

    return resolve


# --- DAG stage entry points -----------------------------------------------------------


def _tiers(inputs: list[ArtifactRef]) -> list[SubjectTier]:
    """The classified Table rows from the extract_icd10cm subject-tiers artifact."""
    ref = next((ref for ref in inputs if ref.uri.name == ICD10CM_SUBJECT_TIERS_FILENAME), None)
    if ref is None:
        msg = f"no {ICD10CM_SUBJECT_TIERS_FILENAME} among the inputs (run extract_icd10cm first)"
        raise ValueError(msg)
    frame = pl.read_csv(ref.uri, separator="\t")
    return [
        SubjectTier(name=row["name"], icd10cm_code=row["icd10cm_code"], tier=row["tier"])
        for row in frame.iter_rows(named=True)
    ]


def derive_denylist(inputs: list[ArtifactRef], ctx: TaskContext) -> list[ArtifactRef]:
    """Wording screen from DAG artifacts; publishes one denylist JSON ref.

    Inputs: the extract_icd10cm subject tiers, the acquired Drugs@FDA zip, and the shaped
    approved-treats table (its subject texts are the label-side exemption corpus).
    """
    from dakp_pipeline import icd10cm_denylist
    from dakp_pipeline.sources.icd10cm import ICD10CM_DRUG_TABLE_ZIP_URL, ICD10CM_RELEASE
    # nothing below needs the tablassert package (the wording screen runs ungated)

    tiers_ref = next(
        (ref for ref in inputs if ref.uri.name == ICD10CM_SUBJECT_TIERS_FILENAME), None
    )
    # exactly one zip is in scope here: the acquired Drugs@FDA register (the ICD-10-CM zip is
    # consumed by extract_icd10cm upstream, whose tiers TSV is this stage's first input).
    zip_ref = next((ref for ref in inputs if ref.uri.suffix.lower() == ".zip"), None)
    treats_ref = next((ref for ref in inputs if ref.uri.name.startswith("approved_treats")), None)
    missing = [
        name
        for name, ref in (
            ("extract_icd10cm tiers", tiers_ref),
            ("acquire_drugsfda zip", zip_ref),
            ("shape_approved_treats table", treats_ref),
        )
        if ref is None
    ]
    if missing:
        msg = f"derive_icd10cm_screen missing inputs: {missing}"
        raise ValueError(msg)

    entries, exempt = screen(_tiers(inputs), label_vocabulary(treats_ref.uri, zip_ref.uri))
    wordings = denied_wordings(entries)
    if not wordings:
        msg = "derive_icd10cm_screen derived an empty wording denylist; refusing to publish"
        raise ValueError(msg)

    payload = {
        "release": ICD10CM_RELEASE,
        "source_url": ICD10CM_DRUG_TABLE_ZIP_URL,
        "source_blake3": zip_ref.blake3,
        "screened": [list(row) for row in entries],
        "exempt": [list(row) for row in exempt],
        "wordings": list(wordings),
        "snapshot_key_count": len(icd10cm_denylist.SCREENED_SUBJECTS),
    }
    wd = Workdir(ctx.workdir)
    out = wd.interim / "icd10cm_screen" / DENYLIST_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    store = ArtifactStore(wd)
    ref = store.register(
        out,
        media_type="application/json",
        rows=len(entries),
        inputs=[tiers_ref.blake3, zip_ref.blake3, treats_ref.blake3],
        operation=OperationBlock(name=DENYLIST_OPERATION),
    )
    return [ref]


def derive_curies(inputs: list[ArtifactRef], ctx: TaskContext) -> list[ArtifactRef]:
    """Identifier screen from the derived wordings + the configured fullmap.

    Without ``$DAKP_FULLMAP`` publishes a ``deferred`` marker (the configs then fall back to
    the checked-in :mod:`dakp_pipeline.icd10cm_curies` snapshot), mirroring the kg_build
    deferral contract.
    """
    deny_ref = next((ref for ref in inputs if ref.uri.name == DENYLIST_FILENAME), None)
    if deny_ref is None:
        msg = f"no {DENYLIST_FILENAME} among the inputs (run derive_icd10cm_screen first)"
        raise ValueError(msg)
    payload = json.loads(deny_ref.uri.read_text(encoding="utf-8"))

    fullmap = ctx.params.get("fullmap")
    wd = Workdir(ctx.workdir)
    out = wd.interim / "icd10cm_screen" / CURIES_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    if not fullmap:
        out.write_text(
            json.dumps(
                {"mode": "deferred", "reason": "no fullmap configured ($DAKP_FULLMAP)"},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        store = ArtifactStore(wd)
        return [
            store.register(
                out,
                media_type="application/json",
                inputs=[deny_ref.blake3],
                operation=OperationBlock(name=CURIES_OPERATION),
            )
        ]
    try:
        from dakp_pipeline import tablassert as dakp_tablassert
    except ImportError as error:  # the CURIE screen needs the build's subject settings
        msg = (
            "derive_icd10cm_curies needs the tablassert package (install the pipeline's "
            "'tablassert' extra) to reuse the build's subject resolution settings"
        )
        raise RuntimeError(msg) from error

    screened_keys = {row[0] for row in payload["screened"]}
    exempt_keys = {_pair_key(name) for name, _evidence in payload["exempt"]}
    curies = screened_curies(
        list(payload["wordings"]),
        resolve=quick_map_resolver(str(fullmap)),
        screened_keys=screened_keys,
        exempt_keys=exempt_keys,
        base_exclude_regex=list(dakp_tablassert._SUBJECT_EXCLUDE_REGEX_CURATED),
        prioritize=list(dakp_tablassert.SUBJECT_PRIORITIZE),
        avoid=dakp_tablassert.category_avoid_list(dakp_tablassert.SUBJECT_PRIORITIZE),
    )
    if not curies:
        msg = "derive_icd10cm_curies derived no screened CURIEs; refusing to publish"
        raise ValueError(msg)
    out.write_text(
        json.dumps(
            {"mode": "ok", "fullmap": str(fullmap), "curies": [list(row) for row in curies]},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    store = ArtifactStore(wd)
    return [
        store.register(
            out,
            media_type="application/json",
            rows=len(curies),
            inputs=[deny_ref.blake3],
            operation=OperationBlock(name=CURIES_OPERATION),
        )
    ]


def screen_params(refs: list[ArtifactRef]) -> dict[str, list[str]] | None:
    """The runtime screen override for ``tablassert.generate`` from the derived refs.

    Returns ``{"denylist": [...], "curies": [...]}`` when the CURIE derivation ran, else
    ``None`` (deferred marker or refs absent) so the configs fall back to the checked-in
    snapshots.
    """
    curies_ref = next((ref for ref in refs if ref.uri.name == CURIES_FILENAME), None)
    if curies_ref is None:
        return None
    curies_payload = json.loads(curies_ref.uri.read_text(encoding="utf-8"))
    if curies_payload.get("mode") != "ok":
        return None
    deny_ref = next((ref for ref in refs if ref.uri.name == DENYLIST_FILENAME), None)
    if deny_ref is None:
        msg = f"{CURIES_FILENAME} is ok but {DENYLIST_FILENAME} is missing from the refs"
        raise ValueError(msg)
    deny_payload = json.loads(deny_ref.uri.read_text(encoding="utf-8"))
    return {
        "denylist": list(deny_payload["wordings"]),
        "curies": [row[0] for row in curies_payload["curies"]],
    }
