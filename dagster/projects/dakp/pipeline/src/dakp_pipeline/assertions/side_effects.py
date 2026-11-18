"""Label-backed side effects from explicit DailyMed reaction descriptions.

This intentionally covers only reaction lists in safety sections already mined by the
contraindication workflow. It does not infer side effects from rejected contraindications,
FAERS reports, or every phenotype mentioned in a warning. The shared mining path retains
singleton ingredients, source-local evidence, species and deterministic aggregation.
"""

from dakp_pipeline.assertions.contraindications import build_contraindication_rows
from dakp_pipeline.assertions.evidence import write_assertion_table
from dakp_pipeline.assertions.ner_dispatch import _resolve_devices, default_ner
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.logging_setup import logger, stats
from dakp_pipeline.ner.mention_cache import MentionCache
from dakp_pipeline.ner.ner import DiseaseNER

_TABLE = "side_effect_assertions"
_PREDICATE = "biolink:has_side_effect"


class SideEffectsShaper:
    """Emit only explicit treatment-reaction lists, never section-level guesses."""

    def transform(self, inputs: list[ArtifactRef], ctx: TaskContext) -> list[ArtifactRef]:
        ner_param = ctx.params.get("ner")
        ner = ner_param if isinstance(ner_param, DiseaseNER) else default_ner(ctx.fixture_root)
        with MentionCache(ctx.workdir) as cache:
            rows = build_contraindication_rows(
                inputs,
                ner,
                devices=_resolve_devices(ner),
                cache=cache,
                ctx=ctx,
                described_reactions_only=True,
            )
        for row in rows:
            row["predicate"] = _PREDICATE
            row["assertion_context"] = "side_effect"
            # A product's application number is not regulatory approval of a side effect.
            row["FDA_regulatory_approvals"] = ""
            row["disease_context_text"] = ""
        stats(logger, "shape_side_effects", assertions=len(rows))
        return write_assertion_table(_TABLE, rows, inputs, ctx, operation="shape_side_effects")
