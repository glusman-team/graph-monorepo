#!/usr/bin/env python3
"""Validate Linkouts deployment paths and preview isolation without Fly credentials."""

from __future__ import annotations

from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[3]
SITE = ROOT / "sites" / "linkouts"
CONTRACTS = ROOT / "dagster" / "projects" / "linkouts" / "contracts"


def load(name: str) -> dict:
    with (SITE / name).open("rb") as stream:
        return tomllib.load(stream)


def main() -> None:
    production = load("fly.toml")
    preview = load("fly.dev.toml")

    assert production["app"] == "edge-linkouts"
    assert preview["app"] == "edge-linkouts-dev"
    assert production["app"] != preview["app"]

    for config in (production, preview):
        dockerfile = config["build"]["dockerfile"]
        assert dockerfile == "sites/linkouts/Dockerfile"
        assert (ROOT / dockerfile).is_file(), dockerfile
        assert config["http_service"]["checks"][0]["path"] == "/healthz"

    assert "LINKOUTS_PREVIEW_FIXTURES" not in production["env"]
    assert "CLUSTER_QUERY" not in preview["env"]
    forbidden = ("COSMOS", "SECRET", "TOKEN", "KEY", "CLUSTER")
    assert all(not any(word in key for word in forbidden) for key in preview["env"])

    files = preview["files"]
    assert len(files) == 1
    mounted = files[0]
    assert mounted["guest_path"] == "/app/preview-docs.ndjson"
    assert preview["env"]["LINKOUTS_PREVIEW_FIXTURES"] == mounted["guest_path"]
    source = (SITE / mounted["local_path"]).resolve()
    assert source == (CONTRACTS / "docs.ndjson").resolve()
    assert source.is_file()

    dockerfile = (SITE / "Dockerfile").read_text()
    assert "COPY sites/linkouts ./sites/linkouts" in dockerfile
    assert "COPY web" not in dockerfile
    assert "COPY kgs" not in dockerfile
    assert "USER app" in dockerfile
    print("Deployment paths and credential-free preview configuration are valid.")


if __name__ == "__main__":
    main()
