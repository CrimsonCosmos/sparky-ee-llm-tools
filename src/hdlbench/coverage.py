"""Code coverage of a design under a problem's own stimulus, by Verilator's coverage instrumentation (line, branch and toggle). This measures how much of the DESIGN the stimulus exercises: a design (or reference) that is
mostly unexercised is not being checked, whatever the pass fraction says. A tool analysis, not a score: the C++ build takes ~10-20 s."""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import sim, tools
from .problem import Problem


@dataclass
class Coverage:
    line: tuple[int, int] = (0, 0)             # (hit, total)
    branch: tuple[int, int] = (0, 0)
    toggle: tuple[int, int] = (0, 0)
    uncovered: list[str] = field(default_factory=list)    # 'file:line (kind)' of points never hit (first few)
    notes: list[str] = field(default_factory=list)

    @staticmethod
    def pct(t: tuple[int, int]) -> float | None:
        return None if t[1] == 0 else 100.0 * t[0] / t[1]

    def text(self) -> str:
        f = lambda n, t: f"{n} {'n/a' if t[1] == 0 else f'{100.0 * t[0] / t[1]:.1f}% ({t[0]}/{t[1]})'}"
        return ", ".join([f("line", self.line), f("branch", self.branch), f("toggle", self.toggle)])


_ITEM = re.compile(r"C '(.*)' (\d+)$")


def parse_dat(text: str, exclude_files: tuple[str, ...] = ("tb.v", "tb.sv")) -> Coverage:
    """Verilator coverage.dat -> Coverage. Keys are fields separated by \\x01 (name) / \\x02 (value)."""
    cov = Coverage()
    tot = {"v_line": [0, 0], "v_branch": [0, 0], "v_toggle": [0, 0]}
    branch_kinds = {"if", "else", "elsif", "case"}                      # Verilator reports branch arms as line points of these kinds
    unc: list[str] = []
    for line in text.splitlines():
        m = _ITEM.match(line)
        if not m:
            continue
        fields = dict(part.split("\x02", 1) for part in m.group(1).split("\x01") if "\x02" in part)
        page = fields.get("page", "").split("/")[0]
        hier = fields.get("h", "")
        if page not in tot or fields.get("f") in exclude_files or (hier and not (hier == "tb.dut" or hier.startswith("tb.dut."))):
            continue                                                    # only the design under test, never the testbench
        pages = [page] + (["v_branch"] if page == "v_line" and fields.get("o") in branch_kinds else [])
        hit = int(m.group(2)) > 0
        for pg in pages:
            tot[pg][1] += 1
            tot[pg][0] += hit
        if not hit and len(unc) < 12 and page != "v_toggle":
            unc.append(f"{fields.get('f')}:{fields.get('l')} ({fields.get('o', page[2:])})")
    cov.line, cov.branch, cov.toggle = map(tuple, (tot["v_line"], tot["v_branch"], tot["v_toggle"]))
    cov.uncovered = unc
    return cov


def measure_vectors(p: Problem, files: list[Path], vec: dict[str, list[int]], timeout: int = 300) -> Coverage:
    """Coverage of `files` (the design under test, top = p.top) when driven by the stimulus `vec` through the problem's usual testbench."""
    verilator = tools.which("verilator")
    if not verilator:
        c = Coverage(); c.notes.append("verilator not found: coverage NOT RUN"); return c
    vcov = Path(verilator).with_name("verilator_coverage")
    cycles = len(next(iter(vec.values())))
    with tempfile.TemporaryDirectory(prefix="hb_cov_") as td:
        td = Path(td)
        (td / "tb.v").write_text(sim._tb(p, p.top, p.ports, vec, cycles))
        for n, vals in vec.items():
            (td / f"vec_{n}.hex").write_text("\n".join(f"{v:x}" for v in vals) + "\n")
        rc, text = tools.run([verilator, "--binary", "--timing", "--coverage", "-Wno-fatal", "-Wno-lint", "-Wno-style", "--top-module", "tb", "-o", "simv", str(td / "tb.v"), *map(str, files)], td, timeout)
        exe = td / "obj_dir" / "simv"
        if rc != 0 or not exe.is_file():
            c = Coverage(); c.notes.append(f"verilator build failed: {text.strip()[-300:]}"); return c
        rc, text = tools.run([str(exe)], td, timeout)
        dat = td / "coverage.dat"
        if not dat.is_file():
            c = Coverage(); c.notes.append(f"no coverage.dat produced (rc={rc})"); return c
        return parse_dat(dat.read_text())


def measure(p: Problem, files: list[Path] | None = None, which_set: str = "heldout", limit: int | None = None) -> Coverage:
    """Coverage under the problem's visible or held-out vectors (combinational problems and sequential problems with the standard testbench; problems with a custom testbench are not supported here)."""
    if p.testbench:
        c = Coverage(); c.notes.append("problem uses a custom testbench: coverage NOT RUN"); return c
    vec = sim.vectors(p, which_set)
    if limit:
        vec = {k: v[:limit] for k, v in vec.items()}
    return measure_vectors(p, files or p.reference_files, vec)
