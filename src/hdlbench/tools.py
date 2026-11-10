"""Tool lookup and process running. Reuses the silicon toolchain lookup (Yosys, Icarus, Verilator, SymbiYosys); nextpnr comes from the optional YoWASP wheel. A missing tool is reported, never skipped silently."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from silicon import toolchain


def which(tool: str) -> str | None:
    p = toolchain.which(tool)
    if p:
        return p
    sibling = Path(sys.executable).parent / tool                      # tools installed into the same environment (e.g. YoWASP wheels)
    return str(sibling) if sibling.is_file() else shutil.which(tool)


def nextpnr_ice40() -> str | None:
    return which("yowasp-nextpnr-ice40") or which("nextpnr-ice40")


def run(cmd: list[str], cwd: Path | str | None = None, timeout: int = 300, stdin: str = "") -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=toolchain.env(), input=stdin)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout} s: {' '.join(cmd[:3])}"
    return r.returncode, r.stdout + r.stderr


def report() -> dict[str, str | None]:
    return {t: which(t) for t in ("yosys", "iverilog", "vvp", "verilator", "sby", "yosys-abc")} | {"nextpnr-ice40": nextpnr_ice40()}
