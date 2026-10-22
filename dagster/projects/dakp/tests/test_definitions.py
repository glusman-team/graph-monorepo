"""Code-location structure: the asset graph, its published outputs, and its jobs.

These tests pin the DAG-level contract that the data-plane tests cannot: the stage set is
derived from the source table (a dropped source shows up here), every stage publishes
exactly one declared artifact set, deps are lineage-only (never value inputs, which would
force the producer into the same run), and every stage has a narrow single-output job.
"""

import inspect

from dakp_dag import assets
from dakp_dag.definitions import defs

EXPLICIT_STAGES = [
    *assets.SHAPE_STAGES,
    assets.NER_TRAINING_EXPORT,
    assets.TABLASSERT_CONFIGS,
    assets.KG_BUILD,
    assets.KG_QC,
    assets.LEGACY_TSV_EXPORT,
]

EXPECTED_STAGES = [
    *[assets.acquire_stage(source.key) for source in assets.SOURCES],
    assets.ACQUIRE_NER_MODELS,
    *[assets.extract_stage(source.key) for source in assets.SOURCES],
    *EXPLICIT_STAGES,
]


def asset_names() -> set[str]:
    return {asset.key.to_user_string() for asset in assets.all_assets}


def test_every_stage_is_an_asset():
    assert asset_names() == set(EXPECTED_STAGES)
    assert len(assets.all_assets) == len(EXPECTED_STAGES), "duplicate asset in all_assets"


def test_stage_names_follow_the_source_table():
    """A new source in SOURCES gets its acquire+extract stage without touching this file."""
    for source in assets.SOURCES:
        assert assets.acquire_stage(source.key) in asset_names()
        assert assets.extract_stage(source.key) in asset_names()
        assert source.key in assets.SOURCE_BY_KEY
        assert source.extractor.split(":", 1)[0] in {"go", "py"}


def test_definitions_load():
    assert defs.get_repository_def() is not None


def test_full_job_covers_every_stage():
    job = defs.resolve_job_def("dakp_build")
    assert {node.name for node in job.nodes} == set(EXPECTED_STAGES)


def test_every_stage_has_a_narrow_job_selecting_only_itself():
    names = {job.name for job in assets.all_jobs}
    assert names == {"dakp_build"} | {f"dakp_build_{stage}" for stage in EXPECTED_STAGES}
    for stage in EXPECTED_STAGES:
        job = defs.resolve_job_def(f"dakp_build_{stage}")
        assert {node.name for node in job.nodes} == {stage}
        assert job.tags["dakp/stage"] == stage


def test_declared_outputs_cover_every_stage_exactly_once():
    assert set(assets.stage_outputs) == set(EXPECTED_STAGES)
    for stage, outputs in assets.stage_outputs.items():
        assert outputs, f"{stage} declares no published artifact"
        assert len(set(outputs)) == len(outputs), f"{stage} declares a duplicate artifact"


def test_deps_are_lineage_only_never_value_inputs():
    """A value input would force the producer into the same run, breaking narrow jobs.

    The discriminator is the compute function's signature: a lineage dep shows up in
    ``specs[].deps`` only, while a value input also becomes a function parameter.
    """
    for asset in assets.all_assets:
        stage = asset.key.to_user_string()
        params = list(inspect.signature(asset.node_def.compute_fn.decorated_fn).parameters)
        assert params == ["context"], f"{stage} consumes value inputs: {params}"
        for spec in asset.specs:
            for dep in spec.deps:
                assert dep.asset_key.to_user_string() in set(EXPECTED_STAGES), (
                    f"{stage} depends on unknown asset {dep.asset_key.to_user_string()}"
                )


def test_extraction_deps_on_its_own_acquisition_only():
    for source in assets.SOURCES:
        extract = assets.EXTRACT_ASSETS[source.key]
        deps = {dep.asset_key.to_user_string() for spec in extract.specs for dep in spec.deps}
        assert deps == {assets.acquire_stage(source.key)}


def test_shaping_deps_match_the_dakp_dag():
    """The wiring ported from dags/dakp_build.py: which extracts feed which shaper."""
    expected = {
        "shape_approved_treats": {
            "extract_dailymed",
            "extract_drugsfda",
            "extract_faers",
            "extract_ema",
            "extract_ema_smpc",
            "acquire_ner_models",
        },
        "shape_faers_uses": {
            "extract_faers",
            "extract_dailymed",
            "extract_drugsfda",
            "extract_canada_vigilance",
            "shape_approved_treats",
        },
        "shape_contraindications": {
            "extract_dailymed",
            "extract_drugsfda",
            "extract_ema_smpc",
            "extract_ema",
            "acquire_ner_models",
        },
        assets.NER_TRAINING_EXPORT: {
            "extract_dailymed",
            "extract_faers",
            "extract_ema",
            "acquire_ner_models",
            "shape_contraindications",
        },
        assets.TABLASSERT_CONFIGS: set(assets.SHAPE_STAGES),
        assets.KG_BUILD: {*assets.SHAPE_STAGES, assets.TABLASSERT_CONFIGS},
        assets.KG_QC: {assets.KG_BUILD},
        assets.LEGACY_TSV_EXPORT: {assets.KG_BUILD},
    }
    by_name = {asset.key.to_user_string(): asset for asset in assets.all_assets}
    for stage, deps in expected.items():
        actual = {
            dep.asset_key.to_user_string() for spec in by_name[stage].specs for dep in spec.deps
        }
        assert actual == deps, f"{stage} deps drifted: {actual ^ deps}"


def test_acquisition_stages_have_no_deps():
    for source in assets.SOURCES:
        acquire = assets.ACQUIRE_ASSETS[source.key]
        assert not [dep for spec in acquire.specs for dep in spec.deps]
    assert not [dep for spec in assets.acquire_ner_models.specs for dep in spec.deps]
