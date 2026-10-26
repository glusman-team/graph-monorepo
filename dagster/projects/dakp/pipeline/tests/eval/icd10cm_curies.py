"""Derive the ICD-10-CM screened subject CURIEs that ship as subject ``exclude_regex`` patterns.

``src/dakp_pipeline/icd10cm_curies.py`` is a GENERATED snapshot and this tool is its only writer.

Why a CURIE screen on top of the wording denylist
-------------------------------------------------
``icd10cm_denylist.SUBJECT_DENYLIST`` drops observed-use rows whose subject TEXT literally names a
screened substance. Two leaks survive it (measured on the 1.25.1 candidate build):

* reporter spellings that RESOLVE to a screened substance without naming it (FAERS ``LEVOXINE`` ->
  CHEBI:15571 hydrazine, ``TPN`` -> CHEBI:3639 chlorothalonil, ``DIAZIDE`` -> CHEBI:34682 diazinon);
* label-ingredient subjects of the contraindication table, which the wording screen never covered
  (``TURPENTINE``, ``Formaldehyde``, ``HEXYLRESORCINOL``).

Both end with a poison CURIE as an edge subject. This tool resolves every screened name through the
production fullmap with the SAME subject settings the table configs use (``tablassert.quick_map``:
the build's own op chain) and records each resolved CURIE whose OWN preferred name is a screened,
non-exempt substance. Excluding those CURIEs at resolution drops the poison identity in every
table while a mis-resolved wording falls through to its real drug (``LEVOXINE`` -> levothyroxine).
The preferred-name rule is deliberate: a synonym-only match would exclude real medicines the Table
lists under street or trade names (warfarin via ``D-Con``, nitroglycerin via ``dynamite``).

Resolution iterates to a fixed point: once a screened CURIE is excluded, its wordings may fall
through to ANOTHER screened CURIE (``limonene`` -> CHEBI:15384, then CHEBI:15382), so the derivation
re-resolves with every CURIE found so far excluded until no new one appears.

Run on the machine holding the production fullmap (wenceslaus)::

    uv run python tests/eval/icd10cm_curies.py --fullmap /path/fullmap [--write | --check]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from dakp_pipeline import icd10cm_denylist
from dakp_pipeline import tablassert as dakp_tablassert
from dakp_pipeline.assertions.observed_uses import _pair_key

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "src" / "dakp_pipeline" / "icd10cm_curies.py"


def derive(fullmap: Path) -> list[tuple[str, str, str]]:
    """``(curie, preferred name, screened key)`` for every screened subject CURIE, sorted."""
    from tablassert.fullmap import fullmap_db_path, quick_map

    db = fullmap_db_path(fullmap)
    screened = {row[0] for row in icd10cm_denylist.SCREENED_SUBJECTS}
    exempt = {_pair_key(name) for name, _ in icd10cm_denylist.LABEL_BACKED_EXCEPTIONS}
    prioritize = list(dakp_tablassert.SUBJECT_PRIORITIZE)
    avoid = dakp_tablassert.category_avoid_list(dakp_tablassert.SUBJECT_PRIORITIZE)
    curated = list(dakp_tablassert._SUBJECT_EXCLUDE_REGEX_CURATED)
    found: dict[str, tuple[str, str]] = {}
    while True:
        excluded = curated + [f"^{curie}$" for curie in sorted(found)]
        resolved = quick_map(
            list(icd10cm_denylist.SUBJECT_DENYLIST),
            db,
            prioritize=prioritize,
            avoid=avoid,
            exclude_regex=excluded,
        )
        new: dict[str, tuple[str, str]] = {}
        for frame in resolved.values():
            for curie, name in zip(
                frame["CURIE"].to_list(), frame["PREFERRED_NAME"].to_list(), strict=True
            ):
                key = _pair_key(str(name))
                if curie not in found and key in screened and key not in exempt:
                    new[curie] = (str(name), key)
        if not new:
            break
        found.update(new)
    return sorted((curie, name, key) for curie, (name, key) in found.items())


MODULE_TEMPLATE = '''"""Generated ICD-10-CM screened subject CURIEs.

GENERATED FILE, do not edit by hand. Regenerate on the machine holding the production fullmap::

    uv run python tests/eval/icd10cm_curies.py --fullmap <fullmap> --write

then regenerate ``tables/*.yaml``: ``tablassert._SUBJECT_EXCLUDE_REGEX`` appends one anchored
``^CURIE$`` pattern per entry, so the subject resolution of EVERY assertion table drops these
identities. Each entry is a CURIE the production fullmap resolves a screened ICD-10-CM Table of
Drugs and Chemicals name to AND whose own preferred name is that screened (non label-backed)
substance; see tests/eval/icd10cm_curies.py for the derivation and its fixed-point iteration.
"""

from __future__ import annotations

#: The fullmap source release the snapshot was resolved against.
FULLMAP_SOURCE = "{fullmap_source}"

#: ``(curie, fullmap preferred name, screened key)`` sorted by CURIE.
SCREENED_CURIES: tuple[tuple[str, str, str], ...] = (
{rows})
'''


def render_module(entries: list[tuple[str, str, str]], fullmap_source: str) -> str:
    rows = "".join(f"    ({curie!r}, {name!r}, {key!r}),\n" for curie, name, key in entries)
    return MODULE_TEMPLATE.format(rows=rows, fullmap_source=fullmap_source)


def ruff_normalize(text: str) -> str:
    ruff = shutil.which("ruff")
    if ruff is None:
        return text
    proc = subprocess.run(
        [ruff, "format", "--stdin-filename", SNAPSHOT_PATH.name, "-"],
        input=text,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="derive the ICD-10-CM screened subject CURIEs")
    parser.add_argument("--fullmap", type=Path, required=True, help="production fullmap path")
    parser.add_argument(
        "--fullmap-source", default="2026jul22", help="fullmap source tag recorded in the snapshot"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="rewrite the generated snapshot")
    mode.add_argument("--check", action="store_true", help="fail if the snapshot is stale")
    args = parser.parse_args(argv)

    entries = derive(args.fullmap)
    if not entries:
        print("derivation found no screened CURIEs; check the fullmap", file=sys.stderr)
        return 1
    for curie, name, key in entries:
        print(f"{curie:40s} {name[:40]:40s} {key}")
    print(f"screened subject CURIEs: {len(entries)}")
    text = ruff_normalize(render_module(entries, args.fullmap_source))
    if args.check:
        if SNAPSHOT_PATH.read_text(encoding="utf-8") != text:
            print(f"stale snapshot: {SNAPSHOT_PATH}", file=sys.stderr)
            return 1
        return 0
    if args.write:
        SNAPSHOT_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
