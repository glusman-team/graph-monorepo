#!/usr/bin/env python3
"""Unit tests for scoped CI and shared-contract fan-out."""

from __future__ import annotations

import unittest

from ci_changes import OUTPUTS, all_outputs, classify


class ChangesTest(unittest.TestCase):
    def assert_only(self, paths: list[str], *expected: str) -> None:
        result = classify(paths)
        for name in OUTPUTS:
            self.assertEqual(result[name], name in expected, f"{name} for {paths}")

    def test_dakp_python_is_scoped(self) -> None:
        self.assert_only(
            ["dagster/projects/dakp/src/dakp_dag/assets.py"], "dakp_python", "dagster"
        )

    def test_dakp_go_fans_out_to_python_integration(self) -> None:
        self.assert_only(
            ["dagster/projects/dakp/workers/go/internal/worker/store.go"],
            "dakp_python",
            "dakp_go",
            "dagster",
        )

    def test_linkouts_go_fans_out_to_reader_and_integration(self) -> None:
        self.assert_only(
            ["dagster/projects/linkouts/workers/go/internal/codec/blob.go"],
            "linkouts_python",
            "linkouts_go",
            "linkouts_web",
            "dagster",
        )

    def test_shared_contract_runs_writer_reader_and_deploy(self) -> None:
        self.assert_only(
            ["dagster/projects/linkouts/contracts/docs.ndjson"],
            "linkouts_python",
            "linkouts_go",
            "linkouts_web",
            "deploy",
            "dagster",
        )

    def test_kgx_inputs_run_writer_and_integration_only(self) -> None:
        self.assert_only(
            ["dagster/projects/linkouts/fixtures/kgx/edges.ndjson"],
            "linkouts_python",
            "linkouts_go",
            "dagster",
        )

    def test_phoenix_change_runs_go_contract_and_deploy(self) -> None:
        self.assert_only(
            ["sites/linkouts/lib/edge_linkouts/codec.ex"],
            "linkouts_go",
            "linkouts_web",
            "deploy",
        )

    def test_docs_are_scoped(self) -> None:
        self.assert_only(["sites/docs/src/content/docs/index.mdx"], "docs")

    def test_shared_libraries_fan_out_to_dependents(self) -> None:
        self.assert_only(
            ["dagster/libs/mo-dagster/src/mo_dagster/pipes.py"],
            "dakp_python",
            "dakp_go",
            "linkouts_python",
            "dagster",
        )

    def test_workspace_config_loads_both_code_locations(self) -> None:
        self.assert_only(
            ["dagster/workspace.yaml"], "dakp_python", "linkouts_python", "dagster"
        )

    def test_shared_tooling_runs_everything(self) -> None:
        self.assertEqual(classify(["flake.lock"]), all_outputs(True))
        self.assertEqual(classify([".github/workflows/ci.yml"]), all_outputs(True))

    def test_unrelated_documentation_does_not_run_components(self) -> None:
        self.assertEqual(
            classify(["README.md", "plans/port-linkouts.md"]), all_outputs(False)
        )

    def test_empty_change_set_is_explicit(self) -> None:
        self.assertEqual(classify([]), all_outputs(False))


if __name__ == "__main__":
    unittest.main()
