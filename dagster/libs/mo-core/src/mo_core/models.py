"""Domain models + JSON Schema export (single source of truth for codegen).

The schemas written by `export_json_schema` are consumed by each DAG's
`workers/go/gen/` (quicktype). Extraction/NER record schemas matter most:
DAKP-style raw inputs flow into Go workers, KG structs flow out to mo-kg.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field


class ExtractionRecord(BaseModel):
    """Raw input record entering a DAKP-pattern pipeline."""

    id: str
    source: str
    text: str
    metadata: dict[str, str] = Field(default_factory=dict)


class ExtractedEntity(BaseModel):
    """Entity emitted by a Go extraction worker."""

    text: str
    entity_type: str
    source_record_id: str
    confidence: float = Field(ge=0.0, le=1.0)


class KGNode(BaseModel):
    """KGX node record (biolink-style)."""

    id: str
    name: str
    category: str
    description: str | None = None


class KGEdge(BaseModel):
    """KGX edge record (biolink-style)."""

    subject: str
    predicate: str
    object: str
    knowledge_level: Literal["observation", "prediction", "inference"] = "prediction"


#: Models covered by the schema export, in export order.
EXPORTED_MODELS = [ExtractionRecord, ExtractedEntity, KGNode, KGEdge]


def export_json_schema(out_path) -> list:
    """Write each exported model's JSON Schema to out_path/<name>.json.

    Called per-DAG by a `go generate` hook (quicktype reads the JSON).
    Returns the list of files written.
    """
    from pathlib import Path

    out = Path(out_path)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for model in EXPORTED_MODELS:
        target = out / f"{model.__name__}.json"
        target.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        written.append(target)
    return written
