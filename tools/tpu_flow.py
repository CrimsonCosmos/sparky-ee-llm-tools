"""Run a TPU RTL module (default: the systolic array) through the sky130A silicon flow and print sign-off verdicts + metrics, saving them as JSON.
usage: python tools/tpu_flow.py <version> <N> <clock_ns> <workdir> [top=tpu_array] [util_pct=40]
Parameters (N) are applied by wrapping the top in a generated one-line module (no RTL edit). Needs the silicon tools (docs/SILICON_DEPENDENCIES.md)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from silicon import flow, signoff
from silicon.spec import SiliconSpec

version, n, clk, work = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), Path(sys.argv[4])
top = sys.argv[5] if len(sys.argv) > 5 else "tpu_array"
util = int(sys.argv[6]) if len(sys.argv) > 6 else 40
hw = Path(__file__).resolve().parents[1] / "hardware" / "tpu" / version
work.mkdir(parents=True, exist_ok=True)
files = [hw / "tpu_pe.v", hw / "tpu_array.v"] if top == "tpu_array" else sorted(hw.glob("*.v"))
import math
lg = max(1, math.ceil(math.log2(n)))
if top == "tpu_array":   # v2/v3 array port list; a wrapper fixes the parameter without touching the RTL
    wrap = f"""module tpu_array_top(input clk, input rst_n, input wload, input [{lg - 1}:0] wl_idx, input [{n * 8 - 1}:0] w_row, input [{n * 8 - 1}:0] a_vec, input a_valid, input a_first,
  output [{n * 32 - 1}:0] y_vec, output y_valid);
  tpu_array #(.N({n})) u(.*);
endmodule
"""
    (work / "wrap.v").write_text(wrap); files.append(work / "wrap.v"); top = "tpu_array_top"
spec = SiliconSpec(design_name=top, verilog_files=files, clock_port="clk", clock_period_ns=clk, pdk="sky130A", core_utilization_pct=util, reset_port="rst_n", reset_active_low=True,
                   extra={"USE_SLANG": True})
t = time.time()
r = flow.run_flow(spec, work, tag=f"{version}_n{n}", timeout=14400)
out = {"version": version, "N": n, "top": top, "clock_ns": clk, "flow_ok": r.ok, "seconds": round(time.time() - t), "failed_step": r.failed_step}
print("flow ok:", r.ok, f"{time.time() - t:.0f} s", r.failed_step or "", r.errors[:2])
if r.ok:
    out["signoff"] = []
    for v in signoff.signoff(r):
        print(f"{v.check:28s} {v.status:8s} {v.source:10s} {v.detail[:100]}")
        out["signoff"].append({"check": v.check, "status": v.status, "source": v.source, "detail": v.detail[:200]})
    out["metrics"] = {}
    for k in ("design__instance__count", "design__instance__area", "design__die__area", "design__instance__count__class:sequential_cell", "power__total", "timing__setup__ws", "timing__hold__ws"):
        out["metrics"][k] = r.metric(k)
        print(k, r.metric(k))
Path(work / "result.json").write_text(json.dumps(out, indent=1, default=str))
