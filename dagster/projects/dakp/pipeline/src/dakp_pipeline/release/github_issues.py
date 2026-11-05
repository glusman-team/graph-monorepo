"""Create or update DAKP build-failure issues through GitHub's REST API."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from typing import Any


def _find_template() -> Path:
    """Locate the issue template via DAKP_REPO_ROOT, else by searching upward."""
    import os

    relative = Path(".github") / "ISSUE_TEMPLATE" / "dakp_build_failed.md"
    root = os.environ.get("DAKP_REPO_ROOT")
    candidates = [Path(root)] if root else []
    candidates.extend(Path(__file__).resolve().parents)
    for base in candidates:
        if (base / relative).is_file():
            return base / relative
    return Path(root or ".") / relative


DEFAULT_TEMPLATE = _find_template()
ISSUE_LABEL = "dakp-build-failure"
MAX_ISSUE_BODY = 60_000


@dataclass(frozen=True)
class StepStat:
    name: str
    status: str
    started_at: str = ""
    ended_at: str = ""
    duration_seconds: float | None = None
    error: str = ""
    log_tail: str = ""


@dataclass(frozen=True)
class FailureEvent:
    job_name: str
    run_id: str
    run_url: str
    git_sha: str
    version: str
    delta_message: str
    gpu_summary: str
    steps: tuple[StepStat, ...]
    fingerprint_key: str | None = None

    @property
    def failed_steps(self) -> tuple[StepStat, ...]:
        return tuple(step for step in self.steps if step.status == "FAILURE") or self.steps[-1:]

    @property
    def fingerprint(self) -> str:
        if self.fingerprint_key:
            raw = self.fingerprint_key
        else:
            first_error = next(
                (step.error for step in self.failed_steps if step.error), "run failure"
            )
            raw = f"{self.job_name}|{','.join(step.name for step in self.failed_steps)}|{first_error.splitlines()[0]}"
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def sanitize_log(text: str) -> str:
    text = re.sub(r"\bhf_[A-Za-z0-9]{16,}\b", "hf_***", text)
    text = re.sub(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b", "gh***", text)
    text = re.sub(r"(?i)(Bearer\s+)[^\s,]+", r"\1***", text)
    return re.sub(r"(?i)\b(HF_TOKEN|GITHUB_TOKEN)=([^\s]+)", r"\1=***", text)


def truncate_log(text: str, limit: int = 12_000) -> str:
    clean = sanitize_log(text)
    if len(clean) <= limit:
        return clean
    omitted = len(clean) - limit
    return f"[... truncated {omitted} characters ...]\n" + clean[-limit:]


def _step_rows(steps: tuple[StepStat, ...]) -> str:
    return (
        "\n".join(
            f"| `{step.name}` | {step.status} | {step.started_at} | {step.ended_at} | "
            f"{step.duration_seconds:.1f} |"
            if step.duration_seconds is not None
            else f"| `{step.name}` | {step.status} | {step.started_at} | {step.ended_at} | |"
            for step in steps
        )
        or "| run | FAILURE | | | |"
    )


def render_failure_issue(
    event: FailureEvent, template_path: Path = DEFAULT_TEMPLATE
) -> tuple[str, str]:
    template = template_path.read_text(encoding="utf-8")
    if template.startswith("---\n"):
        end = template.find("\n---\n", 4)
        if end >= 0:
            template = template[end + 5 :]
    failed = event.failed_steps
    failed_names = ", ".join(step.name for step in failed) or "run"
    errors = (
        "\n\n".join(
            f"### `{step.name}`\n{truncate_log(step.error, 4000)}" for step in failed if step.error
        )
        or "The Dagster run failed without a step-level error event."
    )
    logs = (
        "\n\n".join(
            f"### `{step.name}` log tail\n```text\n{truncate_log(step.log_tail)}\n```"
            for step in failed
            if step.log_tail
        )
        or "No step log tail was available; use the Dagster run link and host compute logs."
    )
    title = f"[DAKP build failed] {event.job_name}: {failed_names}"[:250]
    body = Template(template).safe_substitute(
        job_name=event.job_name,
        run_id=event.run_id,
        run_url=event.run_url,
        git_sha=event.git_sha or "unknown",
        version=event.version or "not assigned",
        failed_steps=failed_names,
        error_summary=errors,
        step_stats=_step_rows(event.steps),
        delta_message=event.delta_message or "unavailable",
        gpu_summary=truncate_log(event.gpu_summary, 2000) or "unavailable",
        log_tail=logs,
        timestamp=datetime.now(UTC).isoformat(),
        fingerprint=event.fingerprint,
    )
    if len(body) > MAX_ISSUE_BODY:
        body = body[:MAX_ISSUE_BODY] + "\n\n[issue body truncated to 60000 characters]\n"
    return title, body


class GitHubIssueClient:
    def __init__(
        self,
        repo: str,
        token: str | None,
        *,
        transport: Callable[[str, str, dict[str, Any] | None], Any] | None = None,
    ):
        if not token:
            raise RuntimeError("GITHUB_TOKEN is required to file a DAKP build-failure issue")
        self.repo = repo
        self.token = token
        self.transport = transport or self._request

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"https://api.github.com{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "dakp-release-automation",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise RuntimeError(f"GitHub API {method} {path} -> {exc.code}: {detail}") from exc

    def create_or_comment(self, event: FailureEvent) -> dict[str, Any]:
        title, body = render_failure_issue(event)
        marker = f"dakp-fingerprint: {event.fingerprint}"
        existing = None
        for page in range(1, 11):
            query = urllib.parse.urlencode(
                {"state": "open", "labels": ISSUE_LABEL, "per_page": 100, "page": page}
            )
            issues = self.transport("GET", f"/repos/{self.repo}/issues?{query}", None)
            existing = next(
                (issue for issue in issues if marker in (issue.get("body") or "")), None
            )
            if existing is not None or len(issues) < 100:
                break
        if existing:
            comment = self.transport(
                "POST",
                f"/repos/{self.repo}/issues/{existing['number']}/comments",
                {"body": body},
            )
            return {
                "action": "commented",
                "issue": existing["number"],
                "comment": comment.get("id"),
            }
        try:
            self.transport(
                "POST",
                f"/repos/{self.repo}/labels",
                {
                    "name": ISSUE_LABEL,
                    "color": "d73a4a",
                    "description": "Automated DAKP release failure",
                },
            )
        except RuntimeError as exc:
            if "422" not in str(exc):
                raise
        issue = self.transport(
            "POST",
            f"/repos/{self.repo}/issues",
            {"title": title, "body": body, "labels": [ISSUE_LABEL]},
        )
        return {"action": "created", "issue": issue["number"], "url": issue.get("html_url")}


def report_gpu_timeout(
    *,
    repo: str,
    token: str | None,
    check_run_id: str,
    deadline: str,
    reason: str,
    dagster_url: str,
    client: GitHubIssueClient | None = None,
) -> dict[str, Any]:
    event = FailureEvent(
        job_name="dakp_gpu_gate",
        run_id=check_run_id,
        run_url=f"{dagster_url}/runs/{check_run_id}",
        git_sha="unknown",
        version="not assigned (GPU gate timeout)",
        delta_message="A changed source delta was ready to build.",
        gpu_summary=reason,
        steps=(
            StepStat(
                name="gpu_gate",
                status="FAILURE",
                ended_at=deadline,
                error=reason,
                log_tail=reason,
            ),
        ),
        fingerprint_key=f"gpu-timeout|{check_run_id}",
    )
    return (client or GitHubIssueClient(repo, token)).create_or_comment(event)


__all__ = [
    "FailureEvent",
    "GitHubIssueClient",
    "StepStat",
    "render_failure_issue",
    "report_gpu_timeout",
    "sanitize_log",
    "truncate_log",
]
