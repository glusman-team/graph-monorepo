"""Logging: ``loguru`` is the primary structured logger, bridged into stdlib ``logging``
(no orchestrator coupling).

Three pieces:

* :class:`InterceptHandler` installs on the stdlib root logger so third-party libraries that
  emit through ``logging`` flow into ``loguru`` (single sink of truth).
* :func:`configure_logging` adds an optional rotating file sink under ``<workdir>/logs/`` plus
  a structured stderr sink.
* :func:`configure_worker_logging` is the SPAWNED-CHILD counterpart (the per-GPU NER workers):
  it redirects the child's stderr FILE DESCRIPTOR to a per-worker file, the only capture point
  that also covers libraries owning their own stderr handler (``transformers``, ``torch``) and
  native output.

Narration helpers (:func:`stats`, :func:`step`, :func:`progress`) implement the DAKP
one-stat-per-line convention so stage logs stay readable and greppable. Forwarded records carry
the :data:`FROM_LOGURU_ATTR` sentinel so :class:`InterceptHandler` never re-ingests them (loop
prevention).
"""

from __future__ import annotations

import importlib.machinery
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger


class InterceptHandler(logging.Handler):
    """Forward stdlib :mod:`logging` records into ``loguru``.

    Mirrors the canonical loguru integration recipe. Installed on the stdlib root
    logger (and selected children) so library logs share loguru's sinks and formatting.
    Records carrying the :data:`FROM_LOGURU_ATTR` sentinel originated FROM loguru and are
    skipped so the bridge can never loop.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, FROM_LOGURU_ATTR, False):
            return  # re-emitted from loguru; re-ingesting it would loop

        # Map stdlib level names to loguru levels (catch custom levels gracefully).
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find the caller that originated the record, not this handler: walk up through the
        # stdlib logging frames. The first frame is always this handler (in this module, not
        # ``logging``), so ``depth == 0`` forces the first step before the filename test applies.
        frame, depth = logging.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(workdir: Path | None = None, level: str = "INFO") -> None:
    """Configure loguru sinks and the stdlib bridge.

    Idempotent: repeated calls replace sinks rather than stacking them.

    Args:
        workdir: If given, a rotating file sink is added at ``<workdir>/logs/dakp.log``.
        level: Minimum level for the sinks (e.g. ``"INFO"`` / ``"DEBUG"``).
    """
    logger.remove()

    if workdir is not None:
        log_dir = workdir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "dakp.log",
            level=level,
            rotation="20 MB",
            retention=5,
            compression="gz",
            backtrace=False,
            diagnose=False,
            enqueue=False,
        )

    # loguru is the single sink of truth. Primary human-facing sink: structured stderr.
    # Auto-colorize only on a real TTY (avoids a terminfo lookup that prints a spurious
    # warning under pytest capture).
    logger.add(
        sys.stderr, level=level, colorize=None, backtrace=False, diagnose=False, enqueue=False
    )

    # Bridge stdlib logging -> loguru so third-party libs share our sinks.
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logging.root.setLevel(level)
    for noisy in ("urllib3", "botocore"):
        logging.getLogger(noisy).setLevel(level)


#: Worker log files live here, one file per worker PROCESS (see :func:`_worker_log_path`).
WORKER_LOG_SUBDIR = Path("logs") / "workers"

#: Level for the worker's own (DAKP) records once its stderr is a private file: keep it verbose.
_WORKER_SINK_LEVEL = "DEBUG"

#: Level for the worker's records when stderr is still the inherited one (no worker file): real problems
#: only. An ERROR genuinely belongs in the task log; INFO/DEBUG is the noise this module removes.
_WORKER_FALLBACK_LEVEL = "ERROR"

#: Root level for bridged stdlib records (``warnings``, libraries without their own handler).
_WORKER_LIBRARY_LEVEL = logging.WARNING

#: Worker logs older than this are pruned at dispatch time (they are per-process diagnostics,
#: so nothing rotates them in place).
WORKER_LOG_MAX_AGE_S = 7 * 24 * 60 * 60

_STDERR_FD = 2


def _worker_log_name(name: str) -> str:
    """Sanitize a worker name into a filesystem-safe stem (``"cuda:0"`` -> ``"cuda-0"``)."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.") or "worker"


def _worker_log_path(workdir: Path | str, name: str) -> Path:
    """Per-PROCESS worker log path: ``<workdir>/logs/workers/<name>-<pid>.log``.

    The pid is part of the name because a device is NOT a unique worker key: sequential shape
    tasks and repeated dispatches reuse each device with a fresh process. Keying on the pid
    guarantees no two processes ever
    write to one file, which is also why nothing rotates these files in place (see
    :func:`prune_worker_logs`).
    """
    log_dir = Path(workdir) / WORKER_LOG_SUBDIR
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{_worker_log_name(name)}-{os.getpid()}.log"


def configure_worker_logging(workdir: Path | str | None, name: str) -> None:
    """Configure logging inside a ``spawn``-started child process (the per-GPU NER workers).

    A spawned child is a fresh interpreter: loguru starts with its DEFAULT sink (``sys.stderr``,
    level DEBUG) and stdlib ``logging`` has no handler. A parent process may capture a subprocess's
    stderr as generic noise.

    The capture point is the stderr FILE DESCRIPTOR, not ``sys.stderr`` or the root logger:
    ``transformers`` and ``torch`` both set ``propagate = False`` on their library logger and
    attach their OWN ``StreamHandler(sys.stderr)``, so a root-level :class:`InterceptHandler`
    never sees those records, and native CUDA / tqdm output never passes through Python at all.
    ``dup2`` onto fd 2 catches every one of them. Nothing is suppressed; it is REDIRECTED to
    ``<workdir>/logs/workers/<name>-<pid>.log``, and on top of the redirect:

    * loguru is re-pointed at ``sys.stderr`` (now the file) at :data:`_WORKER_SINK_LEVEL`;
    * stdlib ``logging`` is bridged into loguru by :class:`InterceptHandler` at
      :data:`_WORKER_LIBRARY_LEVEL`, so propagating library records are formatted like ours
      (libraries holding their own stderr handler land in the same file via the redirect);
    * :mod:`warnings` is routed through ``logging.captureWarnings`` into that same bridge.

    With no usable worker file (``workdir`` None in tests/offline runs, or an unwritable log
    directory) the child keeps the inherited stderr but logs at :data:`_WORKER_FALLBACK_LEVEL`
    only: a failure still surfaces in the task log, the routine narration does not. Logging
    setup never raises here — an unwritable path must not turn into a failed shard.
    """
    logger.remove()  # drop loguru's default sink; re-added below at the level we actually want

    level = _WORKER_FALLBACK_LEVEL
    if workdir is not None:
        try:
            with _worker_log_path(workdir, name).open("ab", buffering=0) as handle:
                # dup2 duplicates the open file description onto fd 2, so closing `handle`
                # right after leaves fd 2 pointing at the file for the life of the process.
                sys.stderr.flush()
                os.dup2(handle.fileno(), _STDERR_FD)
        except OSError:
            pass  # unwritable workdir: keep inherited stderr, stay at the fallback level
        else:
            level = _WORKER_SINK_LEVEL

    logger.add(
        sys.stderr, level=level, colorize=False, backtrace=False, diagnose=False, enqueue=False
    )

    # Bridge stdlib logging (and, through it, `warnings`) into loguru. A spawned child owns
    # its own root logger, so clobbering the root handlers is safe here. Libraries that set `propagate = False` bypass this and are caught by the fd
    # redirect instead.
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    logging.root.setLevel(_WORKER_LIBRARY_LEVEL)
    logging.captureWarnings(True)


def prune_worker_logs(workdir: Path | str, max_age_s: float = WORKER_LOG_MAX_AGE_S) -> int:
    """Delete worker logs older than ``max_age_s``; return how many were removed.

    Worker logs are per-process files written through a redirected file descriptor, so no sink
    rotates or retires them. The dispatching parent calls this once before spawning, which
    bounds the directory without ever touching a file a live worker holds open (those are
    brand new). Best-effort by design: a missing directory or an unlink race is not an error.
    """
    cutoff = time.time() - max_age_s
    removed = 0
    for path in sorted((Path(workdir) / WORKER_LOG_SUBDIR).glob("*.log")):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:  # pragma: no cover - unlink/stat race with a concurrent dispatcher
            continue
    return removed


def bind(**fields: Any) -> Any:
    """Return a loguru logger pre-bound with structured fields for the current task.

    Example::

        log = bind(task_id="extract_faers", shard_id="24Q3", artifact_id=ref.blake3)
        log.info("parsed quarter", rows=12, cache_hit=False)
    """
    return logger.bind(**fields)


# --- one-stat-per-line narration helpers ------------------------------------------
#
# Stage logs read best as short, self-contained lines, so DAKP never packs several stats
# into one record: every stat gets its own ``event: key = value`` line, prefixed
# with the event name so each line stands alone and is greppable.

#: Attribute set on stdlib records re-emitted from loguru so bridges never re-ingest them.
FROM_LOGURU_ATTR = "_dakp_from_loguru"


def _format_value(value: Any) -> str:
    """Render a stat value for a log line (bools lowercase, everything else ``str``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def stats(log: Any, event: str, /, *, level: str = "INFO", _depth: int = 1, **fields: Any) -> None:
    """Emit one ``event: key = value`` log line per field, in insertion order.

    Example::

        stats(log, "extract_faers", quarter="24Q3", rows=123, cache_hit=False)
        # extract_faers: quarter = 24Q3
        # extract_faers: rows = 123
        # extract_faers: cache_hit = false

    ``log`` may be the module :data:`logger` or any :func:`bind` result. ``level`` is a loguru
    level name (e.g. ``"DEBUG"`` for verbose per-artifact stats). ``_depth`` attributes the
    record to the caller (helpers like :func:`step` pass 2 so records name THEIR caller).
    """
    for key, value in fields.items():
        log.opt(depth=_depth).log(level, "{}: {} = {}", event, key, _format_value(value))


def _elapsed_s(started: float) -> float:
    return round(time.monotonic() - started, 3)


@contextmanager
def step(log: Any, event: str, /, **context: Any) -> Iterator[None]:
    """Narrate a pipeline phase: ``event: started`` on entry, finish/fail stats on exit.

    On success emits ``event: finished = true`` and ``event: elapsed_s = <N>`` (one line
    each). On exception emits ``event: failed = true``, ``event: error = <ExcType>`` and the
    elapsed time, then re-raises. Extra keyword arguments become plain stat lines on the
    finish/fail records (identity fields such as the table name), so nested phases stay
    attributable when several run inside one task. Usage::

        with step(log, "acquire_faers"):
            refs = faers.fetch(ctx)
    """
    log.opt(depth=1).info("{}: started", event)
    started = time.monotonic()
    try:
        yield
    except BaseException as exc:
        stats(
            log,
            event,
            _depth=2,
            failed=True,
            error=type(exc).__name__,
            elapsed_s=_elapsed_s(started),
            **context,
        )
        raise
    stats(log, event, _depth=2, finished=True, elapsed_s=_elapsed_s(started), **context)


def progress(log: Any, event: str, done: int, total: int, *, every: int) -> None:
    """Emit ``event: progress = <done>/<total>`` every ``every`` items; a no-op otherwise.

    Call once per item after incrementing; only multiples of ``every`` (and never ``done=0``)
    log, so long loops stay quiet between milestones.
    """
    if done > 0 and every > 0 and done % every == 0:
        log.opt(depth=1).info("{}: progress = {}/{}", event, done, total)


#: Module spawned workers re-import instead of the parent's ``__main__`` script (see
#: :func:`spawn_safe_main`). Must import with zero side effects. A plain MODULE, not the
#: package itself - ``runpy.run_module`` cannot directly execute a package without a
#: ``__main__.py``.
_SPAWN_SAFE_MAIN_MODULE = "dakp_pipeline.logging_setup"


@contextmanager
def spawn_safe_main() -> Iterator[None]:
    """Keep spawn children from re-executing the parent's ``__main__`` script.

    The spawn start method re-initializes ``__main__`` in every child: when
    ``__main__.__spec__`` exists it imports that module by name, otherwise it RE-EXECUTES
    ``__main__.__file__``. When the parent runs as a plain script with no spec (a CLI, an
    orchestrator's entrypoint), each spawned worker would re-execute that script and die
    initializing whatever it needs (observed as ``BrokenProcessPool`` before the first
    task). For the pool's lifetime, point spawn at a side-effect-free module; the worker
    callable itself is unpickled via its own module, and the original ``__main__`` spec is
    restored on exit.
    """
    main = sys.modules["__main__"]
    if getattr(main, "__spec__", None) is not None:
        yield  # module context (python -m / console scripts with a spec): spawn imports by name
        return
    main.__spec__ = importlib.machinery.ModuleSpec(_SPAWN_SAFE_MAIN_MODULE, loader=None)
    try:
        yield
    finally:
        main.__spec__ = None


__all__ = [
    "FROM_LOGURU_ATTR",
    "WORKER_LOG_MAX_AGE_S",
    "WORKER_LOG_SUBDIR",
    "InterceptHandler",
    "bind",
    "configure_logging",
    "configure_worker_logging",
    "logger",
    "progress",
    "prune_worker_logs",
    "spawn_safe_main",
    "stats",
    "step",
]
