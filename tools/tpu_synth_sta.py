"""Fast pre-layout timing estimate (register-to-register paths only; unbuffered high-fanout nets such as reset are ignored): Yosys synthesis (abc, sky130 hd) of the TPU array + OpenSTA at a chosen corner. No place-and-route, so wire load is ignored: use for RELATIVE comparison of micro-architectures only.
usage: python tools/tpu_synth_sta.py <version> <N> <corner e.g. ss_100C_1v60> <clock_ns>"""
import subprocess, sys, tempfile, re, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from silicon import toolchain

version, n, corner, clk = sys.argv[1], int(sys.argv[2]), sys.argv[3], float(sys.argv[4])
hw = Path(__file__).resolve().parents[1] / "hardware" / "tpu" / version
lib = next(Path("/home/user/tools/pdk/ciel/sky130/versions").glob(f"*/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__{corner}.lib"))
lg = max(1, math.ceil(math.log2(n)))
d = Path(tempfile.mkdtemp(prefix="tsta_"))
fpadd = "fpadd" in sys.argv
ring = "ring" in sys.argv
core = "core" in sys.argv
if fpadd:
    (d / "wrap.v").write_text("module top(input clk, input [31:0] a, input [31:0] b, output [31:0] y);\n  fp32_add_p u(.*);\nendmodule\n")
elif ring:
    (d / "wrap.v").write_text(f"""module top(input clk, input rst_n, input lwe, input [{max(1,math.ceil(math.log2(4*16)))-1}:0] laddr, input [127:0] ldata, input [{max(1,math.ceil(math.log2(4*16)))-1}:0] raddr, output [127:0] rdata, input start, input [15:0] nbc, output done, input rin_valid, input [127:0] rin_data, output rout_valid, output [127:0] rout_data);
  tpu_ring_ar_node #(.C(4), .RANK(1), .W(16), .NBCL2(4)) u(.*);
endmodule
""")
elif core:
    has_g = int(version[1:]) >= 9
    gk = "input [15:0] cmd_gk, input [15:0] cmd_ng, " + ("input [31:0] cmd_tiles, input [31:0] cmd_outs, " if int(version[1:]) >= 10 else "") if has_g else ""
    go = "output [15:0] out_g, " if has_g else ""
    (d / "wrap.v").write_text(f"""module top(input clk, input rst_n, input cmd_valid, output cmd_ready, input [15:0] cmd_m, input [15:0] cmd_kt, input [15:0] cmd_nt, {gk}input [31:0] cmd_wbase, output cmd_done,
  input abuf_we, input [15:0] abuf_waddr, input [{n * 8 - 1}:0] abuf_wdata, output mem_req_valid, input mem_req_ready, output [31:0] mem_req_addr, output [31:0] mem_req_beats,
  input mem_rvalid, output mem_rready, input [{n * 8 - 1}:0] mem_rdata, output out_valid, output [15:0] out_nt, output [15:0] out_m, {go}output [{n * 32 - 1}:0] out_data);
  tpu_core #(.N({n}), .MMAX(8), .KTMAX(4)) u(.*);
endmodule
""")
else:
  (d / "wrap.v").write_text(f"""module top(input clk, input rst_n, input wload, input [{lg-1}:0] wl_idx, input [{n*8-1}:0] w_row, input [{n*8-1}:0] a_vec, input a_valid, input a_first, output [{n*32-1}:0] y_vec, output y_valid);
  tpu_array #(.N({n})) u(.*);
endmodule
""")
env = toolchain.env()
booth = "-booth" if "booth" in sys.argv else ""
files = f"{hw.parent}/v13_fp32_add_p.v" if fpadd else f"{hw.parent}/v13_ring_allreduce.v {hw.parent}/v13_fp32_add_p.v" if ring else f"{hw}/tpu_pe.v {hw}/tpu_array.v {hw}/tpu_core.v"
ys = f"""read_verilog -sv {files} {d}/wrap.v
hierarchy -top top
synth -flatten -top top {booth}
dfflibmap -liberty {lib}
abc -D {int(clk*1000)} -liberty {lib}
opt_clean -purge
stat -liberty {lib}
write_verilog -noattr {d}/net.v
"""
(d / "s.ys").write_text(ys)
r = subprocess.run([toolchain.which("yosys"), "-q", "-l", str(d / "y.log"), str(d / "s.ys")], capture_output=True, text=True, env=env)
if r.returncode:
    print(r.stderr[-1500:]); sys.exit(1)
area = re.findall(r"Chip area for module.*?:\s*([\d.]+)", (d / "y.log").read_text())
(d / "t.tcl").write_text(f"""read_liberty {lib}
read_verilog {d}/net.v
link_design top
create_clock -name clk -period {clk} [get_ports clk]
set_input_delay 0 -clock clk [all_inputs]
set_output_delay 0 -clock clk [all_outputs]
report_checks -path_delay max -format full -from [all_registers -clock_pins] -to [all_registers -data_pins]
""")
o = subprocess.run([toolchain.which("sta"), "-no_splash", "-exit", str(d / "t.tcl")], capture_output=True, text=True, env=env).stdout
wns = re.search(r"(-?[\d.]+)\s+slack", o)
print(o[:3500] if len(sys.argv) > 5 else "", end="")
print(f"{version} N={n} {corner} clock {clk} ns: synthesized area {area[-1] if area else '?'} um^2, pre-layout worst reg-to-reg slack {wns.group(1) if wns else o[-300:]} ns")
