"""Formal equivalence must prove equivalent-but-different designs and refute near-miss ones (each 'different' case is one that simulation of a few vectors could easily miss)."""
import pytest

from hdlbench import formal, problem, tools

pytestmark = pytest.mark.skipif(not (tools.which("yosys") and tools.which("sby")), reason="yosys/sby not available")

ADD8_RIPPLE = """module add8(input [7:0] a, input [7:0] b, input cin, output [7:0] sum, output cout);
  wire [8:0] c; assign c[0] = cin;
  genvar i; generate for (i = 0; i < 8; i = i + 1) begin : g
    assign sum[i] = a[i] ^ b[i] ^ c[i]; assign c[i+1] = (a[i] & b[i]) | (c[i] & (a[i] ^ b[i])); end endgenerate
  assign cout = c[8];
endmodule"""
ADD8_RARE_BUG = ADD8_RIPPLE.replace("assign cout = c[8];", "assign cout = c[8] ^ (a == 8'hA5 && b == 8'h5A && cin);")        # differs for 1 of 131072 inputs


def w(tmp_path, text):
    f = tmp_path / "cand.v"
    f.write_text(text)
    return [f]


def test_comb_equivalent_but_structurally_different_is_proved(tmp_path):
    r = formal.check(problem.get("add8"), w(tmp_path, ADD8_RIPPLE))
    assert r.status == formal.EQUIVALENT and r.proved


def test_comb_rare_input_bug_is_refuted_with_the_exact_counterexample(tmp_path):
    r = formal.check(problem.get("add8"), w(tmp_path, ADD8_RARE_BUG))
    assert r.status == formal.DIFFERENT
    ce = r.counterexample
    assert (int(ce["a"]), int(ce["b"]), int(ce["cin"])) == (0xA5, 0x5A, 1) and ce["gold_cout"] != ce["gate_cout"]


def test_comb_reference_dont_care_is_accepted_but_a_wrong_defined_bit_is_not(tmp_path):
    p = problem.get("prienc8")
    ok = "module prienc8(input [7:0] in, output reg [2:0] idx, output valid); assign valid = |in; integer i; always @* begin idx = 0; for (i = 0; i < 8; i = i + 1) if (in[i]) idx = i; end endmodule"
    assert formal.check(p, w(tmp_path, ok)).status == formal.EQUIVALENT
    bad = ok.replace("if (in[i]) idx = i;", "if (in[i] && in != 8'h81) idx = i;")                      # wrong for the single input 8'h81 (idx 0 instead of 7)
    assert formal.check(p, w(tmp_path, bad)).status == formal.DIFFERENT


GRAY_ALT = """module gray4(input clk, input rst_n, input en, output [3:0] q);
  reg [3:0] g; wire [3:0] b = {g[3], g[3]^g[2], g[3]^g[2]^g[1], g[3]^g[2]^g[1]^g[0]}; wire [3:0] n = b + 4'd1;
  always @(posedge clk) if (!rst_n) g <= 0; else if (en) g <= n ^ (n >> 1);
  assign q = g;
endmodule"""


def test_seq_equivalent_with_different_state_encoding_is_proved(tmp_path):
    r = formal.check(problem.get("gray4"), w(tmp_path, GRAY_ALT))
    assert r.status == formal.EQUIVALENT, r.detail


@pytest.mark.parametrize("name,edit", [
    ("gray4", GRAY_ALT.replace("if (en) g <= n ^ (n >> 1)", "if (en && g != 4'b1000) g <= n ^ (n >> 1)")),         # sticks at one state: needs 15 enabled cycles to show
    ("gray4", GRAY_ALT.replace("assign q = g;", "assign q = en ? g : g;").replace("if (!rst_n) g <= 0", "if (!rst_n) g <= 4'b0001")),   # wrong reset value
])
def test_seq_near_miss_designs_are_refuted(tmp_path, name, edit):
    r = formal.check(problem.get(name), w(tmp_path, edit))
    assert r.status == formal.DIFFERENT, r.detail


def test_seq_lfsr_wrong_tap_and_right_tap(tmp_path):
    p = problem.get("lfsr8")
    ref = (p.dir / "reference.v").read_text()
    assert formal.check(p, w(tmp_path, ref.replace("module lfsr8", "module lfsr8").replace("q[7] ^ q[5] ^ q[4] ^ q[3]", "q[3] ^ q[4] ^ q[5] ^ q[7]"))).status == formal.EQUIVALENT
    assert formal.check(p, w(tmp_path, ref.replace("q[7] ^ q[5] ^ q[4] ^ q[3]", "q[7] ^ q[5] ^ q[4] ^ q[2]"))).status == formal.DIFFERENT


SEQDET_FSM = """module seqdet(input clk, input rst_n, input d, output hit);
  reg [2:0] s; reg r;
  always @(posedge clk) begin
    if (!rst_n) begin s <= 0; r <= 0; end
    else begin r <= 0;
      case (s)
        0: s <= d ? 1 : 0;
        1: s <= d ? 1 : 2;
        2: s <= d ? 3 : 0;
        3: begin if (d) begin r <= 1; s <= 1; end else s <= 2; end
        default: s <= 0;
      endcase
    end
  end
  assign hit = r;
endmodule"""


def test_seq_fsm_implementation_of_the_pattern_detector_is_proved_and_a_different_pattern_is_not(tmp_path):
    p = problem.get("seqdet")
    r = formal.check(p, w(tmp_path, SEQDET_FSM))
    assert r.status == formal.EQUIVALENT, r.detail
    wrong = SEQDET_FSM.replace("2: s <= d ? 3 : 0;", "2: s <= d ? 3 : 2;")                           # the 1,0,0,1,0,1,1 style history now matches differently
    r = formal.check(p, w(tmp_path, wrong))
    assert r.status == formal.DIFFERENT, r.detail
