"""Stimulus, simulation (Icarus Verilog) and comparison against the reference, with the failing vectors kept for feedback.

Semantics: an X/Z bit in the REFERENCE output is 'undefined by the spec' (don't care); a defined reference bit must equal the candidate bit, so an X in the candidate there is a mismatch."""
from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import tools
from .problem import Problem


@dataclass
class Mismatch:
    cycle: int
    inputs: dict[str, int]
    expected: dict[str, str]
    got: dict[str, str]
    outputs: list[str]

    def text(self) -> str:
        ins = ", ".join(f"{k}={v:#x}" for k, v in self.inputs.items())
        bad = ", ".join(f"{o}: expected {self.expected[o]} got {self.got[o]}" for o in self.outputs)
        return f"cycle {self.cycle}: inputs {ins} -> {bad}"


def _corner(w: int) -> list[int]:
    full = (1 << w) - 1
    return sorted({0, full, 1, full >> 1, 1 << (w - 1)} | {1 << i for i in range(w)})


def vectors(p: Problem, which_set: str) -> dict[str, list[int]]:
    """Per data input, one value per cycle. 'visible' and 'heldout' use different seeds; small combinational problems are exhaustive (visible = even indices, held-out = odd)."""
    ins = p.data_inputs
    seed = p.visible_seed if which_set == "visible" else p.heldout_seed
    n = p.visible_vectors if which_set == "visible" else p.heldout_vectors
    rng = random.Random(seed)
    total = sum(ins.values())
    if p.kind == "comb" and total <= p.exhaustive_max_input_bits:
        allv = range(1 << total)
        pick = [v for v in allv if (v % 2 == 0) == (which_set == "visible")]
        cols, shift = {}, 0
        for name, w in ins.items():
            cols[name] = [(v >> shift) & ((1 << w) - 1) for v in pick]
            shift += w
        return cols
    cols = {name: [] for name in ins}
    if which_set == "heldout":                                              # directed corners first (only in the held-out set, so they are never visible)
        for name, w in ins.items():
            for c in _corner(w):
                for other in ins:
                    cols[other].append(c if other == name else rng.getrandbits(ins[other]))
    for _ in range(n):
        for name, w in ins.items():
            cols[name].append(rng.getrandbits(w))
    return cols


def _tb(p: Problem, top: str, ports: dict[str, tuple[str, int]], vec: dict[str, list[int]], cycles: int) -> str:
    ins, outs = sorted(vec), p.outputs
    decl = [f"reg [{ports[n][1] - 1}:0] {n};" for n in ins] + [f"wire [{ports[n][1] - 1}:0] {n};" for n in outs]
    if p.clock:
        decl.append(f"reg {p.clock} = 0;")
    if p.reset:
        decl.append(f"reg {p.reset};")
        active = 0 if p.reset_active_low else 1
    mems = [f"reg [{ports[n][1] - 1}:0] v_{n} [0:{cycles - 1}];" for n in ins]
    conns = [f".{n}({n})" for n in ins + outs] + ([f".{p.clock}({p.clock})"] if p.clock else []) + ([f".{p.reset}({p.reset})"] if p.reset else [])
    fmt = " ".join("%h" for _ in outs)
    body = ["initial begin"] + [f'$readmemh("vec_{n}.hex", v_{n});' for n in ins] + ['fd = $fopen("out.txt", "w");']
    if p.kind == "seq":
        body += [f"{p.reset} = {active};" if p.reset else "", "for (i = 0; i < " + str(p.reset_cycles) + "; i = i + 1) begin @(negedge " + p.clock + "); end",
                 "@(negedge " + p.clock + "); " + (f"{p.reset} = {1 - active};" if p.reset else "")]
        body += [f"for (i = 0; i < {cycles}; i = i + 1) begin"] + [f"{n} = v_{n}[i];" for n in ins] + [f"@(posedge {p.clock}); #1;", f'$fdisplay(fd, "{fmt}"{"".join(", " + n for n in outs)});', f"@(negedge {p.clock});", "end"]
    else:
        body += [f"for (i = 0; i < {cycles}; i = i + 1) begin"] + [f"{n} = v_{n}[i];" for n in ins] + ["#1;", f'$fdisplay(fd, "{fmt}"{"".join(", " + n for n in outs)});', "end"]
    body += ["$fclose(fd); $finish;", "end"]
    clk = [f"always #5 {p.clock} = ~{p.clock};"] if p.clock else []
    return ("`timescale 1ns/1ps\nmodule tb;\n" + "\n".join(decl + mems) + f"\n{top} dut ({', '.join(conns)});\n" + "\n".join(clk) + "\ninteger fd, i;\n" + "\n".join(body) + "\nendmodule\n")


def simulate(p: Problem, files: list[Path], vec: dict[str, list[int]], timeout: int = 120) -> list[list[str]]:
    """Rows of output values (hex text, X preserved), one row per vector, in `p.outputs` order. Raises RuntimeError with the tool message if it cannot compile/run."""
    iverilog, vvp = tools.which("iverilog"), tools.which("vvp")
    if not (iverilog and vvp):
        raise RuntimeError("iverilog/vvp not found")
    cycles = len(next(iter(vec.values()))) if vec else 1
    ports = p.ports
    with tempfile.TemporaryDirectory(prefix="hb_sim_") as td:
        td = Path(td)
        (td / "tb.v").write_text(_tb(p, p.top, ports, vec, cycles))
        for n, vals in vec.items():
            (td / f"vec_{n}.hex").write_text("\n".join(f"{v:x}" for v in vals) + "\n")
        rc, text = tools.run([iverilog, "-g2012", "-o", str(td / "s.vvp"), str(td / "tb.v"), *map(str, files)], td, timeout)
        if rc != 0:
            raise RuntimeError(f"iverilog: {text.strip()[-500:]}")
        rc, text = tools.run([vvp, str(td / "s.vvp")], td, timeout)
        out = td / "out.txt"
        if rc != 0 or not out.is_file():
            raise RuntimeError(f"simulation failed (rc={rc}): {text.strip()[-300:]}")
        rows = [ln.split() for ln in out.read_text().splitlines() if ln.strip()]
    if len(rows) != cycles:
        raise RuntimeError(f"simulation produced {len(rows)} rows for {cycles} vectors (timeout/\\$finish?): {text.strip()[-200:]}")
    return rows


def _bad(ref: str, got: str) -> bool:
    if ref.lower() == got.lower():
        return False
    return len(ref) != len(got) or any(r not in "xz" and r != g for r, g in zip(ref.lower(), got.lower()))


def compare(p: Problem, vec: dict[str, list[int]], ref_rows: list[list[str]], got_rows: list[list[str]], limit: int = 5) -> tuple[int, int, list[Mismatch]]:
    """(vectors passing, total, first `limit` mismatches)."""
    outs, ok, bad = p.outputs, 0, []
    for i, (r, g) in enumerate(zip(ref_rows, got_rows)):
        if any(_bad(a, b) for a, b in zip(r, g)):
            if len(bad) < limit:
                bad.append(Mismatch(i, {n: vec[n][i] for n in vec}, dict(zip(outs, r)), dict(zip(outs, g)), [o for o, a, b in zip(outs, r, g) if _bad(a, b)]))
        else:
            ok += 1
    return ok, len(ref_rows), bad
