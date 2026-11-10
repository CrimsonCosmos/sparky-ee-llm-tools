"""Quality metrics of a (functionally verified) design, from Yosys' real device mapping: `synth_ice40` cell counts (SB_LUT4 / SB_CARRY / SB_DFF*), the longest topological path through the mapped
cells (`ltp`), a technology-neutral 2-input-gate count, and, when YoWASP nextpnr is installed, place-and-route timing on a stated device. nextpnr numbers are the tool's timing-model ESTIMATES, not
silicon measurements. A metric that cannot be produced is None with the reason in `notes` (never 0)."""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import tools
from .problem import Problem


@dataclass(frozen=True)
class Target:
    """The fixed device used for scoring (so numbers are comparable). Defaults are the device family Yosys/nextpnr/icestorm fully support in this environment."""
    family: str = "ice40"
    device_flag: str = "--hx8k"                 # nextpnr-ice40 device selection
    lut_size: int = 4                           # ice40 logic cell = LUT4 (the SB_LUT4 mapping above); used for the depth estimate
    package: str = "ct256"
    freq_mhz: float = 50.0                      # nextpnr timing-driven target; Fmax is reported independently of it


@dataclass
class Metrics:
    luts: int | None = None
    carries: int | None = None
    flops: int | None = None
    depth: int | None = None                    # longest path in LUT levels after `abc -lut N` (flip-flops excluded); a delay proxy, not a timing result
    gates: int | None = None                    # 2-input AND/OR/XOR + MUX count after ABC, technology neutral
    fmax_mhz: float | None = None               # sequential designs
    other: dict[str, int] = field(default_factory=dict)      # mapped cells that are NOT LUT4/carry/flip-flop (block RAM, DSP, ...): their cost is not captured by the LUT count
    cells: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _yosys(script: str, cwd: Path, timeout: int = 300) -> tuple[int, str]:
    y = tools.which("yosys")
    if not y:
        return 127, "yosys not found"
    return tools.run([y, "-q", "-p", script], cwd, timeout)


def synth_ice40(files: list[Path], top: str, out_json: Path, cwd: Path, lut_size: int = 4) -> tuple[dict[str, int], int | None, str]:
    """Cell counts, longest path (cells), log."""
    fl = " ".join(str(Path(f).resolve()) for f in files)
    rc, text = _yosys(f"read_verilog -sv {fl}; synth_ice40 -top {top} -json {out_json}; tee -o stat.json stat -json", cwd)
    rc2, text2 = _yosys(f"read_verilog -sv {fl}; synth -top {top} -flatten; abc -lut {lut_size}; tee -o ltp.txt ltp -noff", cwd)
    stat = cwd / "stat.json"
    if not stat.is_file():
        raise RuntimeError(f"synth_ice40 failed: {text.strip()[-300:]}")
    data = json.loads(stat.read_text())
    mod = data.get("design", data.get("modules", {}))
    cells = mod.get("num_cells_by_type") if "num_cells_by_type" in mod else next(iter(data["modules"].values()))["num_cells_by_type"]
    ltp = (cwd / "ltp.txt").read_text() if (cwd / "ltp.txt").is_file() else ""
    m = re.search(r"Longest topological path in \S+ \(length=(\d+)\)", ltp + text2)
    return {k: int(v) for k, v in cells.items() if k.startswith("SB_")}, (int(m.group(1)) if m else None), text


def gate_count(files: list[Path], top: str, cwd: Path) -> int:
    fl = " ".join(str(Path(f).resolve()) for f in files)
    rc, text = _yosys(f"read_verilog -sv {fl}; synth -top {top} -flatten; abc -g AND,OR,XOR,MUX; opt_clean -purge; tee -o g.json stat -json", cwd)
    g = cwd / "g.json"
    if not g.is_file():
        raise RuntimeError(f"gate count failed: {text.strip()[-200:]}")
    data = json.loads(g.read_text())
    cells = data["design"]["num_cells_by_type"] if "design" in data else next(iter(data["modules"].values()))["num_cells_by_type"]
    return sum(int(v) for k, v in cells.items() if k.startswith("$_") and k not in ("$_DFF_P_", "$_DFF_N_"))


def nextpnr_timing(p: Problem, json_path: Path, target: Target, cwd: Path, timeout: int = 600) -> tuple[float | None, float | None, str]:
    exe = tools.nextpnr_ice40()
    if not exe:
        return None, None, "nextpnr-ice40 (pip install yowasp-nextpnr-ice40) not found: timing NOT RUN"
    rc, text = tools.run([exe, target.device_flag, "--package", target.package, "--json", json_path.name, "--freq", f"{target.freq_mhz:g}", "--pcf-allow-unconstrained", "--seed", "1"], cwd, timeout)
    if rc != 0:
        why = re.findall(r"ERROR: (.*)", text)
        return None, None, f"nextpnr failed: {why[0] if why else text.strip()[-200:]}"
    fm = list(re.finditer(r"Max frequency for clock '[^']*': ([\d.]+) MHz", text))[-1:] and list(re.finditer(r"Max frequency for clock '[^']*': ([\d.]+) MHz", text))[-1]     # last = after routing
    cd = re.search(r"Max delay <async>\s+-> <async>\s*: ([\d.]+) ns", text)
    return (float(fm.group(1)) if fm else None), (float(cd.group(1)) if cd else None), "nextpnr-ice40 timing-model estimate (seed 1)"


def measure(p: Problem, files: list[Path], target: Target | None = None, pnr: bool = True) -> Metrics:
    target = target or Target()
    m = Metrics()
    with tempfile.TemporaryDirectory(prefix="hb_met_") as td:
        td = Path(td)
        try:
            cells, depth, _ = synth_ice40(files, p.top, td / "d.json", td, target.lut_size)
        except RuntimeError as e:
            m.notes.append(str(e))
            return m
        m.cells = cells
        m.luts = cells.get("SB_LUT4", 0)
        m.carries = cells.get("SB_CARRY", 0)
        m.flops = sum(v for k, v in cells.items() if k.startswith("SB_DFF"))
        m.depth = depth
        m.other = {k: v for k, v in cells.items() if k != "SB_LUT4" and k != "SB_CARRY" and not k.startswith("SB_DFF")}
        if m.other:
            m.notes.append(f"uses other primitives {m.other}: LUT counts are NOT comparable (memory/DSP hidden from the LUT metric)")
        if depth is None:
            m.notes.append("longest path not reported by Yosys ltp")
        try:
            m.gates = gate_count(files, p.top, td)
        except RuntimeError as e:
            m.notes.append(str(e))
        if pnr and p.kind == "seq":
            fmax, _, note = nextpnr_timing(p, td / "d.json", target, td)
            m.notes.append(note)
            m.fmax_mhz = fmax
            if fmax is None and "failed" not in note:
                m.notes.append("nextpnr reported no Fmax: the design has no register-to-register path")
        elif pnr:
            m.notes.append("nextpnr timing NOT RUN for combinational designs: pad-to-pad delay depends on arbitrary pin placement; use `depth` (mapped-cell levels) as the delay proxy")
    return m


def ratios(cand: Metrics, ref: Metrics) -> dict[str, float | None]:
    """candidate / reference for each lower-is-better metric (1.0 = same as the human reference, below 1.0 = better)."""
    def r(a, b):
        return None if a is None or b in (None, 0) else a / b
    return {"luts": r(cand.luts, ref.luts), "depth": r(cand.depth, ref.depth), "gates": r(cand.gates, ref.gates), "flops": r(cand.flops, ref.flops)}
