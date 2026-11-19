"""Typed NDJSON datasets for exact object-span evaluation."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

CaseSource = Literal["dailymed", "faers"]
ObjectType = Literal["Disease", "PhenotypicFeature"]
NonBlank = Annotated[str, StringConstraints(pattern=r"\S")]
DAILYMED_LOINCS = frozenset({"34066-1", "34070-3"})


class Annotation(BaseModel):
    """Exact object surface; give a start index only when the surface is ambiguous."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    surface: NonBlank
    type: ObjectType
    start: Annotated[int, Field(ge=0)] | None = None

    def offset(self, text: str) -> int:
        """Resolve a zero-based Python character index, not a byte offset."""
        if self.start is not None:
            if not text.startswith(self.surface, self.start):
                raise ValueError(f"surface {self.surface!r} does not match start {self.start}")
            return self.start
        start = text.find(self.surface)
        if start < 0 or text.find(self.surface, start + 1) >= 0:
            raise ValueError(f"surface {self.surface!r} must occur exactly once or specify start")
        return start


class NERCase(BaseModel):
    """One source snippet and its exhaustive annotations (empty for a negative control)."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: NonBlank
    source: CaseSource
    loinc: Literal["34066-1", "34070-3"] | None = None
    text: NonBlank
    mentions: tuple[Annotation, ...]
    origin: NonBlank | None = None

    @model_validator(mode="after")
    def validate_case(self) -> Self:
        """Reject ambiguous/overlapping labels and unsupported source/section pairs."""
        if self.source == "dailymed" and self.loinc not in DAILYMED_LOINCS:
            raise ValueError("DailyMed requires a supported LOINC")
        if self.source == "faers" and self.loinc is not None:
            raise ValueError("FAERS must not declare a LOINC")
        for previous, current in pairwise(sorted(self.spans())):
            if current[0] < previous[1]:
                raise ValueError("overlapping surfaces")
        return self

    def spans(self) -> tuple[tuple[int, int, ObjectType], ...]:
        """Return annotation keys in file order with zero-based, half-open offsets."""
        return tuple(
            (start, start + len(mention.surface), mention.type)
            for mention in self.mentions
            for start in (mention.offset(self.text),)
        )


class NERDataset(BaseModel):
    """Ordered corpus; combine multiple NDJSON files and reject duplicate IDs."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    cases: Annotated[tuple[NERCase, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def require_unique_ids(self) -> Self:
        """Prevent predictions for one case from silently replacing another."""
        seen: set[str] = set()
        for case in self.cases:
            if case.id in seen:
                raise ValueError(f"case id is duplicated: {case.id}")
            seen.add(case.id)
        return self

    @classmethod
    def load(cls, *paths: Path) -> Self:
        """Read UTF-8 NDJSON in order; ignore blank lines and report malformed line locations."""
        cases: list[NERCase] = []
        for path in paths:
            with path.open(encoding="utf-8") as stream:
                for number, line in enumerate(stream, 1):
                    if line.strip():
                        try:
                            cases.append(NERCase.model_validate_json(line))
                        except ValueError as exc:
                            raise ValueError(f"{path}:{number}: {exc}") from exc
        return cls(cases=tuple(cases))
