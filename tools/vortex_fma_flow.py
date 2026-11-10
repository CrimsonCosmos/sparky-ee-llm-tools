"""Run the Vortex GPU's FP32 fused-multiply-add unit (VX_fma_unit, the project's portable RTL core) through the silicon flow on sky130A and print sign-off verdicts.
usage: python tools/vortex_fma_flow.py <vortex checkout> <dir with generated VX_config.vh/VX_types.vh> <workdir>
The generated headers come from Vortex's own `ci/gen_config.py` (see docs/VORTEX_PROBE.md). Needs the silicon tools (docs/SILICON_DEPENDENCIES.md). Result recorded in docs/VORTEX_PROBE.md."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from silicon import flow, signoff
from silicon.spec import SiliconSpec

vortex, hdr, work = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
V = vortex / "hw" / "rtl"
files = [V / "VX_gpu_pkg.sv", V / "fpu" / "VX_fpu_pkg.sv", V / "fpu" / "VX_fma_unit.sv", V / "fpu" / "VX_fma_unit_rtl.sv", V / "fpu" / "VX_fp_classifier.sv", V / "fpu" / "VX_fp_rounding.sv",
         V / "fpu" / "VX_fncp_unit.sv"] + sorted(V.glob("libs/*.sv"))
spec = SiliconSpec(design_name="VX_fma_unit", verilog_files=files, clock_port="clk", clock_period_ns=25.0, pdk="sky130A", core_utilization_pct=35, reset_port="reset", reset_active_low=False,
                   extra={"USE_SLANG": True, "VERILOG_INCLUDE_DIRS": [str(V), str(V / "libs"), str(V / "fpu"), str(hdr)], "VERILOG_DEFINES": ["VX_CFG_XLEN=32", "SYNTHESIS"]})
t = time.time()
r = flow.run_flow(spec, work, tag="fma", timeout=7200)
print("flow ok:", r.ok, f"{time.time() - t:.0f} s", r.failed_step or "", r.errors[:2])
if r.ok:
    for v in signoff.signoff(r):
        print(f"{v.check:28s} {v.status:8s} {v.source:10s} {v.detail[:100]}")
    for k in ("design__instance__count", "design__instance__area", "design__die__area", "design__instance__count__class:sequential_cell", "power__total", "timing__setup__ws", "timing__hold__ws"):
        print(k, r.metric(k))
