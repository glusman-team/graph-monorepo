"""Generate the Hugging Face dataset card from real release outputs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from dakp_pipeline.release.delta import SourceDelta
from dakp_pipeline.release.verify import ReleaseVerification

_TEMPLATE_PATH = Path(__file__).with_name("card_template.md")
_RELEASE_HEADER = "| Version | Nodes | Edges | File size |"
_LEGACY_RELEASE_HEADER = "| Version | Tag | Nodes | Edges | Biolink |"
_HF_RESOLVE = "https://huggingface.co/datasets/SkyeAv/drug-approvals-kp/resolve"


class ReleaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    path: Path
    bytes: int
    md5: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.model_dump(), "path": str(self.path)}


class KGStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    nodes: int
    edges: int
    node_categories: dict[str, int]
    edge_predicates: dict[str, int]
    node_namespaces: dict[str, int]
    human_contraindication_edges: int
    chemical_subjects: int
    disease_objects: int
    publication_edges: int


class CardResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
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
    edges = human_contraindications = chemicals = diseases = publications = 0
    for edge in _rows(edges_path):
        edges += 1
        predicate = str(edge.get("predicate") or "unknown")
        predicates[predicate] += 1
        chemicals += str(edge.get("subject") or "").startswith("CHEBI:")
        diseases += str(edge.get("object") or "").startswith("MONDO:")
        is_human = edge.get("species_context_qualifier") == "NCBITaxon:9606"
        if not is_human:
            qualifiers = edge.get("qualifiers") or []
            is_human = any(
                isinstance(qualifier, dict)
                and qualifier.get("qualifier_type_id") == "biolink:species_context_qualifier"
                and qualifier.get("qualifier_value") == "NCBITaxon:9606"
                for qualifier in qualifiers
            )
        human_contraindications += predicate == "biolink:contraindicated_in" and is_human
        publications += bool(edge.get("publications"))
    return KGStats(
        nodes=nodes,
        edges=edges,
        node_categories=dict(sorted(categories.items())),
        edge_predicates=dict(sorted(predicates.items())),
        node_namespaces=dict(sorted(namespaces.items())),
        human_contraindication_edges=human_contraindications,
        chemical_subjects=chemicals,
        disease_objects=diseases,
        publication_edges=publications,
    )


def file_stat(path: Path) -> ReleaseFile:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    return ReleaseFile(name=path.name, path=path, bytes=path.stat().st_size, md5=digest.hexdigest())


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


def _front_matter(previous_card: str, version: str) -> str:
    front_matter = ""
    if previous_card.startswith("---\n"):
        end = previous_card.find("\n---\n", 4)
        if end >= 0:
            front_matter = previous_card[: end + 5]
    if not front_matter:
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
    if not re.search(r"(?m)^configs:", front_matter):
        return front_matter
    front_matter, config_names = re.subn(
        r"(?m)^([ \t]*-[ \t]+config_name:[ \t]*)\d+\.\d+\.\d+_(edges|nodes)([ \t]*)$",
        lambda match: f"{match[1]}{version}_{match[2]}{match[3]}",
        front_matter,
    )
    front_matter, data_files = re.subn(
        r"(?m)^([ \t]*-[ \t]+)\d+\.\d+\.\d+/DRUG_APPROVALS_KP_\d+\.\d+\.\d+\.(edges|nodes)\.ndjson([ \t]*)$",
        lambda match: (
            f"{match[1]}{version}/DRUG_APPROVALS_KP_{version}.{match[2]}.ndjson{match[3]}"
        ),
        front_matter,
    )
    if config_names != 2 or data_files != 2:
        raise ValueError(
            "dataset card front matter must contain exactly two versioned configs and data files"
        )
    return front_matter


def _release_row(version: str, nodes: int, edges: int, size: str) -> str:
    stem = f"DRUG_APPROVALS_KP_{version}"
    base = f"{_HF_RESOLVE}/v{version}/{version}"
    return (
        f"| v{version} | {nodes} | {edges} | {size} | "
        f"[nodes]({base}/{stem}.nodes.ndjson), [edges]({base}/{stem}.edges.ndjson), "
        f"[RIG]({base}/{stem}.RIG.yaml) |"
    )


def previous_release_rows(previous_card: str) -> list[str]:
    rows: list[str] = []
    table: str | None = None
    for line in previous_card.splitlines():
        if line.startswith(_RELEASE_HEADER):
            table = "current"
            continue
        if line.startswith(_LEGACY_RELEASE_HEADER):
            table = "legacy"
            continue
        if table is None:
            continue
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        if table == "current" and re.fullmatch(r"v\d+\.\d+\.\d+", cells[0]):
            rows.append(
                _release_row(
                    cells[0][1:],
                    int(cells[1].replace(",", "")),
                    int(cells[2].replace(",", "")),
                    cells[3],
                )
            )
        elif table == "legacy" and re.fullmatch(r"\d+\.\d+\.\d+", cells[0]):
            rows.append(
                _release_row(
                    cells[0],
                    int(cells[2].replace(",", "")),
                    int(cells[3].replace(",", "")),
                    "see tag",
                )
            )

    def version_key(row: str) -> tuple[int, ...]:
        version = row.split("|")[1].strip().removeprefix("v")
        return tuple(int(part) for part in version.split("."))

    unique = {row.split("|")[1].strip(): row for row in rows}
    return sorted(unique.values(), key=version_key, reverse=True)


def _latest_previous_stats(rows: list[str]) -> tuple[int, int] | None:
    if not rows:
        return None
    cells = [cell.strip() for cell in rows[0].strip().strip("|").split("|")]
    try:
        return int(cells[1].replace(",", "")), int(cells[2].replace(",", ""))
    except (IndexError, ValueError):
        return None


def _human_bytes(size: int) -> str:
    if size < 1 << 20:
        return f"{size / 1024:.1f} KB"
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
    new_row = _release_row(version, stats.nodes, stats.edges, _human_bytes(total_bytes))
    release_rows = "\n".join([new_row, *old_rows])
    previous_stats = _latest_previous_stats(old_rows)
    deltas = (
        f"{stats.nodes - previous_stats[0]:+d} nodes / {stats.edges - previous_stats[1]:+d} edges vs {old_rows[0].split('|')[1].strip()}"
        if previous_stats
        else "no prior release row available for a numeric comparison"
    )
    change_bullets = (
        "\n".join(
            f"- **{change.source}** - {', '.join(change.dates) if change.dates else 'date not reported'}; "
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
        front_matter=_front_matter(previous_card, version).rstrip(),
        version=version,
        date=(built_at or datetime.now(UTC)).date().isoformat(),
        delta_message=delta.message,
        change_bullets=change_bullets,
        graph_deltas=deltas,
        nodes=stats.nodes,
        edges=stats.edges,
        species=stats.human_contraindication_edges,
        chemicals=stats.chemical_subjects,
        diseases=stats.disease_objects,
        study_backed=stats.publication_edges,
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
        previous_version=(
            delta.previous_version
            or (old_rows[0].split("|")[1].strip().removeprefix("v") if old_rows else None)
            or "none recorded"
        ),
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
