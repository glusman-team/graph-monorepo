from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from check_dead_code import ACQUIRE_MODULE, DAKP_GO, go_findings, python_findings, run


class PythonFindingTests(unittest.TestCase):
    def test_ignores_variables_and_dynamic_acquisition(self) -> None:
        output = "\n".join(
            [
                "dagster/example.py:10: unused variable 'entity_type' (60% confidence)",
                f"{ACQUIRE_MODULE}:20: unused function 'acquire_ema_smpc' (60% confidence)",
            ]
        )
        self.assertEqual(python_findings(output), [])

    def test_retains_new_dead_function(self) -> None:
        line = "dagster/example.py:20: unused function 'orphaned' (60% confidence)"
        self.assertEqual(python_findings(line), [line])

    def test_dynamic_name_is_not_allowed_in_another_file(self) -> None:
        line = (
            "dagster/example.py:20: unused function 'acquire_ema_smpc' (60% confidence)"
        )
        self.assertEqual(python_findings(line), [line])

    def test_retains_dead_class_and_method(self) -> None:
        lines = [
            "dagster/example.py:20: unused class 'Unused' (60% confidence)",
            "dagster/example.py:30: unused method 'orphaned' (60% confidence)",
        ]
        self.assertEqual(python_findings("\n".join(lines)), lines)


class GoFindingTests(unittest.TestCase):
    def test_allows_only_documented_test_oracle(self) -> None:
        line = "internal/blake3store/blake3store.go:91:6: unreachable func: HashTree"
        self.assertEqual(go_findings(line, DAKP_GO), [])

    def test_rejects_same_name_in_another_file(self) -> None:
        line = "internal/other/orphan.go:91:6: unreachable func: HashTree"
        self.assertEqual(go_findings(line, DAKP_GO), [f"{DAKP_GO}: {line}"])

    def test_rejects_new_dead_function(self) -> None:
        line = "internal/worker/orphan.go:20:6: unreachable func: orphaned"
        self.assertEqual(go_findings(line, DAKP_GO), [f"{DAKP_GO}: {line}"])

    def test_unexpected_output_fails_closed(self) -> None:
        line = "deadcode produced an unparsable result"
        self.assertEqual(
            go_findings(line, DAKP_GO),
            [f"{DAKP_GO}: unrecognized deadcode output: {line}"],
        )


class CommandFailureTests(unittest.TestCase):
    def test_missing_executable_returns_clean_failure(self) -> None:
        with patch(
            "check_dead_code.subprocess.run", side_effect=FileNotFoundError("deadcode")
        ):
            result = run(["deadcode"], Path("/tmp"))
        self.assertEqual(result.returncode, 127)
        self.assertIn("deadcode", result.stderr)

    def test_timeout_returns_clean_failure(self) -> None:
        with patch(
            "check_dead_code.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["deadcode"], 600),
        ):
            result = run(["deadcode"], Path("/tmp"))
        self.assertEqual(result.returncode, 124)
        self.assertIn("600 seconds", result.stderr)


if __name__ == "__main__":
    unittest.main()
