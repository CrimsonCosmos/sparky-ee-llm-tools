"""Scan insertion with OpenROAD's own DFT commands (set_dft_config / scan_replace / execute_dft_plan) on a finished design, plus an INDEPENDENT check of the result: the chain is traced through the written netlist
and then exercised by gate-level simulation (Icarus Verilog with the PDK cell models), shifting a random pattern through and comparing with a Python model of a shift register. A DFT step whose result is not
checked this way proves nothing."""
from __future__ import annotations

import random
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import toolchain
from .flow import FlowResult
from .rtl import _run


@dataclass
class ScanResult:
    ok: bool
    netlist: Path | None = None
    chains: int = 0
    scan_cells: int = 0
    ports: dict[str, str] = field(default_factory=dict)       # role -> port name
    log: str = ""
    errors: list[str] = field(default_factory=list)


def insert_scan(result: FlowResult, outdir: Path, max_chains: int = 1) -> ScanResult:
    """Run OpenROAD scan insertion on the final ODB + SDC of a flow result; writes `<outdir>/<top>_scan.v`."""
    if not result.ok:
        return ScanResult(False, errors=["the flow did not complete"])
    openroad = toolchain.which("openroad")
    lib_map = result.resolved.get("LIB") or {}
    corner = result.resolved.get("DEFAULT_CORNER") or ""
    lib = next((p[0] for pat, p in lib_map.items() if corner.endswith(pat.lstrip("*"))), None)
    a = result.artifacts
    if not (openroad and lib and "odb" in a and "sdc" in a):
        return ScanResult(False, errors=["need openroad, the Liberty file, the final ODB and SDC"])
    outdir.mkdir(parents=True, exist_ok=True)
    top = result.resolved["DESIGN_NAME"]
    out = outdir / f"{top}_scan.v"
    tcl = outdir / "scan.tcl"
    tcl.write_text(f"read_liberty {lib}\nread_db {a['odb']}\nread_sdc {a['sdc']}\nset_dft_config -max_chains {max_chains}\nscan_replace\nreport_dft_plan -verbose\nexecute_dft_plan\nwrite_verilog {out}\nexit\n")
    rc, text = _run([openroad, "-no_splash", "-exit", str(tcl)])
    m = re.search(r"Number of chains: (\d+)", text)
    cells = [int(x) for x in re.findall(r"has (\d+) cells", text)]
    ports = {}
    if out.is_file():
        body = out.read_text()
        for role in ("scan_enable", "scan_in", "scan_out"):
            pm = re.search(rf"\b({role}_0)\b", body)
            if pm:
                ports[role] = pm.group(1)
    ok = rc == 0 and out.is_file() and bool(m) and int(m.group(1)) > 0 and len(ports) == 3
    return ScanResult(ok, out if out.is_file() else None, int(m.group(1)) if m else 0, sum(cells), ports, text, [] if ok else [text.strip()[-300:]])


def trace_chain(netlist: Path, ports: dict[str, str]) -> list[str]:
    """Instances in scan order, found by following scan_in -> SCD pins and each cell's Q output -> next cell's SCD, in the netlist text (independent of OpenROAD's own report). Returns [] if the chain cannot be traced."""
    text = netlist.read_text()
    insts = {}
    for m in re.finditer(r"\n\s*(sky130_fd_sc_hd__\w+)\s+(\S+)\s*\((.*?)\);", text, re.S):
        pins = dict(re.findall(r"\.(\w+)\(([^)]*)\)", m.group(3)))
        insts[m.group(2)] = (m.group(1), {k: v.strip() for k, v in pins.items()})
    alias: dict[str, str] = {}                                           # `assign a = b;` makes the two names one net
    for a, b in re.findall(r"\bassign\s+(\S+)\s*=\s*(\S+)\s*;", text):
        alias[a] = b

    def canon(n: str) -> str:
        seen = set()
        while n in alias and n not in seen:
            seen.add(n)
            n = alias[n]
        return n
    by_scd = {canon(pins["SCD"]): name for name, (_, pins) in insts.items() if "SCD" in pins}
    chain, net = [], canon(ports["scan_in"])
    seen = set()
    while net in by_scd and by_scd[net] not in seen:
        name = by_scd[net]
        chain.append(name)
        seen.add(name)
        net = canon(insts[name][1].get("Q", ""))
    return chain + ([f"-> {ports['scan_out']}"] if net == canon(ports["scan_out"]) else [])


def shift_test(result: FlowResult, scan: ScanResult, tie: dict[str, int] | None = None, netlist: Path | None = None, patterns: int = 3, seed: int = 1) -> tuple[bool, str]:
    """Gate-level simulation: hold the design in scan mode (scan_enable=1), shift random bits in and require them to appear at scan_out after exactly `scan_cells` clock cycles. Every other input of the design
    (resets, enables, data) must be given a constant in `tie` - the caller knows their meaning (e.g. {"rst_n": 1} releases an active-low reset); a missing one makes the test NOT RUN instead of guessing."""
    iverilog, vvp = toolchain.which("iverilog"), toolchain.which("vvp")
    models = result.resolved.get("CELL_VERILOG_MODELS") or []
    nl = netlist or scan.netlist
    if not (iverilog and vvp and models and nl):
        return False, "NOT RUN: need iverilog, the PDK cell models and the scan netlist"
    top = result.resolved["DESIGN_NAME"]
    n = scan.scan_cells
    tie = tie or {}
    clk = result.resolved.get("CLOCK_PORT") or "clk"
    header = re.search(rf"module\s+{re.escape(top)}\s*\((.*?)\);", Path(nl).read_text(), re.S)
    inputs = re.findall(r"\binput\s+(?:\[[^\]]*\]\s*)?(\w+)", Path(nl).read_text())
    others = [x for x in inputs if x not in (clk, *scan.ports.values())]
    missing = [x for x in others if x not in tie]
    if missing:
        return False, f"NOT RUN: tie constants are needed for inputs {missing}"
    rnd = random.Random(seed)
    bits = [rnd.getrandbits(1) for _ in range(n * patterns)]
    p = scan.ports
    tb = (f"`timescale 1ns/1ps\nmodule tb;\nreg clk = 0; reg {p['scan_in']} = 0; reg {p['scan_enable']} = 1; wire {p['scan_out']};\n"
          + "".join(f"reg {'[%d:0] ' % (w - 1) if w > 1 else ''}{x} = {tie[x]};\n" for x, w in ((x, int(re.search(rf"input\s+\[(\d+):0\]\s+{x}\b", Path(nl).read_text()).group(1)) + 1 if re.search(rf"input\s+\[(\d+):0\]\s+{x}\b", Path(nl).read_text()) else 1) for x in others))
          + f"{top} dut (.{clk}(clk), .{p['scan_in']}({p['scan_in']}), .{p['scan_enable']}({p['scan_enable']}), .{p['scan_out']}({p['scan_out']})" + "".join(f", .{x}({x})" for x in others) + ");\n"
          f"always #5 clk = ~clk;\ninteger i; reg [{len(bits) - 1}:0] pat = {len(bits)}'b{''.join(map(str, reversed(bits)))};\nreg [{len(bits) - 1}:0] got;\n"
          f"initial begin\n  for (i = 0; i < {len(bits)} + {n}; i = i + 1) begin\n    @(negedge clk);\n    if (i >= {n}) got[i - {n}] = {p['scan_out']};\n    {p['scan_in']} = (i < {len(bits)}) ? pat[i] : 1'b0;\n  end\n"
          f"  @(negedge clk); $display(\"GOT %b\", got); $display(\"WANT %b\", pat); $finish;\nend\nendmodule\n")
    defines = ["-DFUNCTIONAL", "-DUNIT_DELAY=#0"]
    with tempfile.TemporaryDirectory(prefix="scan_") as td:
        td = Path(td)
        (td / "tb.v").write_text(tb)
        rc, text = _run([iverilog, "-g2012", *defines, "-o", str(td / "s.vvp"), str(td / "tb.v"), str(nl), *map(str, models)], td)
        if rc != 0:
            return False, f"iverilog failed: {text.strip()[-300:]}"
        rc, text = _run([vvp, str(td / "s.vvp")], td)
    g, w = re.search(r"GOT ([01xz]+)", text), re.search(r"WANT ([01]+)", text)
    if not (g and w):
        return False, f"no result from the simulation: {text.strip()[-200:]}"
    return g.group(1) == w.group(1), f"shifted {len(bits)} bits through {n} scan cells: {'identical' if g.group(1) == w.group(1) else 'MISMATCH'}"
