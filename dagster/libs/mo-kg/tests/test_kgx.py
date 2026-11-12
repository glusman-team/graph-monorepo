"""mo-kg tests: KGX ndjson roundtrip + QC."""

import json

from mo_kg.kgx import qc_counts, read_kgx

NODES = [
    {"id": "DRUG:1", "name": "d", "category": "biolink:Drug"},
    {"id": "DISEASE:1", "name": "x", "category": "biolink:Disease"},
]
EDGES = [
    {"subject": "DRUG:1", "predicate": "biolink:treats", "object": "DISEASE:1"},
]


def _write_ndjson(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_node_edge_roundtrip(tmp_path):
    nodes_path = tmp_path / "n.ndjson"
    edges_path = tmp_path / "e.ndjson"
    _write_ndjson(nodes_path, NODES)
    _write_ndjson(edges_path, EDGES)
    assert read_kgx(nodes_path) == NODES
    assert read_kgx(edges_path) == EDGES


def test_qc_counts_and_dangling(tmp_path):
    nodes_path = tmp_path / "n.ndjson"
    edges_path = tmp_path / "e.ndjson"
    _write_ndjson(nodes_path, NODES)
    _write_ndjson(
        edges_path, EDGES + [{"subject": "GHOST:1", "predicate": "p", "object": "DRUG:1"}]
    )
    counts = qc_counts(nodes_path, edges_path)
    assert counts == {"nodes": 2, "edges": 2, "dangling_edges": 1}
