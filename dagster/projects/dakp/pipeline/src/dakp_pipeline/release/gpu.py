"""GPU availability probe for the release gate (nvidia-smi, no torch import)."""

from __future__ import annotations

import csv
import io
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any


class GPUProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class GPUDevice:
    index: int
    name: str
    used_mib: int
    total_mib: int


@dataclass(frozen=True)
class GPUStatus:
    devices: tuple[GPUDevice, ...]
    processes: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "devices": [asdict(device) for device in self.devices],
            "processes": list(self.processes),
        }


@dataclass(frozen=True)
class GPUGateResult:
    free: bool
    reason: str
    status: GPUStatus
    max_used_mib: int


def _csv_rows(stdout: str) -> list[list[str]]:
    return [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(stdout)) if row]


def probe_gpus(
    *,
    runner: Callable[..., Any] = subprocess.run,
    nvidia_smi: str = "nvidia-smi",
) -> GPUStatus:
    command = [
        nvidia_smi,
        "--query-gpu=index,name,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = runner(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise GPUProbeError(f"cannot execute {nvidia_smi}: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or "no stderr"
        raise GPUProbeError(f"{nvidia_smi} exited {result.returncode}: {detail}")
    devices = tuple(
        GPUDevice(index=int(row[0]), name=row[1], used_mib=int(row[2]), total_mib=int(row[3]))
        for row in _csv_rows(result.stdout)
    )
    if not devices:
        raise GPUProbeError("nvidia-smi reported no GPU devices")

    process_command = [
        nvidia_smi,
        "--query-compute-apps=pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]
    processes_result = runner(process_command, text=True, capture_output=True, check=False)
    processes: tuple[dict[str, Any], ...] = ()
    if processes_result.returncode == 0:
        parsed = []
        for row in _csv_rows(processes_result.stdout):
            if len(row) < 3 or not row[0].isdigit():
                continue
            parsed.append(
                {
                    "pid": int(row[0]),
                    "process_name": row[1],
                    "used_memory": row[2],
                }
            )
        processes = tuple(parsed)
    return GPUStatus(devices=devices, processes=processes)


def evaluate_gpu_gate(status: GPUStatus, max_used_mib: int = 1024) -> GPUGateResult:
    busy = [device for device in status.devices if device.used_mib > max_used_mib]
    if busy:
        detail = ", ".join(
            f"GPU {device.index} ({device.name}) uses {device.used_mib} MiB" for device in busy
        )
        reason = f"GPU gate closed: {detail}; every card must use <= {max_used_mib} MiB"
    else:
        used = ", ".join(f"GPU {device.index}={device.used_mib} MiB" for device in status.devices)
        reason = f"GPU gate open: {used} (limit {max_used_mib} MiB per card)"
    return GPUGateResult(free=not busy, reason=reason, status=status, max_used_mib=max_used_mib)


def find_dakp_gpu_processes(
    status: GPUStatus,
    *,
    workdir,
    proc_root="/proc",
) -> tuple[dict[str, Any], ...]:
    """Return compute processes owned by this DAKP workdir or its NER workers."""
    from pathlib import Path

    leaks = []
    for process in status.processes:
        pid = int(process["pid"])
        cmdline_path = Path(proc_root) / str(pid) / "cmdline"
        environ_path = Path(proc_root) / str(pid) / "environ"
        try:
            cmdline = (
                cmdline_path.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
            )
            environ = (
                environ_path.read_bytes().replace(b"\0", b"\n").decode("utf-8", errors="replace")
            )
        except OSError:
            continue  # process exited between nvidia-smi and /proc inspection
        haystack = f"{cmdline}\n{environ}"
        if (
            str(workdir) in haystack
            or "dakp_pipeline.assertions.ner_dispatch" in haystack
            or "gliner" in cmdline.lower()
        ):
            leaks.append({**process, "cmdline": cmdline[:500]})
    return tuple(leaks)


__all__ = [
    "GPUDevice",
    "GPUGateResult",
    "GPUProbeError",
    "GPUStatus",
    "evaluate_gpu_gate",
    "find_dakp_gpu_processes",
    "probe_gpus",
]
