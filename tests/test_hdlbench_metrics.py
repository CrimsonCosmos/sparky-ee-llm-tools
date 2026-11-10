"""Quality metrics are checked against circuits whose LUT/flop counts follow from the device (ice40 LUT4: any function of <=4 inputs is one LUT) and against ordering facts that must hold."""
from pathlib import Path

import pytest

from hdlbench import metrics, problem, tools

pytestmark = pytest.mark.skipif(not tools.which("yosys"), reason="yosys not available")


_n = [0]


def mk(tmp_path, body, top="t", kind="comb", **kw):
    _n[0] += 1
    f = tmp_path / f"{top}_{_n[0]}.v"
    f.write_text(body)
    return problem.Problem(name=top, dir=tmp_path, top=top, kind=kind, spec_text="", reference_files=[f], **kw), [f]


def test_four_input_function_is_one_lut_and_five_inputs_need_more(tmp_path):
    p, f = mk(tmp_path, "module t(input [3:0] a, output y); assign y = (a[0] & a[1]) ^ (a[2] | a[3]); endmodule")
    assert metrics.measure(p, f, pnr=False).luts == 1
    p, f = mk(tmp_path, "module t(input [4:0] a, output y); assign y = ^a; endmodule")                      # 5-input XOR cannot fit in one LUT4
    assert metrics.measure(p, f, pnr=False).luts == 2


def test_flop_count_and_depth_are_read_from_the_mapped_design(tmp_path):
    p, f = mk(tmp_path, "module t(input clk, input [3:0] d, output reg [3:0] q); always @(posedge clk) q <= d; endmodule", kind="seq", clock="clk")
    m = metrics.measure(p, f, pnr=False)
    assert m.flops == 4 and m.luts == 0
    # a chain of dependent 4-input functions is deeper than the same count of parallel ones
    chain = "module t(input [11:0] a, output y); assign y = ^a; endmodule"                                   # one 12-input XOR: needs >= 2 LUT4 levels
    par = "module t(input [11:0] a, output [2:0] y); assign y = {^a[11:8], ^a[7:4], ^a[3:0]}; endmodule"
    pc, fc = mk(tmp_path, chain)
    pp, fp = mk(tmp_path, par)
    assert metrics.measure(pc, fc, pnr=False).depth > metrics.measure(pp, fp, pnr=False).depth


def test_fewer_luts_for_a_smarter_design_and_the_ratio_to_the_reference(tmp_path):
    p = problem.get("mulc45")
    ref = metrics.measure(p, p.reference_files, pnr=False)
    shift_add = tmp_path / "m.v"
    shift_add.write_text("module mulc45(input [7:0] x, output [13:0] y); assign y = (x << 5) + (x << 3) + (x << 2) + x; endmodule")           # 45 = 32+8+4+1
    cand = metrics.measure(p, [shift_add], pnr=False)
    assert ref.luts and cand.luts
    r = metrics.ratios(cand, ref)
    assert r["luts"] == cand.luts / ref.luts                                    # ratio is exactly candidate/reference
    assert metrics.ratios(ref, ref)["luts"] == 1.0


@pytest.mark.skipif(not tools.nextpnr_ice40(), reason="yowasp-nextpnr-ice40 not installed")
def test_nextpnr_fmax_for_sequential_and_not_run_for_combinational():
    p = problem.get("lfsr8")
    m = metrics.measure(p, p.reference_files)
    assert m.fmax_mhz and 50 < m.fmax_mhz < 5000 and any("estimate" in n for n in m.notes)
    # a longer logic path between registers must not be faster
    pc = problem.get("add8")
    mc = metrics.measure(pc, pc.reference_files)
    assert mc.fmax_mhz is None and any("NOT RUN" in n for n in mc.notes)


@pytest.mark.skipif(not tools.nextpnr_ice40(), reason="yowasp-nextpnr-ice40 not installed")
def test_deeper_register_to_register_logic_has_lower_fmax(tmp_path):
    shallow = "module t(input clk, input [3:0] d, output reg q); reg [3:0] r; always @(posedge clk) begin r <= d; q <= ^r; end endmodule"
    deep = "module t(input clk, input [63:0] d, output reg q); reg [63:0] r; always @(posedge clk) begin r <= d; q <= (^r) & (&r[31:0]) | (r[63:32] > r[31:0]); end endmodule"
    ps, fs = mk(tmp_path, shallow, kind="seq", clock="clk")
    pd, fd = mk(tmp_path, deep, kind="seq", clock="clk")
    a, b = metrics.measure(ps, fs).fmax_mhz, metrics.measure(pd, fd).fmax_mhz
    assert a and b and b < a
