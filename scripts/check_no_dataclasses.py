#!/usr/bin/env python3
"""Fail when repository Python imports dataclasses.

Every repository-owned record is a pydantic ``BaseModel``: one validation story, one
serialization story, and one immutability story instead of a second stdlib dialect that
skips validation. ``pydantic.dataclasses`` is banned for the same reason - it looks like
the model API but validates nothing on assignment and serializes differently.

The gate reads the syntax tree, not text, so comments, docstrings, and identifiers such
as ``dataclass_free`` never trigger it. Third-party packages keep their own internals;
only files tracked in this repository are scanned.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED_MODULES = frozenset({"dataclasses", "pydantic.dataclasses"})
DOCS_URL = "https://docs.pydantic.dev/latest/concepts/models/"


def tracked_python_files(root: Path) -> list[Path]:
    """Every Python file git tracks, so deleted and ignored paths never enter the scan."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [Path(name) for name in listing.split("\0") if name]


def banned_imports(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Yield (line, description) for each import that pulls in a dataclass API."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_banned(alias.name):
                    # alias.lineno points at the alias itself, so `a, b` on one line
                    # still reports each banned member separately.
                    alias_text = f" as {alias.asname}" if alias.asname else ""
                    yield (
                        getattr(alias, "lineno", node.lineno),
                        f"import {alias.name}{alias_text}",
                    )
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.level == 0
            and _is_banned(node.module)
        ):
            names = ", ".join(alias.name for alias in node.names)
            yield node.lineno, f"from {node.module} import {names}"


def _is_banned(module: str) -> bool:
    """True for the banned modules and anything nested under them."""
    return module in BANNED_MODULES or any(
        module.startswith(f"{banned}.") for banned in BANNED_MODULES
    )


def findings_for(path: Path, source: str) -> list[str]:
    """Return every dataclass import in one file as ``path:line: detail`` diagnostics."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        # Fail loudly: an unparsable file means the gate cannot vouch for it.
        return [f"{path}:{error.lineno}: cannot parse Python source ({error.msg})"]
    return [f"{path}:{line}: {detail}" for line, detail in banned_imports(tree)]


def scan(root: Path, files: Sequence[Path]) -> list[str]:
    """Scan every candidate file and collect diagnostics in repository order."""
    findings: list[str] = []
    for relative in files:
        absolute = root / relative
        if not absolute.is_file():
            # A tracked path missing from the working tree is a broken checkout, not a pass.
            findings.append(
                f"{relative}: tracked file is missing from the working tree"
            )
            continue
        findings.extend(findings_for(relative, absolute.read_text(encoding="utf-8")))
    return findings


def main() -> int:
    findings = scan(ROOT, tracked_python_files(ROOT))
    if not findings:
        return 0
    print(
        "dataclass imports are banned; use pydantic BaseModel instead:", file=sys.stderr
    )
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    print(f"  see {DOCS_URL}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
