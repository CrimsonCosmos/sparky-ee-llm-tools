"""ngspice checks of generated circuits (ngspice is run as an external process; results are parsed from its ASCII output).

The buck check simulates the designed power stage in steady state (ideal complementary switches at the duty that yields Vout) and reports inductor
ripple / peak and output ripple, to compare with the design equations. It verifies the sizing, not the control loop."""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..toolchain import ngspice


@dataclass(frozen=True)
class BuckSim:
    vout_avg: float
    il_avg: float
    il_ripple_pp: float
    il_peak: float
    vout_ripple_pp: float


def simulate_buck(vin: float, vout: float, iout: float, fsw: float, inductance: float, cout: float, esr: float, *, ron: float,
                  cycles: int, settle_cycles: int, points_per_cycle: int, timeout: int = 120) -> BuckSim:
    """Open-loop steady state of a synchronous buck. `ron` is the switch on-resistance (set from the IC's Rds(on) when known)."""
    exe = ngspice()
    if not exe:
        raise RuntimeError("ngspice not found")
    t = 1.0 / fsw
    duty = vout / vin
    tstep = t / points_per_cycle
    net = f"""buck
Vin in 0 {vin}
Vg g 0 PULSE(0 1 0 {tstep} {tstep} {duty * t - tstep} {t})
Vgb gb 0 PULSE(0 1 {duty * t + 2 * tstep} {tstep} {tstep} {(1 - duty) * t - 4 * tstep} {t})
.model SW SW(Vt=0.5 Vh=0 Ron={ron} Roff=1G)
.model DBODY D(Is=1e-12 N=1 Rs=10m)
S1 in sw g 0 SW
S2 sw 0 gb 0 SW
D2 0 sw DBODY
Vl sw n1 0
L1 n1 out {inductance}
{'Rs out cs ' + str(esr) if esr > 0 else 'Rs out cs 1u'}
C1 cs 0 {cout} ic={vout}
Rload out 0 {vout / iout}
.tran {tstep} {cycles * t} uic
.control
run
wrdata __OUT__ v(out) i(Vl)
.endc
.end
"""
    with tempfile.TemporaryDirectory(prefix="spice_") as td:
        out = Path(td) / "w.txt"
        cir = Path(td) / "b.cir"
        cir.write_text(net.replace("__OUT__", str(out)))
        r = subprocess.run([exe, "-b", str(cir)], capture_output=True, text=True, timeout=timeout)
        if not out.exists():
            raise RuntimeError(f"ngspice failed: {(r.stdout + r.stderr).strip()[-400:]}")
        rows = [tuple(map(float, ln.split())) for ln in out.read_text().splitlines() if ln.strip()]
    start = settle_cycles * t
    w = [(tt, v, i) for tt, v, _, i in rows if tt >= start]
    vs, il = [x[1] for x in w], [x[2] for x in w]
    return BuckSim(sum(vs) / len(vs), sum(il) / len(il), max(il) - min(il), max(il), max(vs) - min(vs))
