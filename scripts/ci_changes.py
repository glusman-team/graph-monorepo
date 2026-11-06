#!/usr/bin/env python3
"""Map changed repository paths to scoped CI jobs, including contract fan-out."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path

OUTPUTS = (
    "dakp_python",
    "dakp_go",
    "linkouts_python",
    "linkouts_go",
    "linkouts_web",
    "docs",
    "deploy",
    "dagster",
)
SHARED_TOOLING = {
    ".envrc",
    ".envrc.example",
    ".golangci.yml",
    "Makefile",
    "flake.lock",
    "flake.nix",
    "nx.json",
    "package-lock.json",
    "package.json",
    "prek.toml",
}


def all_outputs(value: bool) -> dict[str, bool]:
    return {name: value for name in OUTPUTS}


def classify(paths: Iterable[str]) -> dict[str, bool]:
    result = all_outputs(False)
    for raw in paths:
        path = raw.strip()
        path = path.removeprefix("./")
        if not path:
            continue

        if path in SHARED_TOOLING or path.startswith(("nix/", "scripts/")):
            return all_outputs(True)
        if path == ".github/workflows/ci.yml":
            return all_outputs(True)

        if path.startswith("dagster/libs/"):
            for name in ("dakp_python", "dakp_go", "linkouts_python", "dagster"):
                result[name] = True
        if path.startswith(
            ("dagster/dg.toml", "dagster/workspace.yaml", "dagster/deployments/")
        ):
            for name in ("dakp_python", "linkouts_python", "dagster"):
                result[name] = True

        if path.startswith("dagster/projects/dakp/"):
            result["dagster"] = True
            if "/workers/go/" in path:
                result["dakp_python"] = True
                result["dakp_go"] = True
            else:
                result["dakp_python"] = True

        if path.startswith("dagster/projects/linkouts/workers/go/"):
            for name in ("linkouts_python", "linkouts_go", "linkouts_web", "dagster"):
                result[name] = True
        elif path.startswith("dagster/projects/linkouts/contracts/"):
            for name in (
                "linkouts_python",
                "linkouts_go",
                "linkouts_web",
                "deploy",
                "dagster",
            ):
                result[name] = True
        elif path.startswith("dagster/projects/linkouts/fixtures/kgx/"):
            for name in ("linkouts_python", "linkouts_go", "dagster"):
                result[name] = True
        elif path.startswith("dagster/projects/linkouts/"):
            result["linkouts_python"] = True
            result["dagster"] = True

        if path.startswith("sites/linkouts/"):
            for name in ("linkouts_go", "linkouts_web", "deploy"):
                result[name] = True

        if path.startswith("sites/docs/") or path == ".github/workflows/pages.yml":
            result["docs"] = True
        if path in {".dockerignore", ".github/workflows/deploy-linkouts.yml"}:
            result["deploy"] = True

    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--files", type=Path, required=True, help="Newline-delimited changed paths"
    )
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    paths = args.files.read_text(encoding="utf-8").splitlines()
    result = classify(paths)
    rendered = json.dumps(result, sort_keys=True)
    print(rendered)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for name, value in result.items():
                stream.write(f"{name}={str(value).lower()}\n")


if __name__ == "__main__":
    main()
