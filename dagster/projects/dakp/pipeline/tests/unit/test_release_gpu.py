"""GPU gate parses real nvidia-smi CSV shapes and enforces every-card limits."""

import subprocess

import pytest

from dakp_pipeline.release.gpu import GPUProbeError, evaluate_gpu_gate, probe_gpus


def _runner(gpu_csv: str, process_csv: str = ""):
    def run(command, **kwargs):
        stdout = gpu_csv if "--query-gpu" in command[1] else process_csv
        return subprocess.CompletedProcess(command, 0, stdout, "")

    return run


def test_gpu_gate_opens_when_every_card_is_at_most_1gib() -> None:
    status = probe_gpus(
        runner=_runner(
            "0, Tesla P100-PCIE-16GB, 512, 16384\n"
            "1, Tesla P100-PCIE-16GB, 1024, 16384\n"
            "2, Tesla P100-PCIE-16GB, 0, 16384\n"
        )
    )
    gate = evaluate_gpu_gate(status, 1024)
    assert gate.free
    assert "GPU gate open" in gate.reason


def test_gpu_gate_closes_when_any_card_exceeds_limit() -> None:
    status = probe_gpus(
        runner=_runner(
            "0, Tesla P100-PCIE-16GB, 0, 16384\n1, Tesla P100-PCIE-16GB, 13600, 16384\n",
            "1234, /usr/bin/llama-server, 13600\n",
        )
    )
    gate = evaluate_gpu_gate(status, 1024)
    assert not gate.free
    assert "GPU 1" in gate.reason
    assert "13600 MiB" in gate.reason
    assert gate.status.processes[0]["process_name"] == "/usr/bin/llama-server"


def test_missing_or_failing_nvidia_smi_fails_loudly() -> None:
    with pytest.raises(GPUProbeError, match="exited 1"):
        probe_gpus(
            runner=lambda command, **kwargs: subprocess.CompletedProcess(
                command, 1, "", "driver error"
            )
        )
    with pytest.raises(GPUProbeError, match="cannot execute"):
        probe_gpus(runner=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("not found")))


def test_post_build_leak_check_matches_only_dakp_processes(tmp_path) -> None:
    from dakp_pipeline.release.gpu import GPUDevice, GPUStatus, find_dakp_gpu_processes

    proc = tmp_path / "proc"
    for pid, cmdline, environ in (
        (123, "python spawn_main", f"DAKP_WORKDIR={tmp_path / 'work'}"),
        (456, "/usr/bin/llama-server", ""),
    ):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(cmdline.encode())
        (proc / str(pid) / "environ").write_bytes(environ.encode())
    status = GPUStatus(
        (GPUDevice(0, "P100", 13000, 16384),),
        (
            {"pid": 123, "process_name": "python", "used_memory": "12000"},
            {"pid": 456, "process_name": "llama-server", "used_memory": "1000"},
        ),
    )
    leaks = find_dakp_gpu_processes(status, workdir=tmp_path / "work", proc_root=proc)
    assert [process["pid"] for process in leaks] == [123]
