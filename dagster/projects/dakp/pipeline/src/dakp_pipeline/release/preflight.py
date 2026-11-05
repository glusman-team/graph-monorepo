"""Fail-fast input and release-environment validation (no Dagster imports)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PreflightReport:
    checks: dict[str, str] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    git_sha: str | None = None
    hf_user: str | None = None


class PreflightError(RuntimeError):
    def __init__(self, report: PreflightReport):
        self.report = report
        super().__init__("DAKP release preflight failed:\n- " + "\n- ".join(report.failures))


def _github_repo_check(repo: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "dakp-release-automation",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _git(runner: Callable[..., Any], repo_root: Path, *args: str) -> Any:
    return runner(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def run_preflight(
    config: Any,
    *,
    repo_root: Path,
    hf_repo_id: str,
    github_repo: str,
    hf_token: str | None,
    github_token: str | None,
    min_free_gb: float = 100.0,
    runner: Callable[..., Any] = subprocess.run,
    hf_api: Any | None = None,
    github_check: Callable[[str, str], dict[str, Any]] = _github_repo_check,
    nercache_health: Callable[[], dict[str, Any]] | None = None,
) -> PreflightReport:
    """Validate all release inputs, aggregating failures into one actionable error."""
    report = PreflightReport()

    def check(name: str, ok: bool, detail: str, failure: str | None = None) -> None:
        report.checks[name] = detail
        if not ok:
            report.failures.append(failure or f"{name}: {detail}")

    fullmap = Path(config.fullmap) if config.fullmap else None
    check(
        "fullmap",
        bool(
            fullmap
            and fullmap.is_file()
            and fullmap.stat().st_size > 0
            and os.access(fullmap, os.R_OK)
        ),
        str(fullmap) if fullmap else "DAKP_FULLMAP is unset",
        "DAKP_FULLMAP must point to a non-empty readable fullmap database",
    )
    worker_bin = Path(config.worker_bin)
    check(
        "extract_worker",
        worker_bin.is_file() and os.access(worker_bin, os.X_OK),
        str(worker_bin),
        f"extraction worker is missing or not executable: {worker_bin}",
    )
    fixture_root = Path(config.fixture_root) if config.fixture_root else None
    check(
        "fixture_root",
        bool(fixture_root and fixture_root.is_dir()),
        str(fixture_root) if fixture_root else "unset",
        "DAKP_FIXTURE_ROOT must contain the production disease map and NER gazetteer",
    )
    check(
        "ner_online",
        not config.ner_offline,
        f"DAKP_NER_OFFLINE={config.ner_offline}",
        "release builds cannot run with DAKP_NER_OFFLINE",
    )
    check(
        "unbounded_faers",
        not config.quarter_limit,
        str(config.quarter_limit),
        "DAKP_QUARTER_LIMIT must be unset for a release",
    )
    check(
        "unbounded_dailymed",
        not config.release_limit,
        str(config.release_limit),
        "DAKP_RELEASE_LIMIT must be unset for a release",
    )
    check(
        "no_global_force",
        not bool(getattr(config, "force", False)),
        f"DAKP_FORCE={getattr(config, 'force', False)}",
        "scheduled releases cannot run with DAKP_FORCE=1",
    )
    source_refresh = str(config.params().get("source_refresh", "auto"))
    check(
        "source_refresh",
        source_refresh == "auto",
        source_refresh,
        "scheduled releases require DAKP_SOURCE_REFRESH=auto",
    )

    for name, raw_path in (("workdir", config.workdir), ("published_root", config.published_root)):
        path = Path(raw_path)
        try:
            path.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(path)
            free_gb = usage.free / (1 << 30)
            check(
                name,
                os.access(path, os.W_OK) and free_gb >= min_free_gb,
                f"{path}: {free_gb:.1f} GiB free",
                f"{name} needs {min_free_gb:.1f} GiB free and write access ({path}, {free_gb:.1f} GiB free)",
            )
        except OSError as exc:
            check(name, False, str(exc), f"{name} is not usable: {exc}")

    for label, repo in (("hf_repo_id", hf_repo_id), ("github_repo", github_repo)):
        check(
            label,
            repo.count("/") == 1 and all(repo.split("/")),
            repo,
            f"{label} must be owner/name, got {repo!r}",
        )

    if hf_token:
        try:
            api = hf_api
            if api is None:
                from huggingface_hub import HfApi

                api = HfApi(token=hf_token)
            identity = api.whoami()
            report.hf_user = identity.get("name") or identity.get("user")
            api.dataset_info(hf_repo_id)
            check("hf_access", True, f"{hf_repo_id} as {report.hf_user}")
        except Exception as exc:  # API exceptions differ by hub version
            check(
                "hf_access",
                False,
                type(exc).__name__,
                f"HF token/repo check failed for {hf_repo_id}: {exc}",
            )
    else:
        check(
            "hf_access", False, "HF_TOKEN is unset", "HF_TOKEN is required for release publication"
        )

    if github_token:
        try:
            payload = github_check(github_repo, github_token)
            check(
                "github_access",
                bool(payload.get("id")),
                github_repo,
                f"GitHub repo check returned no id for {github_repo}",
            )
        except Exception as exc:
            check(
                "github_access",
                False,
                type(exc).__name__,
                f"GitHub token/repo check failed for {github_repo}: {exc}",
            )
    else:
        check(
            "github_access",
            False,
            "GITHUB_TOKEN is unset",
            "GITHUB_TOKEN is required for build-failure issues",
        )

    try:
        fetch = _git(runner, repo_root, "fetch", "origin", "main")
        if fetch.returncode:
            raise RuntimeError(fetch.stderr.strip() or "git fetch failed")
        head = _git(runner, repo_root, "rev-parse", "HEAD")
        remote = _git(runner, repo_root, "rev-parse", "origin/main")
        report.git_sha = head.stdout.strip()
        check(
            "tracks_main",
            head.returncode == 0
            and remote.returncode == 0
            and head.stdout.strip() == remote.stdout.strip(),
            f"HEAD={head.stdout.strip()} origin/main={remote.stdout.strip()}",
            f"checkout must track origin/main exactly (HEAD={head.stdout.strip()}, origin/main={remote.stdout.strip()}); fast-forward and reload the code location",
        )
        tracked_diff = _git(runner, repo_root, "diff", "--quiet", "HEAD", "--")
        check(
            "clean_tracked_tree",
            tracked_diff.returncode == 0,
            f"git diff exit {tracked_diff.returncode}",
            "tracked source files differ from HEAD; release from a clean main checkout",
        )
    except Exception as exc:
        check("git", False, type(exc).__name__, f"git origin/main validation failed: {exc}")

    if nercache_health is not None:
        try:
            health = nercache_health()
            check(
                "nercache",
                bool(health.get("ok", True)),
                json.dumps(health, default=str)[:500],
                "managed NER cache is unhealthy",
            )
        except Exception as exc:
            check(
                "nercache",
                False,
                type(exc).__name__,
                f"managed NER cache health check failed: {exc}",
            )

    if report.failures:
        raise PreflightError(report)
    return report


__all__ = ["PreflightError", "PreflightReport", "run_preflight"]
