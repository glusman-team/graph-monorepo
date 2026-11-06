"""Canonical asset/job stage names."""

STAGE_RELEASE = "stage_release"
PUSH_STAGED_RELEASE = "push_staged_release"
STATUS_REPORT = "status_report"
PROBE_RELEASE = "probe_release"
VALIDATE_RELEASE = "validate_release"
DRY_RUN_PUBLICATION = "dry_run_publication"
TRAIN_DICTIONARY = "train_dictionary"
INSPECT_KGX = "inspect_kgx"
INSPECT_EDGE = "inspect_edge"
INIT_STORE = "init_store"
PURGE_STORE = "purge_store"

__all__ = [name for name in globals() if name.isupper()]
