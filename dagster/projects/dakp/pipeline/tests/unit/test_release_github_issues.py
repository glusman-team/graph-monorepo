"""Failure issue rendering, truncation, sanitization, and dedup."""

from __future__ import annotations

from dakp_pipeline.release.github_issues import (
    FailureEvent,
    GitHubIssueClient,
    StepStat,
    render_failure_issue,
    report_gpu_timeout,
)


def _event() -> FailureEvent:
    return FailureEvent(
        job_name="dakp_release_build",
        run_id="run-123",
        run_url="http://localhost:3000/runs/run-123",
        git_sha="abc123",
        version="1.27.1",
        delta_message="New data for 2 sources on 2026-11-04",
        gpu_summary="GPU 0: 0 MiB",
        steps=(
            StepStat(
                "extract_faers", "SUCCESS", "2026-11-06T17:00:00Z", "2026-11-06T17:10:00Z", 600.0
            ),
            StepStat(
                "shape_contraindications",
                "FAILURE",
                "2026-11-06T17:10:00Z",
                "2026-11-06T18:00:00Z",
                3000.0,
                error="RuntimeError: HF_TOKEN=hf_abcdefghijklmnopqr failed",
                log_tail=("line with ghp_abcdefghijklmnopqr\n" * 1000),
            ),
        ),
    )


def test_issue_template_has_stats_delta_and_sanitized_truncated_logs() -> None:
    title, body = render_failure_issue(_event())
    assert "[DAKP build failed]" in title
    assert "dakp-fingerprint:" in body
    assert "New data for 2 sources" in body
    assert "shape_contraindications" in body
    assert "3000.0" in body
    assert "truncated" in body
    assert "hf_abcdefghijklmnopqr" not in body
    assert "ghp_abcdefghijklmnopqr" not in body
    assert "HF_TOKEN=***" in body
    assert len(body) <= 60_000


class _Transport:
    def __init__(self):
        self.issues = []
        self.comments = []
        self.calls = []

    def __call__(self, method, path, payload):
        self.calls.append((method, path, payload))
        if method == "GET":
            return self.issues
        if path.endswith("/labels"):
            return {"name": payload["name"]}
        if path.endswith("/comments"):
            self.comments.append(payload)
            return {"id": len(self.comments)}
        issue = {"number": 7, "html_url": "https://github.test/7", **payload}
        self.issues.append(issue)
        return issue


def test_repeat_failure_comments_instead_of_opening_duplicate() -> None:
    transport = _Transport()
    client = GitHubIssueClient("glusman-team/graph-monorepo", "secret", transport=transport)
    first = client.create_or_comment(_event())
    second = client.create_or_comment(_event())
    assert first == {"action": "created", "issue": 7, "url": "https://github.test/7"}
    assert second["action"] == "commented"
    assert len(transport.issues) == 1
    assert len(transport.comments) == 1


def test_gpu_timeout_uses_failure_template_and_one_fingerprint() -> None:
    transport = _Transport()
    client = GitHubIssueClient("owner/repo", "secret", transport=transport)
    result = report_gpu_timeout(
        repo="owner/repo",
        token="secret",
        check_run_id="check-1",
        deadline="2026-11-07T17:00:00-08:00",
        reason="GPUs remained above 1024 MiB",
        dagster_url="http://localhost:3000",
        client=client,
    )
    assert result["action"] == "created"
    assert "gpu_gate" in transport.issues[0]["body"]
    assert "1024 MiB" in transport.issues[0]["body"]
