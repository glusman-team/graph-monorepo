"""Pipes helpers: launch Go workers (DAKP pattern) from dagster assets.

Python side of the contract with `workers/go/internal/pipesutil`:
- the worker reads its pipes context from env (set by PipesSubprocessClient)
- the worker reports materializations with BLAKE3 cache metadata
  (cache_key, cache_hit, rows)
"""

from __future__ import annotations

from dagster import AssetExecutionContext, PipesSubprocessClient


def subprocess_client() -> PipesSubprocessClient:
    """Fresh pipes subprocess client per invocation (dagster-recommended usage)."""
    return PipesSubprocessClient()


def subprocess_client_resource() -> PipesSubprocessClient:
    """Resource form for definitions that prefer resources over direct calls.

    PipesSubprocessClient is a TreatAsResourceParam: pass the instance itself
    as the value in Definitions(resources={...}); dagster adapts it on load.
    """
    return PipesSubprocessClient()


def launch_worker(
    context: AssetExecutionContext,
    worker_bin: str,
    extras: dict | None = None,
):
    """Run a Go worker binary as a pipes subprocess, returning the completed invocation.

    `run()` blocks until the child exits (non-zero exit raises
    DagsterPipesExecutionError), so the caller finishes with
    `.get_materialize_result()`, which carries whatever the worker reported.
    """
    return subprocess_client().run(
        context=context,
        command=[worker_bin],
        extras=extras or {},
    )
