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
(d / "wrap.v").write_text(f"""module top(input clk, input rst_n, input wload, input [{lg-1}:0] wl_idx, input [{n*8-1}:0] w_row, input [{n*8-1}:0] a_vec, input a_valid, input a_first, output [{n*32-1}:0] y_vec, output y_valid);
  tpu_array #(.N({n})) u(.*);
endmodule
""")
env = toolchain.env()
booth = "-booth" if "booth" in sys.argv else ""
ys = f"""read_verilog -sv {hw}/tpu_pe.v {hw}/tpu_array.v {d}/wrap.v
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
