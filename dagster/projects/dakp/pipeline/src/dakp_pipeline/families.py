"""Edge-family registry: the extensibility seam for assertion types.

Every edge predicate DAKP emits is one :class:`EdgeFamily`: a name, its tabular
TSV contract (from ``io/schemas.ASSERTION_TABLES``), the Biolink predicate and
knowledge-level constants, the shaper class producing the table, and the NER
mining profile it needs. Shapers register here once; the Dagster assets, the
Tablassert config generation, and the KGX validator iterate the registry
instead of hardcoding the three shipped families.

Adding a new predicate type:
1. Implement a shaper with ``transform(inputs, ctx) -> list[ArtifactRef]`` and
   register its table in ``io/schemas.ASSERTION_TABLES``.
2. Declare an ``EdgeFamily`` here (constants live with the shaper module; the
   family record references them, never duplicates them).
3. Done: the Dagster stage, Tablassert configs, and downstream validation pick
   it up from the registry.

Behavior contract: the registry carries METADATA ONLY. Shaping semantics stay
with the shapers; registering a family must never change an emitted row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dakp_pipeline.io.contracts import ArtifactRef, TaskContext


class Shaper(Protocol):
    """The shaper protocol every edge family implements."""

    def transform(self, inputs: list[ArtifactRef], ctx: TaskContext) -> list[ArtifactRef]:
        """Shape input refs into this family's assertion table refs."""
        ...


@dataclass(frozen=True)
class EdgeFamily:
    """One edge predicate family: table contract + shaper wiring."""

    name: str  # registry key, e.g. "approved_treats"
    table: str  # tabular TSV file name, a key of io/schemas.ASSERTION_TABLES
    predicate: str  # biolink predicate of the emitted edges
    knowledge_level: str  # biolink knowledge_level (Knowledge Level enum)
    agent_type: str  # biolink agent_type
    ner_profile: str | None  # NER mining profile (indications|contraindications) or None
    shaper: type  # the Shaper class (instantiated per stage run)


def _approved_treats() -> EdgeFamily:
    from dakp_pipeline.assertions.approved_treats import ApprovedTreatsShaper

    return EdgeFamily(
        name="approved_treats",
        table="approved_treats_assertions",
        predicate="biolink:treats",
        knowledge_level="knowledge_assertion",
        agent_type="manual_validation_of_automated_agent",
        ner_profile="indications",
        shaper=ApprovedTreatsShaper,
    )


def _observed_uses() -> EdgeFamily:
    from dakp_pipeline.assertions.observed_uses import ObservedUsesShaper

    return EdgeFamily(
        name="faers_applied_to_treat",
        table="faers_applied_to_treat_assertions",
        predicate="biolink:applied_to_treat",
        knowledge_level="statistical_association",
        agent_type="manual_validation_of_automated_agent",
        ner_profile=None,  # FAERS text bypasses NER (direct intervention mapping)
        shaper=ObservedUsesShaper,
    )


def _contraindications() -> EdgeFamily:
    from dakp_pipeline.assertions.contraindications import ContraindicationsShaper

    return EdgeFamily(
        name="contraindications",
        table="contraindication_assertions",
        predicate="biolink:contraindicated_in",
        knowledge_level="knowledge_assertion",
        agent_type="manual_validation_of_automated_agent",
        ner_profile="contraindications",
        shaper=ContraindicationsShaper,
    )


# Registration order is the deterministic stage/build order (observed-uses consumes the
# approved_treats table, so it must follow it).
_FAMILY_BUILDERS = (_approved_treats, _observed_uses, _contraindications)


def all_families() -> list[EdgeFamily]:
    """All registered edge families in stage order (fresh instances each call)."""
    return [builder() for builder in _FAMILY_BUILDERS]


def get_family(name: str) -> EdgeFamily:
    """Look a family up by registry key; KeyError names the missing family."""
    for family in all_families():
        if family.name == name:
            return family
    msg = f"no edge family named {name!r}; registered: {sorted(f.name for f in all_families())}"
    raise KeyError(msg)


def family_tables() -> list[str]:
    """The registered tables in stage order (the Tablassert config + validator iterate this)."""
    return [family.table for family in all_families()]


__all__ = ["EdgeFamily", "Shaper", "all_families", "family_tables", "get_family"]
