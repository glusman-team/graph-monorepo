from __future__ import annotations

import unittest
from pathlib import Path

from check_no_dataclasses import _is_banned, findings_for, scan


class BannedImportTests(unittest.TestCase):
    """The ban must catch every spelling that pulls in a dataclass API."""

    def test_flags_stdlib_dataclass_decorator_import(self) -> None:
        source = "from dataclasses import dataclass, field\n"
        self.assertEqual(
            findings_for("example.py", source),
            ["example.py:1: from dataclasses import dataclass, field"],
        )

    def test_flags_pydantic_dataclasses_wrapper(self) -> None:
        """pydantic.dataclasses looks like the model API but validates nothing on assignment."""
        source = "from pydantic.dataclasses import dataclass\n"
        self.assertEqual(len(findings_for("example.py", source)), 1)

    def test_flags_plain_module_import_and_alias(self) -> None:
        source = "import dataclasses\nimport dataclasses as dc\n"
        self.assertEqual(
            findings_for("example.py", source),
            [
                "example.py:1: import dataclasses",
                "example.py:2: import dataclasses as dc",
            ],
        )

    def test_flags_nested_banned_module(self) -> None:
        self.assertTrue(_is_banned("dataclasses"))
        self.assertTrue(_is_banned("pydantic.dataclasses"))
        self.assertFalse(_is_banned("pydantic"))
        self.assertFalse(_is_banned("dataclasses_json"))

    def test_allows_pydantic_models_and_lookalike_text(self) -> None:
        """Only real imports count: prose, identifiers, and BaseModel stay legal."""
        source = (
            '"""The dataclass default no longer applies here."""\n'
            "from pydantic import BaseModel, ConfigDict, Field\n"
            "# dataclasses.are banned, so this comment must not trip the gate\n"
            "dataclass_free = 'dataclasses'\n"
            "class Record(BaseModel):\n"
            "    model_config = ConfigDict(extra='forbid', frozen=True)\n"
            "    name: str = Field(min_length=1)\n"
        )
        self.assertEqual(findings_for("example.py", source), [])

    def test_unparsable_source_fails_loudly(self) -> None:
        """A file the gate cannot read must never be reported as clean."""
        findings = findings_for("broken.py", "def f(:\n")
        self.assertEqual(len(findings), 1)
        self.assertIn("cannot parse Python source", findings[0])


class ScanTests(unittest.TestCase):
    def test_missing_tracked_file_fails_closed(self) -> None:
        """A broken checkout must not silently pass the gate."""
        findings = scan(Path("/nonexistent-root"), ["absent.py"])
        self.assertEqual(
            findings, ["absent.py: tracked file is missing from the working tree"]
        )


if __name__ == "__main__":
    unittest.main()
