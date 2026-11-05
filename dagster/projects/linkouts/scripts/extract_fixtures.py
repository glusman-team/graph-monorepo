#!/usr/bin/env python3
"""Extract the committed test fixtures from a local DAKP drug-approvals dump.

Dev-only tooling: run by `make fixtures`, never by CI, and it never reads a file inside
the repo. The source dump (100+ MB) stays on the developer's machine; only the handful of
records selected here are committed.

Inputs: --old-edges, --old-nodes, --new-edges, --new-nodes, --output.
Every path is explicit; this script never downloads data or assumes a home directory layout.

Outputs under the supplied --output directory:
  nodes.ndjson        every node referenced by a selected edge
  edges.ndjson        6 real v1.11.2 edges: smallest, median, largest, one with
                      has_supporting_studies, one contraindicated_in, one treats
  edges.v2.ndjson     the SAME ids, mutated -> exercises $t/$set/$add/$del and repack
  edges.drift.ndjson  3 real v1.16.0 edges -> exercises renamed/dropped KGX slots
                      (regulatory_approvals vs FDA_regulatory_approvals)
  edges.unresolvable.ndjson  2 synthetic edges whose subject/object are NOT in nodes.ndjson
                      -> the join must OMIT subject_name/object_name, never emit null
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def read_jsonl(path: Path):
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True))
            fh.write("\n")
            n += 1
    return n


def pick_old(path: Path) -> list[dict]:
    """Six edges chosen to cover every field shape in the real KG."""
    smallest = median = largest = None
    studies = contra = treats = None
    rows: list[tuple[int, dict]] = []
    for edge in read_jsonl(path):
        size = len(json.dumps(edge, separators=(",", ":")))
        rows.append((size, edge))
        if "has_supporting_studies" in edge and studies is None:
            studies = edge
        pred = edge.get("predicate")
        if pred == "biolink:contraindicated_in" and contra is None:
            contra = edge
        if pred == "biolink:treats" and treats is None:
            treats = edge
    rows.sort(key=lambda r: r[0])
    smallest = rows[0][1]
    largest = rows[-1][1]
    median = rows[len(rows) // 2][1]

    chosen: list[dict] = []
    seen: set[str] = set()
    for edge in (smallest, median, largest, studies, contra, treats):
        if edge is not None and edge["id"] not in seen:
            seen.add(edge["id"])
            chosen.append(edge)
    return chosen


def mutate(edges: list[dict]) -> list[dict]:
    """Same ids, changed content: one pure no-op, one $set, one $add, one $del."""
    # The flipped status must not contradict the predicate: real DAKP edges only ever put
    # "off_label_use" on biolink:applied_to_treat (treats is always approved_for_condition),
    # so every other predicate degrades to the enum's "not_provided" instead.
    out = []
    for i, edge in enumerate(edges):
        e = json.loads(json.dumps(edge))  # deep copy
        if i % 4 == 0:
            pass  # unchanged -> must encode as {"$t": "<base>"}
        elif i % 4 == 1:
            e["clinical_approval_status"] = (
                "off_label_use"
                if e.get("predicate") == "biolink:applied_to_treat"
                else "not_provided"
            )
            if isinstance(e.get("number_of_cases"), (int, float)):
                e["number_of_cases"] = int(e["number_of_cases"]) + 7
        elif i % 4 == 2:
            pubs = e.get("publications") or []
            e["publications"] = [*pubs, "PMID:40123456"]
            e["supporting_text"] = (e.get("supporting_text") or []) + ["fixture: added in v2"]
        else:
            e.pop("supporting_text", None)
            e.pop("number_of_cases", None)
        out.append(e)
    return out


def pick_new(path: Path, want: int = 3) -> list[dict]:
    """Real v1.16.0 edges, including one that uses the renamed `regulatory_approvals`."""
    renamed = None
    rest: list[dict] = []
    for edge in read_jsonl(path):
        if renamed is None and "regulatory_approvals" in edge:
            renamed = edge
            continue
        if len(rest) < want:
            rest.append(edge)
        if renamed is not None and len(rest) >= want:
            break
    return ([renamed] if renamed else []) + rest


def unresolvable() -> list[dict]:
    """Edges pointing at nodes that do not exist in the fixture node file.

    The join must leave subject_name/object_name absent. A null or "" here is the exact
    failure mode the whole pipeline is designed to avoid.
    """
    base = {
        "subject": "FIXTURE:missing-subject",
        "object": "FIXTURE:missing-object",
        "predicate": "biolink:related_to",
        "category": ["biolink:Association"],
        "knowledge_level": "not_provided",
        "agent_type": "manual_agent",
        "sources": [
            {"resource_id": "infores:drugapprovals-kp", "resource_role": "primary_knowledge_source"}
        ],
    }
    bare = dict(base, id="00000000-0000-3000-8000-000000000001")
    with_empty = dict(
        base, id="00000000-0000-3000-8000-000000000002", subject_name="", object_category=[]
    )
    return [bare, with_empty]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("old-edges", "old-nodes", "new-edges", "new-nodes", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    for path in (args.old_edges, args.old_nodes, args.new_edges, args.new_nodes):
        if not path.exists():
            print(f"missing source file: {path}", file=sys.stderr)
            print("supply the source files explicitly; no downloads are performed", file=sys.stderr)
            return 1

    old_edges = pick_old(args.old_edges)
    v2_edges = mutate(old_edges)
    drift_edges = pick_new(args.new_edges)
    orphan_edges = unresolvable()
    if not old_edges:
        print("no edges selected", file=sys.stderr)
        return 1

    wanted_nodes: set[str] = set()
    for edge in old_edges + v2_edges + drift_edges:
        for key in ("subject", "object"):
            value = edge.get(key)
            if isinstance(value, str):
                wanted_nodes.add(value)

    nodes: list[dict] = []
    seen_nodes: set[str] = set()
    for source in (args.old_nodes, args.new_nodes):
        for node in read_jsonl(source):
            nid = node.get("id")
            if nid in wanted_nodes and nid not in seen_nodes:
                seen_nodes.add(nid)
                nodes.append(node)

    counts = {
        "nodes.ndjson": write_jsonl(out / "nodes.ndjson", nodes),
        "edges.ndjson": write_jsonl(out / "edges.ndjson", old_edges),
        "edges.v2.ndjson": write_jsonl(out / "edges.v2.ndjson", v2_edges),
        "edges.drift.ndjson": write_jsonl(out / "edges.drift.ndjson", drift_edges),
        "edges.unresolvable.ndjson": write_jsonl(out / "edges.unresolvable.ndjson", orphan_edges),
    }

    # Guard the two properties the whole design rests on.
    ids = [e["id"] for e in old_edges]
    assert len(set(ids)) == len(ids), "fixture ids must be unique"
    assert [e["id"] for e in v2_edges] == ids, "v2 must reuse the v1 ids or deltas never fire"
    assert not (wanted_nodes & {"FIXTURE:missing-subject", "FIXTURE:missing-object"}), (
        "the unresolvable edges must stay unresolvable"
    )
    missing = wanted_nodes - seen_nodes
    total_bytes = sum((out / name).stat().st_size for name in counts)
    print("fixtures written to", out)
    for name, count in counts.items():
        print(f"  {name:22} {count:3} rows  {(out / name).stat().st_size:>8} bytes")
    print(f"  total {total_bytes} bytes; referenced nodes missing from source: {len(missing)}")
    if missing:
        print("  (missing is fine: it exercises the omit-when-absent join path)")
    for edge in old_edges[:1] + drift_edges[:1]:
        print("  sample id", edge["id"], "keys", len(edge))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
