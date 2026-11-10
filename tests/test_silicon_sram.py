"""An OpenRAM SRAM macro from the PDK inside a parent design, through the flow. What is checked, and what is not: STA re-run agrees with the flow; KLayout DRC re-run on the KLayout-streamed GDS runs with the PDK's own
rule-group switches and CAN FAIL (an injected sub-minimum-width metal shape is detected); LVS is NOT RUN for this macro (Magic cannot read OpenRAM GDS) and must say so."""
import subprocess
from pathlib import Path

import pytest

from silicon import flow, macros, signoff, toolchain
from silicon.spec import SiliconSpec

SRAM = "sky130_sram_1kbyte_1rw1r_32x256_8"
TOP = """module sram_top (input clk, input rst_n, input csb, input web, input [3:0] wmask, input [7:0] addr, input [31:0] din, output reg [31:0] dout);
  reg csb_r, web_r; reg [3:0] wm_r; reg [7:0] a_r; reg [31:0] d_r; wire [31:0] q0, q1;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) begin csb_r <= 1; web_r <= 1; wm_r <= 0; a_r <= 0; d_r <= 0; dout <= 0; end
    else begin csb_r <= csb; web_r <= web; wm_r <= wmask; a_r <= addr; d_r <= din; dout <= q0 ^ q1; end
  sky130_sram_1kbyte_1rw1r_32x256_8 u_sram (.clk0(clk), .csb0(csb_r), .web0(web_r), .wmask0(wm_r), .addr0(a_r), .din0(d_r), .dout0(q0), .clk1(clk), .csb1(csb_r), .addr1(a_r), .dout1(q1));
endmodule
"""
needs = pytest.mark.skipif(not toolchain.available(), reason="silicon tools not configured")


def test_pdk_drc_defines_are_read_from_the_pdk_config():
    if not toolchain.pdk_root():
        pytest.skip("no PDK")
    r = flow.FlowResult(False, None, 0.0, resolved={"PDK_ROOT": str(toolchain.pdk_root()), "PDK": "sky130A"})
    d = signoff._pdk_drc_defines(r)
    assert d and d.get("beol") == "1" and d.get("feol") == "1"


@needs
def test_pdk_sram_view_is_assembled_from_files():
    mv = macros.from_pdk(SRAM, toolchain.pdk_root(), "sky130A", "sky130_sram_macros")
    assert mv.size_um == (479.78, 397.5) and macros.lef_power_pins(mv.lef[0]) == (["vccd1"], ["vssd1"]) and mv.lib.keys() == {"*"} and "ONE Liberty corner" in mv.source


@needs
@pytest.mark.slow
def test_sram_macro_in_a_parent_design(tmp_path):
    pdk = toolchain.pdk_root()
    mv = macros.from_pdk(SRAM, pdk, "sky130A", "sky130_sram_macros")
    mv = macros.relax_max_transition(mv, 0.06, tmp_path / "lib")           # RSZ-0090: the macro's 0.04 ns input limit is tighter than any library driver (best achievable 0.043 ns)
    top = tmp_path / "sram_top.v"
    top.write_text(TOP)
    spec = SiliconSpec(design_name="sram_top", verilog_files=[top], clock_port="clk", clock_period_ns=25.0, pdk="sky130A", die_area_um=(0, 0, 700, 650), reset_port="rst_n", extra=macros.openram_flow_extra())
    spec = macros.with_macros(spec, {mv.name: (mv, {"u_sram": (100, 100, "N")})}, "VPWR", "VGND")
    r = flow.run_flow(spec, tmp_path / "run", tag="s")
    assert r.ok, (r.failed_step, r.errors[:3])
    gds = next((r.run_dir / "final" / "klayout_gds").glob("*.gds"))
    clean = signoff.rerun_klayout_drc(r, gds=gds)
    assert clean.status == "pass" and "sky130A_mr.drc" in clean.detail
    # the check can fail: inject a 0.05 um wide metal-1 sliver (min width is 0.14 um)
    bad = tmp_path / "bad.gds"
    code = ("import sys, klayout.db as kdb\nly = kdb.Layout(); ly.read(sys.argv[1]); top = ly.top_cell()\n"
            "top.shapes(ly.layer(68, 20)).insert(kdb.Box(5000, 5000, 5050, 6000)); ly.write(sys.argv[2])\n")
    subprocess.run([toolchain.python(), "-c", code, str(gds), str(bad)], check=True, env=toolchain.env())
    broken = signoff.rerun_klayout_drc(r, gds=bad)
    assert broken.status == "fail" and broken.value >= 1
    sta = signoff.rerun_sta(r)
    agree = signoff.compare(signoff.from_metrics(r), sta)
    assert all(v.status == "pass" for v in agree if v.check.startswith("agreement.timing")), [(v.check, v.detail) for v in agree]
    lvs = [v for v in signoff.from_metrics(r) if v.check == "lvs.netgen"][0]
    assert lvs.status == "not_run"                                      # LVS is honestly NOT RUN for an OpenRAM macro
