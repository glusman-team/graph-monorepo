"""Generate the Hugging Face dataset card from real release outputs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import shutil
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

import yaml

from dakp_pipeline.release.delta import SourceDelta
from dakp_pipeline.release.verify import ReleaseVerification

_TEMPLATE_PATH = Path(__file__).with_name("card_template.md")
_RELEASE_HEADER = "| Version | Nodes | Edges | File size |"


@dataclass(frozen=True)
class ReleaseFile:
    name: str
    path: Path
    bytes: int
    md5: str

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "path": str(self.path)}


@dataclass(frozen=True)
class KGStats:
    nodes: int
    edges: int
    node_categories: dict[str, int]
    edge_predicates: dict[str, int]
    node_namespaces: dict[str, int]
    species_context_edges: int
    chemical_subjects: int
    disease_objects: int
    study_backed_edges: int


@dataclass(frozen=True)
class CardResult:
    markdown: str
    stats: KGStats
    files: tuple[ReleaseFile, ...]


def _rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def compute_kg_stats(nodes_path: Path, edges_path: Path) -> KGStats:
    categories: Counter[str] = Counter()
    namespaces: Counter[str] = Counter()
    nodes = 0
    for node in _rows(nodes_path):
        nodes += 1
        node_id = str(node.get("id") or "")
        if ":" in node_id:
            namespaces[node_id.split(":", 1)[0]] += 1
        raw_categories = node.get("category") or []
        if isinstance(raw_categories, str):
            raw_categories = [raw_categories]
        for category in raw_categories:
            categories[str(category)] += 1
    predicates: Counter[str] = Counter()
    edges = species = chemicals = diseases = study_backed = 0
    for edge in _rows(edges_path):
        edges += 1
        predicates[str(edge.get("predicate") or "unknown")] += 1
        chemicals += str(edge.get("subject") or "").startswith("CHEBI:")
        diseases += str(edge.get("object") or "").startswith("MONDO:")
        qualifiers = edge.get("qualifiers") or []
        species += any(
            qualifier.get("qualifier_type_id") == "biolink:species_context_qualifier"
            for qualifier in qualifiers
        )
        study_backed += bool(edge.get("has_supporting_studies"))
    return KGStats(
        nodes=nodes,
        edges=edges,
        node_categories=dict(sorted(categories.items())),
        edge_predicates=dict(sorted(predicates.items())),
        node_namespaces=dict(sorted(namespaces.items())),
        species_context_edges=species,
        chemical_subjects=chemicals,
        disease_objects=diseases,
        study_backed_edges=study_backed,
    )


def file_stat(path: Path) -> ReleaseFile:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    return ReleaseFile(path.name, path, path.stat().st_size, digest.hexdigest())


def stage_release_files(
    *,
    staging_root: Path,
    version: str,
    nodes_path: Path,
    edges_path: Path,
    rig_path: Path,
) -> tuple[ReleaseFile, ...]:
    version_dir = staging_root / version
    version_dir.mkdir(parents=True, exist_ok=True)
    stem = f"DRUG_APPROVALS_KP_{version}"
    destinations = {
        nodes_path: version_dir / f"{stem}.nodes.ndjson",
        edges_path: version_dir / f"{stem}.edges.ndjson",
        rig_path: version_dir / f"{stem}.RIG.yaml",
    }
    files = []
    for source, destination in destinations.items():
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        files.append(file_stat(destination))
    return tuple(files)


def _front_matter(previous_card: str) -> str:
    if previous_card.startswith("---\n"):
        end = previous_card.find("\n---\n", 4)
        if end >= 0:
            return previous_card[: end + 5]
    return """---
license: apache-2.0
card_license: apache-2.0
pretty_name: Drug Approvals Knowledge Provider (DAKP)
language: en
task_categories: graph-ml
tags: [knowledge-graph, kgx, biolink, translator, drug-approvals]
size_categories: 100K<n<1M
---
"""


def previous_release_rows(previous_card: str) -> list[str]:
    lines = previous_card.splitlines()
    rows: list[str] = []
    in_table = False
    for line in lines:
        if line.startswith(_RELEASE_HEADER):
            in_table = True
            continue
        if in_table:
            if line.startswith("| ---"):
                continue
            if line.startswith("| v"):
                rows.append(line)
            elif not line.strip() or not line.startswith("|"):
                break
    return rows


def _latest_previous_stats(rows: list[str]) -> tuple[int, int] | None:
    if not rows:
        return None
    cells = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]
    try:
        return int(cells[1]), int(cells[2])
    except (IndexError, ValueError):
        return None


def _human_bytes(size: int) -> str:
    mib = size / (1 << 20)
    return f"{mib:.1f} MB" if mib < 1024 else f"{mib / 1024:.2f} GB"


def _counter_rows(counter: dict[str, int]) -> str:
    return "\n".join(f"| {name} | {count} |" for name, count in counter.items()) or "| none | 0 |"


def _biolink_version(rig_path: Path) -> str:
    payload = yaml.safe_load(rig_path.read_text(encoding="utf-8"))
    stack = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                if "biolink" in str(key).lower() and "version" in str(key).lower():
                    return str(item)
                stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)
    match = re.search(r"biolink[^\n]{0,60}?(\d+\.\d+\.\d+)", str(payload), re.IGNORECASE)
    return match.group(1) if match else "declared in the emitted RIG"


def tablassert_version() -> str:
    try:
        return importlib.metadata.version("tablassert")
    except importlib.metadata.PackageNotFoundError:
        return "not importable in the card-generation environment"


def build_dataset_card(
    *,
    version: str,
    files: tuple[ReleaseFile, ...],
    stats: KGStats,
    rig_path: Path,
    delta: SourceDelta,
    previous_card: str,
    verification: ReleaseVerification,
    built_at: datetime | None = None,
) -> CardResult:
    old_rows = [row for row in previous_release_rows(previous_card) if f"| v{version} " not in row]
    total_bytes = sum(file.bytes for file in files)
    new_row = f"| v{version} | {stats.nodes} | {stats.edges} | {_human_bytes(total_bytes)} | [nodes]({version}/{files[0].name}), [edges]({version}/{files[1].name}), [RIG]({version}/{files[2].name}) |"
    release_rows = "\n".join([new_row, *old_rows])
    previous_stats = _latest_previous_stats(old_rows)
    deltas = (
        f"{stats.nodes - previous_stats[0]:+d} nodes / {stats.edges - previous_stats[1]:+d} edges vs {old_rows[0].split('|')[1].strip()}"
        if previous_stats
        else "no prior release row available for a numeric comparison"
    )
    change_bullets = (
        "\n".join(
            f"- **{change.source}** — {', '.join(change.dates) if change.dates else 'date not reported'}; "
            f"{change.current.artifact_count} acquired artifacts."
            for change in delta.changes
        )
        or "- No source artifacts changed; this card should not be published as a new release."
    )
    source_rows = "\n".join(
        f"| {name} | {source.artifact_count} | {', '.join(source.upstream_dates) or 'not reported'} | {', '.join(source.retrieved_dates) or 'not reported'} |"
        for name, source in sorted(delta.current.items())
    )
    file_rows = "\n".join(
        f"| `{file.name}` | {file.bytes} | {_human_bytes(file.bytes)} | `{file.md5}` |"
        for file in files
    )
    markdown = Template(_TEMPLATE_PATH.read_text(encoding="utf-8")).safe_substitute(
        front_matter=_front_matter(previous_card).rstrip(),
        version=version,
        date=(built_at or datetime.now(UTC)).date().isoformat(),
        delta_message=delta.message,
        change_bullets=change_bullets,
        graph_deltas=deltas,
        nodes=stats.nodes,
        edges=stats.edges,
        species=stats.species_context_edges,
        chemicals=stats.chemical_subjects,
        diseases=stats.disease_objects,
        study_backed=stats.study_backed_edges,
        category_rows=_counter_rows(stats.node_categories),
        predicate_rows=_counter_rows(stats.edge_predicates),
        namespace_rows=_counter_rows(stats.node_namespaces),
        source_rows=source_rows,
        file_rows=file_rows,
        total_bytes=total_bytes,
        human_bytes=_human_bytes(total_bytes),
        release_rows=release_rows,
        validation="PASS" if verification.ok else "FAIL",
        validator_output=verification.validate_kgx_stdout or "tablassert validate-kgx exited 0",
        tablassert_version=tablassert_version(),
        biolink_version=_biolink_version(rig_path),
        previous_version=delta.previous_version or "none recorded",
        nodes_file=files[0].name,
        edges_file=files[1].name,
        rig_file=files[2].name,
    )
    return CardResult(markdown=markdown, stats=stats, files=files)


__all__ = [
    "CardResult",
    "KGStats",
    "ReleaseFile",
    "build_dataset_card",
    "compute_kg_stats",
    "file_stat",
    "previous_release_rows",
    "stage_release_files",
    "tablassert_version",
]
