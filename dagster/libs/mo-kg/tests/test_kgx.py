"""mo-kg tests: KGX ndjson roundtrip + QC."""

import json

from mo_kg.kgx import KGXNdjsonSink, qc_counts, read_kgx, write_kgx_edges, write_kgx_nodes

NODES = [
    {"id": "DRUG:1", "name": "d", "category": "biolink:Drug"},
    {"id": "DISEASE:1", "name": "x", "category": "biolink:Disease"},
]
EDGES = [
    {"subject": "DRUG:1", "predicate": "biolink:treats", "object": "DISEASE:1"},
]


def test_node_edge_roundtrip(tmp_path):
    nodes_path = tmp_path / "n.ndjson"
    edges_path = tmp_path / "e.ndjson"
    write_kgx_nodes(nodes_path, NODES)
    write_kgx_edges(edges_path, EDGES)
    assert read_kgx(nodes_path) == NODES
    assert read_kgx(edges_path) == EDGES


def test_qc_counts_and_dangling(tmp_path):
    nodes_path = tmp_path / "n.ndjson"
    edges_path = tmp_path / "e.ndjson"
    write_kgx_nodes(nodes_path, NODES)
    write_kgx_edges(
        edges_path, EDGES + [{"subject": "GHOST:1", "predicate": "p", "object": "DRUG:1"}]
    )
    counts = qc_counts(nodes_path, edges_path)
    assert counts == {"nodes": 2, "edges": 2, "dangling_edges": 1}


def test_sink_writes_default_names(tmp_path):
    sink = KGXNdjsonSink(tmp_path)
    assert sink.write_nodes(NODES).exists()
    assert sink.write_edges(EDGES).exists()
    line = json.loads((tmp_path / "kg.nodes.ndjson").read_text().splitlines()[0])
    assert line["id"] == "DRUG:1"
