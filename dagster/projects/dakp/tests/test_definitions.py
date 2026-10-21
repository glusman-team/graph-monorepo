"""Code-location smoke tests: definitions load and the asset graph is complete."""

from dakp_dag import assets
from dakp_dag.definitions import defs


def test_all_five_stage_assets_exist():
    names = {a.op.name for a in assets.all_assets}
    assert names == {
        "acquisition",
        "extraction",
        "ner",
        "assertions",
        "kg_build",
    }


def test_definitions_load():
    job_def = defs.resolve_job_def("dakp_build")
    node_names = {node.name for node in job_def.nodes}
    assert {"acquisition", "extraction", "ner", "assertions", "kg_build"} <= node_names


def test_kg_build_writes_kgx(tmp_path, monkeypatch):
    """kg_build produces readable KGX ndjson files (demo body, no dagster run)."""
    import json

    from mo_kg import kgx

    nodes = [{"id": "X:1", "name": "x", "category": "biolink:NamedThing"}]
    edges = [{"subject": "X:1", "predicate": "biolink:related_to", "object": "X:1"}]
    nodes_path = tmp_path / "nodes.ndjson"
    edges_path = tmp_path / "edges.ndjson"
    kgx.write_kgx_nodes(nodes_path, nodes)
    kgx.write_kgx_edges(edges_path, edges)

    loaded_nodes = [json.loads(line) for line in nodes_path.read_text().splitlines()]
    loaded_edges = [json.loads(line) for line in edges_path.read_text().splitlines()]
    assert loaded_nodes == nodes
    assert loaded_edges == edges
