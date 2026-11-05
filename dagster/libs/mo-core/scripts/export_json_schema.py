#!/usr/bin/env python3
"""Export mo-core JSON Schemas for per-DAG Go codegen.

Usage: uv run python ../../scripts/export_json_schema.py <out-dir>
Called from a DAG's `go generate` hook; quicktype consumes the output.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mo_core.models import export_json_schema

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: export_json_schema.py <out-dir>", file=sys.stderr)
        raise SystemExit(2)
    for path in export_json_schema(sys.argv[1]):
        print(f"wrote {path}")
