"""Formal equivalence of a candidate to the reference, by Yosys (combinational: miter + built-in SAT) and SymbiYosys with the ABC engines (sequential: unbounded proof by PDR, else a bounded
check). Results: EQUIVALENT (proved), DIFFERENT (counterexample), BOUNDED (no difference found to depth N, NOT a proof), UNKNOWN (tool error/timeout). Only EQUIVALENT counts as solved.

Combinational reference bits that are X are don't-care (miter -ignore_gold_x). Sequential designs are compared from the reset state: reset is asserted for `reset_cycles` cycles then released for good,
and outputs are compared in every cycle after that (so a Mealy dependence on an input is compared too)."""
from __future__ import annotations

import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from . import tools
from .problem import Problem

EQUIVALENT, DIFFERENT, BOUNDED, UNKNOWN = "equivalent", "different", "bounded", "unknown"


@dataclass
class FormalResult:
    status: str
    method: str
    detail: str
    seconds: float
    counterexample: dict[str, str] | None = None

    @property
    def proved(self) -> bool:
        return self.status == EQUIVALENT


def _load_script(p: Problem, ref: list[Path], cand: list[Path], formal: bool) -> str:
    rd = "read_verilog -formal -sv" if formal else "read_verilog -sv"
    r = " ".join(map(str, ref)); c = " ".join(map(str, cand))
    return (f"{rd} {r}\nhierarchy -top {p.top}\nflatten\nproc\nopt_clean\nrename -top gold\ndesign -stash gold_d\ndesign -reset\n"
            f"{rd} {c}\nhierarchy -top {p.top}\nflatten\nproc\nopt_clean\nrename -top gate\ndesign -stash gate_d\ndesign -reset\n")


def _comb(p: Problem, ref: list[Path], cand: list[Path], timeout: int) -> FormalResult:
    t0 = time.time()
    yosys = tools.which("yosys")
    script = (_load_script(p, ref, cand, False) + "design -copy-from gold_d -as gold gold\ndesign -copy-from gate_d -as gate gate\n"
              "miter -equiv -flatten -make_outputs -ignore_gold_x gold gate miter\nhierarchy -top miter\n"
              f"sat -verify -enable_undef -set-def-inputs -prove trigger 0 -show-inputs -show-outputs -timeout {timeout} miter\n")
    with tempfile.TemporaryDirectory(prefix="hb_eq_") as td:
        (Path(td) / "eq.ys").write_text(script)
        rc, text = tools.run([yosys, "-q", "-s", "eq.ys", "-l", "log.txt"], td, timeout + 60)
        log = (Path(td) / "log.txt").read_text() if (Path(td) / "log.txt").is_file() else text
    dt = time.time() - t0
    if "SUCCESS!" in log and "Failed to prove" not in log and re.search(r"no model found", log):
        return FormalResult(EQUIVALENT, "yosys miter + SAT", "proved equal for all inputs (X in the reference = don't care)", dt)
    if "model found" in log and "no model found" not in log or "Failed to prove" in log or "FAIL" in log and "model" in log:
        return FormalResult(DIFFERENT, "yosys miter + SAT", "a counterexample exists", dt, _parse_model(log, p))
    if "TIMEOUT" in log.upper():
        return FormalResult(UNKNOWN, "yosys miter + SAT", f"SAT timed out after {timeout} s", dt)
    return FormalResult(UNKNOWN, "yosys miter + SAT", f"no verdict: {log.strip()[-300:]}", dt)


def _parse_model(log: str, p: Problem) -> dict[str, str]:
    """Counterexample from Yosys' result table: input values (`in_<name>`, decimal) and the two designs' outputs (`gold_<out>` / `gate_<out>`)."""
    ce: dict[str, str] = {}
    for line in log.splitlines():
        m = re.match(r"\s*\\(in_|gold_|gate_)(\w+)\s+(\d+)\s+([0-9a-f]+)\s+[01x]+\s*$", line)
        if m:
            ce[("" if m.group(1) == "in_" else m.group(1)) + m.group(2)] = m.group(3)
    return ce


def _seq_wrapper(p: Problem) -> str:
    ports = p.ports
    ins = p.data_inputs
    decl = ", ".join(["input " + p.clock] + [f"input [{w - 1}:0] {n}" for n, w in ins.items()])
    gw = "\n".join(f"  wire [{ports[o][1] - 1}:0] g_{o}, c_{o};" for o in p.outputs)
    conn = lambda pre: ", ".join([f".{p.clock}({p.clock})"] + ([f".{p.reset}(rst)"] if p.reset else []) + [f".{n}({n})" for n in ins] + [f".{o}({pre}_{o})" for o in p.outputs])
    active = 0 if p.reset_active_low else 1
    cmp = " && ".join(f"(g_{o} === c_{o})" for o in p.outputs)
    rst = f"  wire rst = (cyc < {p.reset_cycles}) ? 1'b{active} : 1'b{1 - active};\n" if p.reset else ""
    return (f"module formal_top({decl});\n  reg [15:0] cyc = 0;\n  always @(posedge {p.clock}) if (cyc != 16'hFFFF) cyc <= cyc + 1;\n{rst}{gw}\n"
            f"  gold g ({conn('g')});\n  gate c ({conn('c')});\n  always @* if (cyc >= {p.reset_cycles}) assert({cmp});\nendmodule\n")


def _seq(p: Problem, ref: list[Path], cand: list[Path], timeout: int) -> FormalResult:
    sby = tools.which("sby")
    if not sby:
        return FormalResult(UNKNOWN, "sby", "SymbiYosys (sby) not found: NOT RUN", 0.0)
    t0 = time.time()
    for mode, engine, depth in (("prove", "abc pdr", None), ("bmc", "abc bmc3", p.formal_depth)):
        with tempfile.TemporaryDirectory(prefix="hb_sby_") as td:
            td = Path(td)
            (td / "formal_top.v").write_text(_seq_wrapper(p))
            body = _load_script(p, ref, cand, True) + "design -copy-from gold_d -as gold gold\ndesign -copy-from gate_d -as gate gate\nread_verilog -formal -sv formal_top.v\nprep -top formal_top\n"
            files = {"formal_top.v": None}
            (td / "eq.sby").write_text(f"[options]\nmode {mode}\n" + (f"depth {depth}\n" if depth else "") + f"\n[engines]\n{engine}\n\n[script]\n{body}\n[files]\nformal_top.v\n")
            rc, text = tools.run([sby, "-f", "eq.sby"], td, timeout)
        dt = time.time() - t0
        # the ABC engines can report FAIL without a trace file, which sby then flags as an error: the engine's own verdict line is authoritative
        passed = "DONE (PASS" in text or re.search(r"engine_0 \(abc .*?\) returned pass", text)
        failed = "DONE (FAIL" in text or re.search(r"engine_0 \(abc .*?\) returned FAIL", text)
        if passed and not failed:
            if mode == "prove":
                return FormalResult(EQUIVALENT, "sby abc pdr", "unbounded proof from the reset state", dt)
            return FormalResult(BOUNDED, "sby abc bmc3", f"no difference within {depth} cycles after reset (NOT a proof)", dt)
        if failed:
            return FormalResult(DIFFERENT, f"sby {engine}", "outputs differ after reset (no trace kept; see the simulation feedback for the failing vectors)", dt)
    return FormalResult(UNKNOWN, "sby", f"no verdict: {text.strip()[-300:]}", time.time() - t0)


def _exhaustive(p: Problem, candidate: list[Path], sim_timeout: int = 30) -> FormalResult:
    """Complete proof by simulating EVERY input (only for combinational problems with few input bits). Used when the SAT proof gives no verdict; the SAT path is validated against it in the tests."""
    from . import sim
    t0 = time.time()
    n = sum(p.data_inputs.values())
    cols, shift = {}, 0
    for name, w in p.data_inputs.items():
        cols[name] = [(v >> shift) & ((1 << w) - 1) for v in range(1 << n)]
        shift += w
    ref, got = sim.simulate(p, p.reference_files, cols, sim_timeout), sim.simulate(p, candidate, cols, sim_timeout)
    ok, tot, bad = sim.compare(p, cols, ref, got, limit=1)
    if ok == tot:
        return FormalResult(EQUIVALENT, "exhaustive simulation", f"all {tot} input combinations equal the reference (X in the reference = don't care)", time.time() - t0)
    return FormalResult(DIFFERENT, "exhaustive simulation", "a counterexample exists", time.time() - t0, {k: str(v) for k, v in bad[0].inputs.items()})


def check(p: Problem, candidate: list[Path], timeout: int = 120) -> FormalResult:
    if not tools.which("yosys"):
        return FormalResult(UNKNOWN, "yosys", "yosys not found: NOT RUN", 0.0)
    try:
        r = _comb(p, p.reference_files, candidate, timeout) if p.kind == "comb" else _seq(p, p.reference_files, candidate, timeout)
        if r.status == UNKNOWN and p.kind == "comb" and sum(p.data_inputs.values()) <= p.exhaustive_max_input_bits:
            e = _exhaustive(p, candidate)                                # SAT gave no verdict (timeout/odd netlist): enumerate all inputs instead
            e.detail += f" (SAT proof had no verdict: {r.detail[:80]})"
            return e
        return r
    except Exception as e:
        return FormalResult(UNKNOWN, "formal", f"{type(e).__name__}: {e}", 0.0)
