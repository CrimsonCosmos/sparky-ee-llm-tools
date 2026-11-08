"""Independent cross-checks of a finished flow: each number LibreLane reports is re-derived from a different file by a different tool, so a wrong report cannot confirm itself.

    die size        LibreLane metric   vs   KLayout reading the streamed GDS
    clock period    the spec           vs   the SDC the flow wrote
    instance count  LibreLane metric   vs   instances counted in the final netlist
    flip-flops      Yosys on the RTL   vs   flop cells (Liberty `ff` groups) in the final netlist
Each check returns a `Check`; one that cannot run says so (`ok` None) instead of passing."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import toolchain
from .flow import FlowResult
from .spec import SiliconSpec


@dataclass
class Check:
    name: str
    ok: bool | None            # None = NOT RUN
    detail: str


def die_size_um(gds: Path) -> tuple[float, float]:
    py = toolchain.python()
    code = "import sys, klayout.db as db\nly = db.Layout()\nly.read(sys.argv[1])\nb = ly.top_cell().dbbox()\nprint(b.width(), b.height())\n"
    r = subprocess.run([py, "-c", code, str(gds)], capture_output=True, text=True, env=toolchain.env(), timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"KLayout could not read {gds}: {r.stderr.strip()[-200:]}")
    w, h = map(float, r.stdout.split())
    return w, h


def sdc_clock_period_ns(sdc_text: str, port: str) -> float | None:
    for line in sdc_text.splitlines():
        m = re.match(r"\s*create_clock\b.*-period\s+([\d.eE+-]+)\b.*\{?" + re.escape(port) + r"\}?\]", line)
        if m:
            return float(m.group(1))
    return None


_KEYWORDS = {"module", "endmodule", "input", "output", "inout", "wire", "reg", "assign", "supply0", "supply1", "parameter", "localparam"}


def netlist_instances(nl_text: str) -> dict[str, int]:
    """Instances per cell type in a structural Verilog netlist (comments removed; `module`/port/wire declarations skipped)."""
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", nl_text, flags=re.S)
    out: dict[str, int] = {}
    for m in re.finditer(r"^\s*([A-Za-z_\\][\w$\\]*)\s+(\\?\S+)\s*\(", text, re.M):
        cell = m.group(1)
        if cell in _KEYWORDS or cell.startswith(("`", "(")):
            continue
        out[cell] = out.get(cell, 0) + 1
    return out


def liberty_sequential_cells(lib_text: str) -> set[str]:
    """Cells that contain an `ff` (flip-flop) or `latch` group."""
    cells: set[str] = set()
    starts = [(m.start(), m.group(1)) for m in re.finditer(r'\bcell\s*\(\s*"?([^")\s]+)"?\s*\)\s*\{', lib_text)]
    for i, (pos, name) in enumerate(starts):
        block = lib_text[pos:starts[i + 1][0] if i + 1 < len(starts) else len(lib_text)]
        if re.search(r"\b(ff|latch)\s*\(", block):
            cells.add(name)
    return cells


def rtl_flop_count(spec: SiliconSpec) -> int:
    """Flip-flops/latches Yosys infers from the RTL alone (`synth`, then the statistics)."""
    yosys = toolchain.which("yosys")
    with tempfile.TemporaryDirectory(prefix="ys_") as td:
        out = Path(td) / "stat.json"
        files = " ".join(str(Path(p).resolve()) for p in spec.verilog_files)
        r = subprocess.run([yosys, "-q", "-p", f"read_verilog -sv {files}; synth -top {spec.design_name}; tee -q -o {out} stat -json"], capture_output=True, text=True,
                           env=toolchain.env(), timeout=300)
        if not out.is_file():
            raise RuntimeError(f"Yosys failed: {(r.stderr or r.stdout).strip()[-300:]}")
        text = out.read_text()
        data = json.loads(text[text.index("{"):])
    cells = data["design"].get("num_cells_by_type", {})
    return sum(n for t, n in cells.items() if re.match(r"\$_?(S?DFF|DLATCH|ALDFF|DFFSR|DFFE|SDFFE)", t) or t in ("$dff", "$dffe", "$adff", "$sdff"))


def run_checks(spec: SiliconSpec, result: FlowResult, tol: float = 0.01) -> list[Check]:
    """All cross-checks of one result. `tol` is the relative tolerance on sizes (rounding of streamed coordinates)."""
    if not result.ok or result.final is None:
        return [Check("flow", None, "NOT RUN: the flow did not complete")]
    checks: list[Check] = []
    a = result.artifacts

    def attempt(name, fn):
        try:
            checks.append(fn())
        except Exception as e:                                         # a check that cannot run is reported, never silently dropped
            checks.append(Check(name, None, f"NOT RUN: {e}"))

    def die():
        w, h = die_size_um(a["gds"])
        ref = result.metric("design__die__area")
        if ref is None:
            return Check("die size", None, "NOT RUN: the flow reported no die area")
        return Check("die size", abs(w * h / ref - 1) <= tol, f"KLayout reads {w:g} x {h:g} um = {w * h:g} um2 from the GDS; the flow reports {ref:g} um2")

    def clock():
        period = sdc_clock_period_ns((a["sdc"]).read_text(), spec.clock_port) if a["sdc"].is_file() else None
        if period is None:
            return Check("clock period", False, f"no create_clock on '{spec.clock_port}' in the SDC")
        return Check("clock period", abs(period - spec.clock_period_ns) <= 1e-9 * max(1, spec.clock_period_ns), f"spec {spec.clock_period_ns:g} ns, SDC {period:g} ns")

    def instances():
        counts = netlist_instances(a["netlist"].read_text())
        n, ref = sum(counts.values()), result.metric("design__instance__count")
        if ref is None:
            return Check("instance count", None, "NOT RUN: the flow reported no instance count")
        return Check("instance count", n == ref, f"{n} instances in the netlist, {ref} reported")

    def flops():
        counts = netlist_instances(a["netlist"].read_text())
        lib_map = result.resolved.get("LIB") or {}
        libs = [Path(p) for paths in lib_map.values() for p in paths if Path(p).is_file()]
        if not libs:
            return Check("flip-flops", None, "NOT RUN: no standard-cell Liberty file in the resolved configuration")
        seq = liberty_sequential_cells(libs[0].read_text())
        in_netlist = sum(n for c, n in counts.items() if c in seq)
        rtl = rtl_flop_count(spec)
        return Check("flip-flops", in_netlist == rtl, f"Yosys infers {rtl} from the RTL, {in_netlist} sequential cells in the netlist")

    attempt("die size", die)
    attempt("clock period", clock)
    attempt("instance count", instances)
    attempt("flip-flops", flops)
    return checks
