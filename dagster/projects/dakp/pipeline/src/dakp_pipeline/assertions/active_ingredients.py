"""Drug-composition assertion aggregation (``biolink:has_active_ingredient``).

Builds ``has_active_ingredient_assertions.tsv``: product -> active ingredient rows from the
two FDA sources DAKP already acquires.

* **DailyMed SPL** (``spl_products.parquet``): one row per manufactured product x active
  ingredient. The extractor keeps BOTH the labeled ingredient substance (``ATORVASTATIN
  CALCIUM``) and its active moiety (``ATORVASTATIN``); ``ingredient_kind`` records which,
  and both kinds become edges. Inactive ingredients are excluded at extraction time.
* **Drugs@FDA** (``products.parquet``): ``drug_name`` x each component of the
  ``;``-separated ``active_ingredient`` cell.

The subject is the PRODUCT NAME, resolved downstream by Tablassert/fullmap (this table is
text-only: no CURIE columns). Two guards keep self-referential edges out:

1. the shaper drops a row whose normalized product name equals the ingredient text
   (``Ibuprofen`` has_active_ingredient ``IBUPROFEN`` is a tautology, not composition);
2. Tablassert drops any edge whose resolved subject and object CURIEs coincide
   (``Statement.drop_self_loops``), which catches differently worded cells landing on one
   identifier.

These edges carry NO qualifiers: composition is a property of the product, not of a
species, disease or population. Animal labels therefore emit the same unqualified rows as
human ones. ``FDA_regulatory_approvals`` and ``supporting_spl_sets`` stay in the TSV for
audit; only ``edge_evidence`` (the ``dailymed:<set_id>`` CURIEs) reaches the edge, as
``publications``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from dakp_pipeline.assertions import (
    AT_MANUAL,
    INFORES_DAILYMED,
    INFORES_DAKP,
    INFORES_DRUGS_AT_FDA,
    KL_ASSERTION,
    row_for,
)
from dakp_pipeline.assertions.evidence import (
    build_fda_approval_index,
    dailymed_set_url,
    find_table,
    load_or_build_dailymed_evidence,
    pipe_safe_text,
    sorted_pipe,
    spl_evidence_pipe,
    write_assertion_table,
)
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.logging_setup import logger, stats, step

_TABLE = "has_active_ingredient_assertions"
PREDICATE = "biolink:has_active_ingredient"
_OPERATION = "shape_has_active_ingredient"

#: Edge provenance matches the other families: DAKP primary, both FDA sources upstream.
UPSTREAM_RESOURCE_IDS = sorted_pipe([INFORES_DAILYMED, INFORES_DRUGS_AT_FDA])


def _norm(text: object) -> str:
    """Case- and whitespace-insensitive identity for product/ingredient text."""
    return " ".join(str(text or "").split()).casefold()


def split_active_ingredients(cell: object) -> list[str]:
    """Split a Drugs@FDA ``ActiveIngredient`` cell on TOP-LEVEL semicolons only.

    ``EZETIMIBE; SIMVASTATIN`` yields two ingredients, but a combination whose components
    are parenthesized (``TRIPLE SULFA (SULFABENZAMIDE;SULFACETAMIDE;SULFATHIAZOLE)``) stays
    one ingredient: the semicolons inside the parentheses are not a product-level split.
    """
    text = str(cell or "")
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


@dataclass
class _Aggregate:
    """One (product, ingredient) pair accumulated across both FDA sources."""

    subject_text: str = ""
    object_text: str = ""
    kinds: set[str] = field(default_factory=set)
    sets: set[str] = field(default_factory=set)
    approvals: set[str] = field(default_factory=set)


def build_has_active_ingredient_rows(
    inputs: Iterable[ArtifactRef], *, ctx: TaskContext | None = None
) -> list[dict[str, str]]:
    """Aggregate DailyMed + Drugs@FDA composition into deterministic assertion rows.

    DailyMed rows cite their SPL sets (``publications``) and the application numbers on those
    sets; Drugs@FDA rows cite the product's own application number. Both merge into one row
    per normalized (product, ingredient) pair, keeping the lexicographically first casing of
    each text so the output is independent of input row order.
    """
    refs = list(inputs)
    with step(logger, "shape_has_active_ingredient.inputs"):
        evidence = load_or_build_dailymed_evidence(refs, ctx)
        approvals = build_fda_approval_index(refs)
        spl_products = find_table(refs, "spl_products.parquet")
        drugsfda_products = find_table(refs, "products.parquet")

    counters: Counter[str] = Counter()
    aggregates: dict[tuple[str, str], _Aggregate] = {}

    def bucket_for(product: str, ingredient: str) -> _Aggregate | None:
        """The aggregate for one pair, or None when the row must not become an edge."""
        if not product or not ingredient:
            counters["dropped_missing_text"] += 1
            return None
        key = (_norm(product), _norm(ingredient))
        if key[0] == key[1]:
            counters["dropped_self_named"] += 1
            return None
        agg = aggregates.get(key)
        if agg is None:
            agg = _Aggregate(subject_text=product, object_text=ingredient)
            aggregates[key] = agg
        else:
            # Deterministic casing: lexicographically first wins regardless of input order.
            agg.subject_text = min(agg.subject_text, product)
            agg.object_text = min(agg.object_text, ingredient)
        return agg

    if spl_products is not None:
        for rec in spl_products.iter_rows(named=True):
            product = pipe_safe_text(rec.get("product_name"))
            ingredient = pipe_safe_text(rec.get("ingredient_name"))
            agg = bucket_for(product, ingredient)
            if agg is None:
                continue
            counters["dailymed_pairs"] += 1
            set_id = str(rec.get("spl_set_id") or "").strip()
            if set_id:
                agg.sets.add(set_id)
                agg.approvals.update(
                    approvals.expand_all_for_edge(
                        evidence.approval_ids_by_set.get(set_id, ()),
                        animal_edge=evidence.is_animal_set(set_id),
                    )
                )
            kind = str(rec.get("ingredient_kind") or "").strip()
            if kind:
                agg.kinds.add(kind)

    if drugsfda_products is not None:
        for rec in drugsfda_products.iter_rows(named=True):
            product = pipe_safe_text(rec.get("drug_name"))
            if not product:
                counters["dropped_missing_text"] += 1
                continue
            displays = approvals.expand(
                rec.get("appl_no") or rec.get("appl_no_raw") or rec.get("appl_no_stripped")
            )
            for ingredient_text in split_active_ingredients(rec.get("active_ingredient")):
                agg = bucket_for(product, pipe_safe_text(ingredient_text))
                if agg is None:
                    continue
                counters["drugsfda_pairs"] += 1
                agg.kinds.add("substance")
                agg.approvals.update(displays)

    rows: list[dict[str, str]] = []
    for key in sorted(aggregates):
        agg = aggregates[key]
        rows.append(
            row_for(
                _TABLE,
                subject_text=agg.subject_text,
                predicate=PREDICATE,
                object_text=agg.object_text,
                ingredient_kind=sorted_pipe(agg.kinds),
                FDA_regulatory_approvals=sorted_pipe(agg.approvals),
                supporting_spl_sets=sorted_pipe(dailymed_set_url(s) for s in agg.sets),
                edge_evidence=spl_evidence_pipe(agg.sets, []),
                knowledge_level=KL_ASSERTION,
                agent_type=AT_MANUAL,
                primary_knowledge_source=INFORES_DAKP,
                upstream_resource_ids=UPSTREAM_RESOURCE_IDS,
            )
        )
    stats(
        logger,
        _OPERATION,
        rows=len(rows),
        distinct_pairs=len(aggregates),
        **dict(sorted(counters.items())),
    )
    return rows


class HasActiveIngredientShaper:
    """Edge-family shaper: FDA product composition, no NER (structured label data)."""

    def transform(self, inputs: list[ArtifactRef], ctx: TaskContext) -> list[ArtifactRef]:
        with step(logger, _OPERATION):
            rows = build_has_active_ingredient_rows(inputs, ctx=ctx)
            return write_assertion_table(_TABLE, rows, inputs, ctx, operation=_OPERATION)


__all__ = [
    "PREDICATE",
    "UPSTREAM_RESOURCE_IDS",
    "HasActiveIngredientShaper",
    "build_has_active_ingredient_rows",
    "split_active_ingredients",
]
