"""Run a TPU RTL module (default: the systolic array) through the sky130A silicon flow and print sign-off verdicts + metrics, saving them as JSON.
usage: python tools/tpu_flow.py <version> <N> <clock_ns> <workdir> [top=tpu_array] [util_pct=40] [hold_margin_ns] [pnr_corners comma list or -] [setup_margin_ns]
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
hold_margin = float(sys.argv[7]) if len(sys.argv) > 7 else None
setup_margin = float(sys.argv[9]) if len(sys.argv) > 9 else None       # extra setup slack the resizer must reach on its (placement-level) estimate; routing adds ~1.4 ns on this design
pnr_corners = sys.argv[8].split(',') if len(sys.argv) > 8 and sys.argv[8] != '-' else None      # corners the resizer optimises (default: the nominal corner only, so the slow corner is merely checked)      # flow knob: extra hold slack the resizer must reach (ns)
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
if top == "tpu_core":    # whole core with small on-chip buffers (flop-based here; SRAM macros in a real build): controller, accumulators, fetch/compute engines + the array
    import os
    mmax, ktmax = int(os.environ.get("TPU_MMAX", 8)), int(os.environ.get("TPU_KTMAX", 4))     # on-chip buffer depth (flop based here)
    has_g = int(version[1:]) >= 9
    GK_IN = "input [15:0] cmd_gk, input [15:0] cmd_ng, " + ("input [31:0] cmd_tiles, input [31:0] cmd_outs, " if int(version[1:]) >= 10 else "") if has_g else ""
    G_OUT = "output [15:0] out_g, " if has_g else ""
    wrap = f"""module tpu_core_top(input clk, input rst_n, input cmd_valid, output cmd_ready, input [15:0] cmd_m, input [15:0] cmd_kt, input [15:0] cmd_nt, {GK_IN}input [31:0] cmd_wbase, output cmd_done,
  input abuf_we, input [15:0] abuf_waddr, input [{n * 8 - 1}:0] abuf_wdata, output mem_req_valid, input mem_req_ready, output [31:0] mem_req_addr, output [31:0] mem_req_beats,
  input mem_rvalid, output mem_rready, input [{n * 8 - 1}:0] mem_rdata, output out_valid, output [15:0] out_nt, output [15:0] out_m, {G_OUT}output [{n * 32 - 1}:0] out_data);
  tpu_core #(.N({n}), .MMAX({mmax}), .KTMAX({ktmax})) u(.*);
endmodule
"""
    (work / "wrap.v").write_text(wrap.replace("{GK_IN}", GK_IN).replace("{G_OUT}", G_OUT)); files = sorted(hw.glob("*.v")); files = [f for f in files if f.name != "tpu_multi.v"] + [work / "wrap.v"]; top = "tpu_core_top"
spec = SiliconSpec(design_name=top, verilog_files=files, clock_port="clk", clock_period_ns=clk, pdk="sky130A", core_utilization_pct=util, reset_port="rst_n", reset_active_low=True,
                   extra={"USE_SLANG": True, **({"PNR_CORNERS": pnr_corners} if pnr_corners else {}), **({"PL_RESIZER_SETUP_SLACK_MARGIN": setup_margin, "GRT_RESIZER_SETUP_SLACK_MARGIN": setup_margin} if setup_margin is not None else {}), **({"PL_RESIZER_HOLD_SLACK_MARGIN": hold_margin, "GRT_RESIZER_HOLD_SLACK_MARGIN": hold_margin} if hold_margin is not None else {})})
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
