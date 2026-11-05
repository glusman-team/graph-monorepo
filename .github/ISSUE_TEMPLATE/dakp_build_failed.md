---
name: DAKP build failed
about: Automated report from a failed scheduled DAKP release check or build
title: "[DAKP build failed]: "
labels: dakp-build-failure
assignees: ""
---

<!-- dakp-fingerprint: $fingerprint -->

## Failure summary

- **Job:** `$job_name`
- **Run:** [$run_id]($run_url)
- **Git SHA:** `$git_sha`
- **DAKP version:** `$version`
- **Failed step(s):** `$failed_steps`
- **Reported at:** $timestamp

## What changed upstream

$delta_message

## Errors

$error_summary

## Step timings and status

| Step | Status | Started (UTC) | Ended (UTC) | Duration (s) |
|---|---|---|---|---:|
$step_stats

## GPU snapshot

```text
$gpu_summary
```

## Truncated logs

$log_tail

## Triage

1. Open the Dagster run for the complete event/compute logs.
2. Fix the failed stage on `main`; the next scheduled release requires
   `HEAD == origin/main` and a clean tracked tree.
3. If this is a repeated failure, new occurrences are added as comments to this
   issue instead of opening duplicates.
4. A GPU-gate timeout does not create a release. After the GPUs are free, run a
   fresh `dakp_release_check`; do not manually reuse an expired pending delta.
