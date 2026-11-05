"""Discovered ICD-10-CM release metadata in generated source/RIG configs."""

import json

from dakp_pipeline import tablassert as tablassert_configs


def test_discovered_icd10cm_release_flows_through_configs() -> None:
    release = "FY2099 April 1 update"
    url = "https://example.test/2099-update/icd10cm-table-and-index-April-1-2099.zip"
    graph = tablassert_configs.graph_config(icd10cm_release=release, icd10cm_url=url)
    serialized = json.dumps(graph)
    assert release in serialized
    assert url in serialized
    assert tablassert_configs.icd10cm_source.ICD10CM_RELEASE not in serialized
    assert tablassert_configs.icd10cm_source.ICD10CM_DRUG_TABLE_ZIP_URL not in serialized
    table_urls = tablassert_configs._table_source_urls("faers_applied_to_treat_assertions", url)
    assert url in table_urls
