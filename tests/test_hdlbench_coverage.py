"""Coverage measurement must discriminate: full stimulus covers the design, weak stimulus leaves named lines/branches uncovered."""
import pytest

from hdlbench import coverage, problem, tools

pytestmark = pytest.mark.skipif(not (tools.which("verilator") and tools.which("yosys")), reason="verilator/yosys not available")

DUT = """module cmp4(input [3:0] a, input [3:0] b, output reg [3:0] y);
  always @* begin
    if (a > b) y = a - b;
    else if (a == b) y = 4'd0;
    else y = b - a;
  end
endmodule
"""


def prob(tmp_path):
    f = tmp_path / "cmp4.v"
    f.write_text(DUT)
    return problem.Problem(name="cmp4", dir=tmp_path, top="cmp4", kind="comb", spec_text="", reference_files=[f]), f


def test_exhaustive_stimulus_covers_every_branch_and_equal_only_stimulus_does_not(tmp_path):
    p, f = prob(tmp_path)
    full = coverage.measure_vectors(p, [f], {"a": [i >> 4 for i in range(256)], "b": [i & 15 for i in range(256)]})
    assert full.branch[1] > 0 and full.branch[0] == full.branch[1] and full.line[0] == full.line[1]
    assert coverage.Coverage.pct(full.toggle) > 90.0                                  # 46 of 48 transitions: |a-b| can never be 15 with a rising MSB in both directions, so 100% is not expected
    weak = coverage.measure_vectors(p, [f], {"a": [3, 5, 9, 9], "b": [3, 5, 9, 9]})              # a == b always: the two other branches never run
    assert weak.branch[0] < weak.branch[1] and weak.line[0] < weak.line[1]
    assert any("cmp4.v" in u for u in weak.uncovered)
    assert coverage.Coverage.pct(weak.toggle) < coverage.Coverage.pct(full.toggle)


def test_parse_dat_ignores_the_testbench_and_counts_hits():
    dat = ("# SystemC::Coverage-3\n"
           "C '\x01f\x02d.v\x01l\x021\x01page\x02v_line/d\x01o\x02block' 5\n"
           "C '\x01f\x02d.v\x01l\x022\x01page\x02v_line/d\x01o\x02block' 0\n"
           "C '\x01f\x02d.v\x01l\x023\x01page\x02v_line/d\x01o\x02else' 4\n"
           "C '\x01f\x02tb.v\x01l\x021\x01page\x02v_line/tb\x01o\x02block' 9\n")
    c = coverage.parse_dat(dat)
    assert c.line == (2, 3) and c.branch == (1, 1) and c.uncovered == ["d.v:2 (block)"]


def test_heldout_stimulus_covers_at_least_as_much_as_visible_for_a_builtin_problem():
    p = problem.get("prienc8")
    v, h = coverage.measure(p, which_set="visible"), coverage.measure(p, which_set="heldout")
    assert h.branch[0] >= v.branch[0] and h.line[0] >= v.line[0] and h.branch[1] == v.branch[1] > 0
