"""Exercise issue 55 through real assertion tables, not only a classifier stub."""

from pathlib import Path

import polars as pl
import pytest

from dakp_pipeline.assertions.contraindications import build_contraindication_rows
from dakp_pipeline.assertions.side_effects import SideEffectsShaper
from dakp_pipeline.io import schemas
from dakp_pipeline.io.content_hash import hash_file
from dakp_pipeline.io.contracts import ArtifactRef, TaskContext
from dakp_pipeline.ner.ner import DiseaseNER

SET = "16e1cc9f-7699-49ea-b179-acb65e162304"
REACTIONS = (
    "Reactions have included anaphylaxis, angioedema, hypotension, tachycardia, "
    "swollen tongue, dyspnea, wheezing and rash."
)


def _inputs(tmp_path: Path, text: str, *, active_count: int = 1) -> list[ArtifactRef]:
    """Build the same section and active-ingredient contracts the extractor publishes."""
    frames = {
        "spl_sections": pl.DataFrame(
            {
                "spl_set_id": [SET],
                "spl_document_id": [f"{SET}#34070-3"],
                "loinc_code": ["34070-3"],
                "clean_text": [text],
            }
        ),
        "spl_ingredients": pl.DataFrame(
            {
                "role": ["active"] * active_count,
                "spl_set_id": [SET] * active_count,
                "ingredient_name": ["ASENAPINE", "OTHER"][:active_count],
                "ingredient_unii": ["UNII:JKW69P5YVP", "UNII:OTHER"][:active_count],
            }
        ),
    }
    refs = []
    for name, frame in frames.items():
        path = tmp_path / f"{name}.parquet"
        frame.write_parquet(path)
        refs.append(
            ArtifactRef(uri=path, blake3=hash_file(path), media_type="application/octet-stream")
        )
    return refs


def _shape(inputs: list[ArtifactRef], ctx: TaskContext) -> pl.DataFrame:
    """Inject deterministic NER; production and tests share all subsequent shaping."""
    ctx = ctx.model_copy(
        update={
            "params": {
                **ctx.params,
                "ner": DiseaseNER(
                    gazetteer={"swollen tongue": "phenotype", "hepatic impairment": "disease"}
                ),
            }
        }
    )

    refs = SideEffectsShaper().transform(inputs, ctx)
    assert len(refs) == 1
    assert refs[0].uri.name == "side_effect_assertions.tsv"
    return schemas.read_table(refs[0].uri)


def test_asenapine_reaction_and_contraindication_stay_separate(
    tmp_path: Path, ctx: TaskContext
) -> None:
    """The reported label retains hepatic impairment, not swollen tongue, as contraindicated."""
    text = (
        "Severe hepatic impairment. A history of hypersensitivity reactions to asenapine. "
        + REACTIONS
    )
    inputs = _inputs(tmp_path, text)
    frame = _shape(inputs, ctx)
    assert frame.columns == schemas.ASSERTION_TABLES["side_effect_assertions"]
    assert frame.height == 1
    row = frame.row(0, named=True)
    assert row["subject_text"] == "ASENAPINE"
    assert row["object_text"] == "swollen tongue"
    assert row["predicate"] == "biolink:has_side_effect"
    assert row["assertion_context"] == "side_effect"
    assert row["FDA_regulatory_approvals"] == ""
    assert row["edge_evidence"] == f"dailymed:{SET}"
    assert row["upstream_resource_ids"] == "infores:dailymed"
    assert row["species_context_text"] == "NCBITaxon:9606"
    assert row["evidence_text"] == REACTIONS
    assert _shape(inputs, ctx).equals(frame)
    contraindications = build_contraindication_rows(
        inputs,
        DiseaseNER(gazetteer={"swollen tongue": "phenotype", "hepatic impairment": "disease"}),
    )
    assert [r["object_text"] for r in contraindications] == ["hepatic impairment"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Swollen tongue.",
        "Monitor for swollen tongue.",
        "Not contraindicated in patients with swollen tongue.",
        "Contraindicated in patients with swollen tongue.",
        "Reactions have not included swollen tongue.",
        "Reactions included in Table 1 discuss swollen tongue.",
        "Reactions included in the boxed warning discuss swollen tongue.",
    ],
)
def test_unrelated_or_negated_prose_is_not_a_side_effect(
    tmp_path: Path, ctx: TaskContext, text: str
) -> None:
    """A rejected contraindication is not evidence that the drug causes the condition."""
    assert _shape(_inputs(tmp_path, text), ctx).is_empty()


def test_combination_product_reactions_are_not_attributed_to_each_ingredient(
    tmp_path: Path, ctx: TaskContext
) -> None:
    """A mixture's reaction cannot automatically be attributed to either component."""
    assert _shape(_inputs(tmp_path, REACTIONS, active_count=2), ctx).is_empty()


def test_no_inputs_produces_empty_contract(ctx: TaskContext) -> None:
    """Missing label evidence must not fabricate a side-effect assertion."""
    assert _shape([], ctx).is_empty()


def test_side_effect_config_has_label_evidence_not_regulatory_approval() -> None:
    """The new family must reach a valid Tablassert config without false approval metadata."""
    pytest.importorskip("tablassert")
    from tablassert.models import Section

    from dakp_pipeline import tablassert

    config = tablassert.table_config("side_effect_assertions")
    Section.model_validate(config)
    assert config["statement"]["predicate"] == "has_side_effect"
    fields = {item["annotation"] for item in config["annotations"]}
    assert "publications" in fields
    assert "regulatory_approvals" not in fields


def test_asenapine_side_effect_builds_valid_kgx(
    tmp_path: Path, ctx: TaskContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve the reported edge through a real tiny fullmap and the installed KG builder."""
    import json

    pytest.importorskip("tablassert")
    from tablassert import rs
    from tablassert.biolink import validate_record
    from tablassert.cli import build_pipeline
    from tablassert.progress import PipelineProgress

    from dakp_pipeline import tablassert

    frame = _shape(_inputs(tmp_path, REACTIONS), ctx)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".tablassert" / "store").mkdir(parents=True)
    synonyms = tmp_path / "synonyms.ndjson"
    entries = [
        ("CHEBI:71253", "asenapine", ["ASENAPINE", "asenapine"], "SmallMolecule"),
        ("UMLS:C0236068", "Tongue swelling", ["swollen tongue"], "PhenotypicFeature"),
        ("NCBITaxon:9606", "Homo sapiens", ["NCBITaxon:9606"], "OrganismTaxon"),
    ]
    synonyms.write_text(
        "".join(
            json.dumps(
                {
                    "curie": curie,
                    "preferred_name": name,
                    "names": names,
                    "types": [category],
                    "taxa": ["NCBITaxon:9606"],
                }
            )
            + "\n"
            for curie, name, names, category in entries
        )
    )
    fullmap = tmp_path / "fullmap.redb"
    rs.build_fullmap_db(fullmap, [], [synonyms])
    (tmp_path / "tabular").mkdir()
    frame.write_csv(tmp_path / "tabular" / "side_effect_assertions.tsv", separator="\t")
    (tmp_path / "tables").mkdir()
    (tmp_path / "tables" / "side_effects.yaml").write_text(
        tablassert.table_yaml("side_effect_assertions")
    )
    graph = tmp_path / "graph.yaml"
    graph.write_text(
        tablassert.graph_yaml(
            tables=["tables/side_effects.yaml"], version="issue55", fullmap=str(fullmap)
        )
    )
    build_pipeline(graph, PipelineProgress(total_stages=6))
    edges = [
        json.loads(line)
        for line in (tmp_path / "kgx" / "DRUG_APPROVALS_KP_issue55.edges.ndjson")
        .read_text()
        .splitlines()
    ]
    assert len(edges) == 1
    edge = edges[0]
    assert edge["subject"] == "CHEBI:71253"
    assert edge["object"] == "UMLS:C0236068"
    assert edge["predicate"] == "biolink:has_side_effect"
    assert edge["publications"] == [f"dailymed:{SET}"]
    assert not edge.get("regulatory_approvals")
    assert validate_record(edge, edge=True) == []
