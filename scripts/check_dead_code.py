"""Fail on unused Python functions/classes and unreachable Go production code."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAKP_GO = "dagster/projects/dakp/workers/go"
LINKOUTS_GO = "dagster/projects/linkouts/workers/go"

PYTHON_SOURCES = (
    "dagster/libs/mo-core/src",
    "dagster/libs/mo-core/scripts",
    "dagster/libs/mo-dagster/src",
    "dagster/libs/mo-kg/src",
    "dagster/projects/dakp/pipeline/src",
    "dagster/projects/dakp/src",
    "dagster/projects/linkouts/pipeline/src",
    "dagster/projects/linkouts/src",
    "scripts/check_dead_code.py",
    "scripts/ci_changes.py",
    "sites/linkouts/scripts/check_deploy.py",
)

# Ruff already rejects unused imports and locals. Vulture checks functions and classes here;
# only these exact Source handlers are exempt because dakp_dag/assets.py dispatches by name.
ACQUIRE_MODULE = "dagster/projects/dakp/pipeline/src/dakp_pipeline/acquire.py"
PYTHON_DYNAMIC_ENTRYPOINTS = frozenset(
    {
        (ACQUIRE_MODULE, "acquire_canada_vigilance"),
        (ACQUIRE_MODULE, "acquire_ema_smpc"),
        (ACQUIRE_MODULE, "acquire_icd10cm"),
    }
)

# deadcode analyzes the production graph, not tests. These functions are deliberate
# test-side reference implementations used by streaming, BLAKE3, or wire-contract parity tests.
# Keep each exception tied to its file; do not add convenience wrappers merely to silence a finding.
GO_TEST_ORACLES = {
    DAKP_GO: frozenset(
        {
            ("internal/blake3store/blake3store.go", "HashTree"),
            ("internal/blake3store/blake3store.go", "hashTreeChunked"),
            ("internal/blake3store/blake3store.go", "listTreeFiles"),
            ("internal/blake3store/blake3store.go", "streamFile"),
            ("internal/blake3store/blake3store.go", "SHA256SRI"),
            ("internal/blake3store/tree_warm_linux.go", "warmTreeFile"),
            ("internal/dailymed/dailymed.go", "RenderTSV"),
            ("internal/dailymed/dailymed.go", "Tables.WriteDir"),
            ("internal/faers/faers.go", "ParseSource"),
            ("internal/faers/faers.go", "ReduceCases"),
            ("internal/faers/faers.go", "Extract"),
            ("internal/faers/faers.go", "ParseSourcesConcurrent"),
            ("internal/faers/faers.go", "ExtractParsed"),
            ("internal/faers/faers.go", "WriteCasesTSV"),
            ("internal/faers/faers.go", "WriteDeleteAuditTSV"),
            ("internal/faers/faers.go", "WriteDedupAuditTSV"),
            ("internal/faers/faers.go", "Case.tsvRow"),
            ("internal/faers/faers.go", "Deduper.Len"),
            ("internal/worker/extract_faers.go", "loadFAERSSources"),
            ("internal/worker/extract_faers.go", "faersSourceFromLoose"),
            ("internal/worker/extract_faers.go", "faersSourcesFromZip"),
        }
    ),
    LINKOUTS_GO: frozenset({("internal/codec/canonical.go", "Parse")}),
}

PYTHON_FINDING = re.compile(
    r"^(?P<path>.+):\d+: unused (?:function|class|method|property) '(?P<name>[^']+)'.*$"
)
GO_FINDING = re.compile(r"^(?P<path>.+):\d+:\d+: unreachable func: (?P<name>\S+)$")


def python_findings(output: str) -> list[str]:
    """Return new Python function/class findings, ignoring variables and dynamic entrypoints."""
    findings = []
    for line in output.splitlines():
        match = PYTHON_FINDING.match(line)
        if (
            match
            and (match.group("path"), match.group("name"))
            not in PYTHON_DYNAMIC_ENTRYPOINTS
        ):
            findings.append(line)
    return findings


def go_findings(output: str, module: str) -> list[str]:
    """Return unapproved deadcode findings; malformed output is an error, never ignored."""
    findings = []
    for line in output.splitlines():
        if not line.strip():
            continue
        match = GO_FINDING.match(line)
        if not match:
            findings.append(f"{module}: unrecognized deadcode output: {line}")
            continue
        key = (match.group("path"), match.group("name"))
        if key not in GO_TEST_ORACLES[module]:
            findings.append(f"{module}: {line}")
    return findings


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, check=False, timeout=600
        )
    except subprocess.TimeoutExpired as exc:
        detail = f"command timed out after {exc.timeout} seconds"
        return subprocess.CompletedProcess(command, 124, "", detail)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def main() -> int:
    failures: list[str] = []
    python = run(
        [
            "vulture",
            "--ignore-decorators",
            "@field_validator,@model_validator",
            "--min-confidence",
            "60",
            *PYTHON_SOURCES,
        ],
        ROOT,
    )
    # Vulture 2.16 returns 3 for findings, 1 for invalid input, and 2 for bad arguments.
    if python.returncode not in {0, 3}:
        detail = "\n".join(
            part.strip() for part in (python.stdout, python.stderr) if part.strip()
        )
        failures.append(f"vulture exited {python.returncode}: {detail}")
    else:
        failures.extend(python_findings(python.stdout))

    for module in GO_TEST_ORACLES:
        go = run(["deadcode", "./..."], ROOT / module)
        if go.returncode:
            detail = "\n".join(
                part.strip() for part in (go.stdout, go.stderr) if part.strip()
            )
            failures.append(f"deadcode {module} exited {go.returncode}: {detail}")
        else:
            failures.extend(go_findings(go.stdout, module))

    if failures:
        print("Dead-code check failed:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("Dead-code check passed for Python and both Go modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
