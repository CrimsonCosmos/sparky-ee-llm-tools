"""Simulate any schematic with KiCad's own SPICE netlist export and ngspice (both external processes): the equivalent of KiCad's simulator, scriptable.
We write no models and no solver. Symbols carry their SPICE models in KiCad's libraries (Device:R/C/L, Simulation_SPICE sources, vendor models via Sim.* fields)."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..toolchain import kicad_cli


def available() -> bool:
    return bool(kicad_cli()) and shutil.which("ngspice") is not None


def netlist(sch_path: str | Path) -> str:
    """KiCad's SPICE netlist of the schematic (`kicad-cli sch export netlist --format spice`); library tables beside it are honoured."""
    exe = kicad_cli()
    if not exe:
        raise RuntimeError("kicad-cli not found")
    sch = Path(sch_path).resolve()
    with tempfile.TemporaryDirectory(prefix="kispice_") as td:
        for f in [sch] + [sch.parent / n for n in ("sym-lib-table", "fp-lib-table", sch.stem + ".kicad_pro")]:
            if f.exists():
                shutil.copy(f, Path(td) / f.name)
        pro = Path(td) / (sch.stem + ".kicad_pro")
        pro.exists() or pro.write_text("{}")
        out = Path(td) / "n.cir"
        r = subprocess.run([exe, "sch", "export", "netlist", "--format", "spice", "-o", str(out), str(Path(td) / sch.name)], capture_output=True, text=True, timeout=120)
        if not out.exists():
            raise RuntimeError(f"kicad-cli spice netlist failed: {(r.stderr or r.stdout).strip()[-300:]}")
        return out.read_text()


def set_source(net: str, ref: str, card: str) -> str:
    """Replace the line of source `ref` by `card` (the part after the node names), e.g. set_source(text, 'V1', 'dc 0 ac 1')."""
    pat = re.compile(rf"^({re.escape(ref)}\s+\S+\s+\S+)\s+.*$", re.M | re.I)
    if not pat.search(net):
        raise KeyError(f"no element {ref} in the netlist")
    return pat.sub(rf"\1 {card}", net, count=1)


def run(net: str, analysis: str, probes: list[str], timeout: int = 120) -> dict[str, np.ndarray]:
    """Run `analysis` (an ngspice analysis card such as '.ac dec 20 10 1meg' or '.tran 1u 5m') and return the swept variable as 'x' plus each probe
    ('v(vout)', 'i(v1)') as arrays (complex for .ac)."""
    exe = shutil.which("ngspice")
    if not exe:
        raise RuntimeError("ngspice not found")
    body = [l for l in net.splitlines() if not l.strip().lower().startswith((".end", ".title", ".ac", ".tran", ".op", ".dc"))]
    kind = analysis.split()[0].lower().lstrip(".")
    with tempfile.TemporaryDirectory(prefix="ngsim_") as td:
        out = Path(td) / "out.txt"
        probe_list = " ".join(probes)
        ctl = [".control", f"{kind} {' '.join(analysis.split()[1:])}", f"wrdata {out} {probe_list}", ".endc"]
        deck = Path(td) / "d.cir"
        deck.write_text("\n".join(["* sparky"] + body + ctl + [".end"]) + "\n")
        r = subprocess.run([exe, "-b", str(deck)], capture_output=True, text=True, timeout=timeout)
        if not out.exists():
            raise RuntimeError(f"ngspice failed: {(r.stderr or r.stdout).strip()[-400:]}")
        raw = np.loadtxt(out)
    cols = 3 if kind == "ac" else 2                      # ngspice wrdata: x, then (re, im) per probe for AC, value per probe otherwise
    res = {"x": raw[:, 0]}
    for i, p in enumerate(probes):
        c = i * cols
        res[p] = raw[:, c + 1] + 1j * raw[:, c + 2] if kind == "ac" else raw[:, c + 1]
    return res
