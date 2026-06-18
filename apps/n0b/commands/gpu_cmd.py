"""GPU availability and memory helpers."""
from __future__ import annotations

import platform
import subprocess
import sys


def cuda_available(verbose: bool = False) -> bool:
    import shutil

    if not shutil.which("nvidia-smi"):
        if verbose:
            print("CUDA not available (nvidia-smi not found)")
        return False
    proc = subprocess.run(
        ["nvidia-smi"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        if verbose:
            print("nvidia-smi found but failed to query GPU")
        return False
    if verbose:
        print("CUDA is available")
        detail = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if detail.stdout.strip():
            print(detail.stdout.strip())
    return True


def mps_available(verbose: bool = False) -> bool:
    if platform.system() != "Darwin":
        if verbose:
            print("MPS not available (not macOS)")
        return False
    proc = subprocess.run(
        ["system_profiler", "SPDisplaysDataType"],
        capture_output=True,
        text=True,
        check=False,
    )
    gpu_info = proc.stdout
    if "Apple M" in gpu_info:
        if verbose:
            print("MPS is available (Apple Silicon)")
            for line in gpu_info.splitlines():
                if "Chipset Model:" in line or "Total Number of Cores:" in line:
                    print(line.strip())
        return True
    if "Metal" in gpu_info:
        if verbose:
            print("MPS is available (Metal-capable GPU)")
            for line in gpu_info.splitlines():
                if "Chipset Model:" in line or "Metal:" in line:
                    print(line.strip())
        return True
    if verbose:
        print("MPS not available (no Metal-capable GPU found)")
    return False


def mlx_available(verbose: bool = False) -> bool:
    if platform.system() != "Darwin":
        if verbose:
            print("MLX not available (not macOS)")
        return False
    arch = platform.machine()
    proc = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"],
        capture_output=True,
        text=True,
        check=False,
    )
    cpu_brand = proc.stdout.strip() if proc.returncode == 0 else ""
    if arch == "arm64" or "Apple" in cpu_brand:
        if verbose:
            print("MLX is available (Apple Silicon macOS detected)")
            if cpu_brand:
                print(f"Processor: {cpu_brand}")
        return True
    if verbose:
        print(f"MLX not available (requires Apple Silicon, found {arch} / {cpu_brand})")
    return False


def cmd_cuda(verbose: bool) -> int:
    return 0 if cuda_available(verbose) else 1


def cmd_mps(verbose: bool) -> int:
    return 0 if mps_available(verbose) else 1


def cmd_mlx(verbose: bool) -> int:
    return 0 if mlx_available(verbose) else 1


def cmd_mb_free() -> int:
    if cuda_available():
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            print(proc.stdout.strip().splitlines()[0])
            return 0
    if mps_available() and platform.system() == "Darwin":
        proc = subprocess.run(
            ["sh", "-c", "vm_stat | awk -v ps=$(pagesize) '/Pages free/ {free=$3} /Pages inactive/ {inactive=$3} END { printf \"%.0f\", (free+inactive)*ps/1024/1024 }'"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            print(proc.stdout.strip())
            return 0
    print("0")
    return 1
