"""RTL verification with the open tools: Verilator lint, and cycle-by-cycle equivalence of the RTL against the flow's gate-level netlist (or against another RTL) by simulating
both with Icarus Verilog on the same seeded random stimulus. Ports are read from the design by Yosys (not guessed); the testbench is generated from them; the cell models are
the PDK's own Verilog. A mismatch names the first differing cycle and signal. Zero-delay functional simulation only: it does not check timing (OpenSTA does) and random stimulus
is evidence, not proof."""
from __future__ import annotations

import json
import random
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import toolchain
from .flow import FlowResult
from .pdk import profile
from .signoff import FAIL, NOT_RUN, PASS, Verdict
from .spec import SiliconSpec


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=toolchain.env(), cwd=cwd, input="")
    return r.returncode, r.stdout + r.stderr


# ---- lint --------------------------------------------------------------------------------------------------------------------------------
@dataclass
class LintReport:
    errors: int
    warnings: int
    by_code: dict[str, int] = field(default_factory=dict)
    text: str = ""

    @property
    def verdict(self) -> Verdict:
        n = self.errors + self.warnings
        return Verdict("lint.verilator", PASS if n == 0 else FAIL, "rerun",
                       f"Verilator -Wall: {self.errors} error(s), {self.warnings} warning(s)" + (f" ({', '.join(f'{k}: {v}' for k, v in sorted(self.by_code.items()))})" if self.by_code else ""))


def lint(spec: SiliconSpec) -> LintReport:
    verilator = toolchain.which("verilator")
    if not verilator:
        raise RuntimeError("verilator not found")
    rc, text = _run([verilator, "--lint-only", "-Wall", "-sv", "--top-module", spec.design_name, *map(str, spec.verilog_files)])
    codes: dict[str, int] = {}
    for m in re.finditer(r"^%Warning-(\w+):", text, re.M):
        codes[m.group(1)] = codes.get(m.group(1), 0) + 1
    errors = len(re.findall(r"^%Error", text, re.M))
    return LintReport(errors, sum(codes.values()), codes, text)


# ---- ports and stimulus -----------------------------------------------------------------------------------------------------------------
def ports(spec: SiliconSpec) -> dict[str, tuple[str, int]]:
    """name -> (direction, width) of the top module, as Yosys elaborates it."""
    yosys = toolchain.which("yosys")
    with tempfile.TemporaryDirectory(prefix="ysp_") as td:
        out = Path(td) / "d.json"
        files = " ".join(str(Path(p).resolve()) for p in spec.verilog_files)
        rc, text = _run([yosys, "-q", "-p", f"read_verilog -sv {files}; hierarchy -top {spec.design_name}; proc; write_json {out}"])
        if not out.is_file():
            raise RuntimeError(f"Yosys could not elaborate the RTL: {text.strip()[-300:]}")
        mod = json.loads(out.read_text())["modules"][spec.design_name]
    return {n: (p["direction"], len(p["bits"])) for n, p in mod["ports"].items()}


def random_vectors(spec: SiliconSpec, pts: dict[str, tuple[str, int]], cycles: int, seed: int, reset_cycles: int = 3) -> dict[str, list[int]]:
    """Per non-clock input, one value per cycle: random, with the reset asserted for the first `reset_cycles` and released after."""
    rng = random.Random(seed)
    vec: dict[str, list[int]] = {}
    for name, (d, w) in pts.items():
        if d != "input" or name == spec.clock_port:
            continue
        if name == spec.reset_port:
            active = 0 if spec.reset_active_low else 1
            vec[name] = [active if i < reset_cycles else 1 - active for i in range(cycles)]
        else:
            vec[name] = [rng.getrandbits(w) for _ in range(cycles)]
    return vec


def _testbench(spec: SiliconSpec, pts: dict[str, tuple[str, int]], vec: dict[str, list[int]], cycles: int) -> str:
    ins = sorted(vec)
    outs = sorted(n for n, (d, _) in pts.items() if d == "output")
    decl = [f"reg [{pts[n][1] - 1}:0] {n};" for n in ins] + [f"wire [{pts[n][1] - 1}:0] {n};" for n in outs]
    mems = [f"reg [{pts[n][1] - 1}:0] v_{n} [0:{cycles - 1}];" for n in ins]
    conns = [f".{spec.clock_port}(clk)"] + [f".{n}({n})" for n in ins + outs]
    reads = [f'$readmemh("vec_{n}.hex", v_{n});' for n in ins]
    drive = [f"{n} = v_{n}[i];" for n in ins]
    fmt = " ".join("%h" for _ in outs)
    return (f"`timescale 1ns/1ps\nmodule tb;\nreg clk = 0;\n" + "\n".join(decl + mems) + f"\n{spec.design_name} dut ({', '.join(conns)});\nalways #5 clk = ~clk;\ninteger fd, i;\n"
            "initial begin\n" + "\n".join(reads) + f'\nfd = $fopen("out.txt", "w");\nfor (i = 0; i < {cycles}; i = i + 1) begin\n@(negedge clk);\n' + "\n".join(drive)
            + f'\n@(posedge clk); #1;\n$fdisplay(fd, "{fmt}"{"".join(", " + n for n in outs)});\nend\n$fclose(fd);\n$finish;\nend\nendmodule\n'), outs


def simulate(spec: SiliconSpec, files: list[Path], vec: dict[str, list[int]], pts: dict[str, tuple[str, int]], cycles: int, defines: tuple = ()) -> list[str]:
    """Per-cycle output lines of one design under the stimulus (Icarus Verilog, outputs sampled just after each rising edge)."""
    iverilog, vvp = toolchain.which("iverilog"), toolchain.which("vvp")
    with tempfile.TemporaryDirectory(prefix="sim_") as td:
        td = Path(td)
        tb, outs = _testbench(spec, pts, vec, cycles)
        (td / "tb.v").write_text(tb)
        for n, vals in vec.items():
            (td / f"vec_{n}.hex").write_text("\n".join(f"{v:x}" for v in vals) + "\n")
        rc, text = _run([iverilog, "-g2012", *defines, "-o", str(td / "sim.vvp"), str(td / "tb.v"), *map(str, files)], td)
        if rc != 0:
            raise RuntimeError(f"iverilog failed: {text.strip()[-400:]}")
        rc, text = _run([vvp, str(td / "sim.vvp")], td)
        out = td / "out.txt"
        if not out.is_file():
            raise RuntimeError(f"simulation wrote no output (rc={rc}): {text.strip()[-300:]}")
        return [l.strip() for l in out.read_text().splitlines() if l.strip()]


def first_mismatch(a: list[str], b: list[str], names: list[str], x_warmup: int = 0) -> str | None:
    if len(a) != len(b):
        return f"different number of cycles ({len(a)} vs {len(b)})"
    for i, (x, y) in enumerate(zip(a, b)):
        if i >= x_warmup and x != y:
            bad = [n for n, p, q in zip(names, x.split(), y.split()) if p != q]
            return f"cycle {i}: {', '.join(bad)} differ ({x} vs {y})"
    return None


def equivalence(spec: SiliconSpec, result: FlowResult, reference_rtl: list[Path] | None = None, cycles: int = 200, seed: int = 1, x_warmup: int = 0) -> Verdict:
    """Gate-level netlist of the run vs the RTL (or `reference_rtl`), same random stimulus."""
    check = "equiv.gate_vs_rtl"
    if not result.ok or "netlist" not in result.artifacts:
        return Verdict(check, NOT_RUN, "rerun", "no finished flow with a netlist")
    models = result.resolved.get("CELL_VERILOG_MODELS") or []
    if not models:
        return Verdict(check, NOT_RUN, "rerun", "no cell Verilog models in the resolved configuration")
    prof = profile(spec.pdk)
    if not prof.sim_defines and spec.pdk != "sky130A":
        return Verdict(check, NOT_RUN, "rerun", f"the simulation macros for {spec.pdk}'s cell models are not recorded (only verified for sky130A)")
    pts = ports(spec)
    vec = random_vectors(spec, pts, cycles, seed)
    outs = sorted(n for n, (d, _) in pts.items() if d == "output")
    rtl = simulate(spec, list(reference_rtl or spec.verilog_files), vec, pts, cycles)
    gate = simulate(spec, [result.artifacts["netlist"], *map(Path, models)], vec, pts, cycles, prof.sim_defines)
    bad = first_mismatch(rtl, gate, outs, x_warmup)
    return Verdict(check, PASS if bad is None else FAIL, "rerun", f"{cycles} random cycles (seed {seed}): " + ("netlist equals RTL on every output" if bad is None else f"MISMATCH at {bad}"))
