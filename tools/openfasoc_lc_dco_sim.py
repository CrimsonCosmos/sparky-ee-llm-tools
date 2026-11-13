"""Simulate OpenFASOC's LC digitally-controlled-oscillator core (cross-coupled NMOS pair + PDK inductor, 800 uA tail) with ngspice and the sky130 models, and measure it independently of the netlist's own .measure lines.
usage: python tools/openfasoc_lc_dco_sim.py <OpenFASOC checkout> <workdir>
The netlists in generators/lc-dco/simulations/Ngspice use relative paths into an `open_pdks` tree; this script rebuilds that layout with a symlink to the PDK found by the silicon toolchain and drops two standard-cell
includes (sky130_fd_sc_hs/ms) that the circuit does not use and the installed PDK lacks. Checks: oscillation frequency from zero crossings of v(outp)-v(outn) after 50 ns, the same at half the time step (must agree),
and the same circuit at half the tail current (reports whether it still starts up)."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from silicon import toolchain

repo, work = Path(sys.argv[1]), Path(sys.argv[2])
pdk = next(Path(toolchain.pdk_root()).rglob("sky130A/libs.ref")).parent
sim = work / "a" / "b" / "c" / "d" / "e" / "Ngspice"
(work / "PDK" / "open_pdks" / "sky130").mkdir(parents=True, exist_ok=True)
link = work / "PDK" / "open_pdks" / "sky130" / "sky130A"
if not link.exists():
    link.symlink_to(pdk)
shutil.copytree(repo / "openfasoc" / "generators" / "lc-dco" / "simulations" / "Ngspice", sim, dirs_exist_ok=True)
base = "\n".join(l for l in (sim / "LC_run.netlist").read_text().splitlines() if "sky130_fd_sc_hs" not in l and "sky130_fd_sc_ms" not in l)


def run(name, edits):
    text = base.replace("LC_run.out", f"{name}.out")
    for a, b in edits:
        assert a in text, a
        text = text.replace(a, b)
    (sim / f"{name}.netlist").write_text(text)
    subprocess.run(["ngspice", "-b", f"{name}.netlist"], cwd=sim, capture_output=True, text=True, timeout=1500)
    d = np.loadtxt(sim / f"{name}.out", skiprows=1)
    t, diff = d[:, 0], d[:, 1] - d[:, 2]
    m = t > 50e-9
    tt, dd = t[m], diff[m]
    s = np.sign(dd)
    idx = np.where((s[:-1] < 0) & (s[1:] >= 0))[0]
    if len(idx) < 3:
        return None, float(dd.max() - dd.min())
    cross = tt[idx] - dd[idx] * (tt[idx + 1] - tt[idx]) / (dd[idx + 1] - dd[idx])
    return 1e-9 / float(np.diff(cross).mean()), float(dd.max() - dd.min())


for name, edits in (("nominal", []), ("half_timestep", [(".tran 1p 100n UIC", ".tran 0.5p 100n UIC")]), ("half_tail_current", [(".param Icurr = 800u", ".param Icurr = 400u")])):
    f, pp = run(name, edits)
    print(f"{name:18s} frequency: {'no oscillation' if f is None else f'{f:.4f} GHz'}   differential peak-to-peak: {pp:.3f} V")
