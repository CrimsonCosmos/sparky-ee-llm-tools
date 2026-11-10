"""The scorer must not be foolable: cheats and near misses must lose reward at the stage designed to catch them, and equivalent designs must be SOLVED."""
import pytest

from hdlbench import problem, score, sim, tools

pytestmark = pytest.mark.skipif(not (tools.which("yosys") and tools.which("iverilog") and tools.which("sby")), reason="yosys/iverilog/sby not available")
CFG = score.RewardConfig()


def ev(name, text, **kw):
    return score.evaluate(problem.get(name), text, CFG, **kw)


def ref(name):
    return (problem.get(name).dir / "reference.v").read_text()


def test_reference_is_solved_with_full_stage_credit():
    s = ev("popcount8", ref("popcount8"))
    assert s.solved and s.reward == pytest.approx(0.95)               # quality = 0.5 of its weight when equal to the reference
    assert all(s.stages[n].status == "pass" for n in score.STAGES)


def test_hardcoded_visible_vectors_pass_visible_but_lose_heldout_and_formal():
    p = problem.get("add8")
    v = sim.vectors(p, "visible")
    cases = "\n".join(f"      {{8'd{v['a'][i]}, 8'd{v['b'][i]}, 1'd{v['cin'][i]}}}: {{cout, sum}} = 9'd{v['a'][i] + v['b'][i] + v['cin'][i]};" for i in range(len(v["a"])))
    cheat = f"module add8(input [7:0] a, input [7:0] b, input cin, output reg [7:0] sum, output reg cout);\n  always @* begin\n    {{cout, sum}} = 9'd0;\n    case ({{a, b, cin}})\n{cases}\n      default: ;\n    endcase\n  end\nendmodule"
    s = ev("add8", cheat)
    assert s.stages["visible"].fraction == 1.0                         # fools the visible tests...
    assert s.stages["heldout"].fraction < 0.2                          # ...but not the held-out set
    assert s.stages["formal"].status == "not_run" and not s.solved and s.reward < 0.5


def test_rare_input_bug_passes_tests_but_formal_refutes_it():
    bug = ref("add8").replace("assign {cout, sum} = a + b + cin;", "assign {cout, sum} = (a == 8'hA5 && b == 8'h5A && cin) ? 9'd0 : a + b + cin;")
    s = ev("add8", bug)
    assert s.stages["visible"].fraction == 1.0
    assert s.stages["formal"].status == "fail" and s.counterexample and not s.solved
    assert s.reward < 0.6


def test_equivalent_but_different_design_is_solved_and_quality_is_relative_to_the_reference():
    shift_add = "module mulc45(input [7:0] x, output [13:0] y); assign y = (x << 5) + (x << 3) + (x << 2) + x; endmodule"
    s = ev("mulc45", shift_add)
    assert s.solved and s.metrics and s.ratios["luts"] is not None
    assert s.stages["quality"].fraction > 0


def test_latch_in_a_combinational_design_fails_synthesizable():
    latch = "module popcount8(input [7:0] in, output reg [3:0] count); always @* if (in[0]) count = 4'd1; endmodule"
    s = ev("popcount8", latch)
    assert s.stages["synthesizable"].status == "fail" and "latch" in s.stages["synthesizable"].detail
    assert s.stages["visible"].status == "not_run" and s.reward == pytest.approx(0.05)


def test_interface_mismatch_scores_zero():
    s = ev("add8", "module add8(input [7:0] a, input [7:0] b, output [8:0] sum); assign sum = a + b; endmodule")
    assert s.stages["interface"].status == "fail" and s.reward == 0 and "cin" in s.stages["interface"].detail


@pytest.mark.parametrize("snippet", ['initial $readmemh("answers.hex", mem);', '$fopen("x")', "`include \"ref.v\"", "initial $system(\"ls\");"])
def test_forbidden_constructs_are_rejected_before_any_tool_runs(snippet):
    s = ev("add8", ref("add8").replace("endmodule", snippet + "\nendmodule"))
    assert s.stages["interface"].status == "fail" and s.reward == 0 and "forbidden" in s.stages["interface"].detail


def test_reserved_module_names_and_missing_top_are_rejected():
    assert ev("add8", ref("add8") + "\nmodule tb; endmodule").stages["interface"].status == "fail"
    assert "no module named" in ev("add8", "module other(input a); endmodule").stages["interface"].detail


def test_syntax_error_fails_compiles_with_the_tool_message():
    s = ev("add8", "module add8(input [7:0] a, input [7:0] b, input cin, output [7:0] sum, output cout); assign sum = a + ; endmodule")
    assert s.stages["compiles"].status == "fail" and s.reward == pytest.approx(0)


def test_feedback_shows_failing_visible_vectors_but_never_heldout_vectors():
    wrong = ref("popcount8").replace("count + in[i]", "count + (in[i] & ~in[0])")          # wrong whenever bit 0 is set
    s = ev("popcount8", wrong)
    assert s.mismatches and "failing visible vectors" in s.feedback
    heldout_inputs = set(sim.vectors(problem.get("popcount8"), "heldout")["in"])
    shown = {m.inputs["in"] for m in s.mismatches}
    assert shown and not shown & heldout_inputs


def test_sequential_near_miss_is_caught_and_correct_variant_is_solved():
    good = "module gray4(input clk, input rst_n, input en, output [3:0] q); reg [3:0] n; always @(posedge clk) if (!rst_n) n <= 0; else if (en) n <= n + 4'd1; assign q = n ^ (n >> 1); endmodule"
    assert ev("gray4", good).solved
    bad = good.replace("else if (en) n <= n + 4'd1;", "else if (en) n <= (n == 4'd13) ? 4'd0 : n + 4'd1;")        # skips two states
    s = ev("gray4", bad)
    assert not s.solved and (s.stages["heldout"].fraction < 1.0 or s.stages["formal"].status == "fail")


# ---- reward-hacking regressions (found by thinking about what a proposer with a different goal could do) ----
def test_initial_blocks_and_declaration_initialisers_are_forbidden():
    good = "module gray4(input clk, input rst_n, input en, output [3:0] q); reg [3:0] n; always @(posedge clk) if (!rst_n) n <= 0; else if (en) n <= n + 4'd1; assign q = n ^ (n >> 1); endmodule"
    assert ev("gray4", good).solved
    for bad in (good.replace("reg [3:0] n;", "reg [3:0] n = 0;"), good.replace("reg [3:0] n;", "reg [3:0] n; initial n = 0;")):
        s = ev("gray4", bad)
        assert s.stages["interface"].status == "fail" and "forbidden" in s.stages["interface"].detail and s.reward == 0


def test_a_design_that_ignores_reset_at_run_time_is_caught_by_heldout_resets_and_by_formal():
    ref_like = "module gray4(input clk, input rst_n, input en, output [3:0] q); reg [3:0] n; always @(posedge clk) if (!rst_n && !en) n <= 0; else if (en) n <= n + 4'd1; assign q = n ^ (n >> 1); endmodule"
    # resets only when en is low: identical after power-up reset (en low during the initial reset cycles is not guaranteed), differs when reset is pulsed with en high
    s = ev("gray4", ref_like)
    assert not s.solved
    assert s.stages["heldout"].fraction < 1.0 or s.stages["formal"].status == "fail"


def test_block_ram_use_is_visible_in_the_metrics_and_withholds_quality_credit(tmp_path):
    from hdlbench import metrics
    f = tmp_path / "ram.v"
    f.write_text("module ram(input clk, input we, input [7:0] a, input [7:0] b, input [7:0] d, output reg [7:0] q); reg [7:0] mem [0:255]; always @(posedge clk) begin if (we) mem[a] <= d; q <= mem[b]; end endmodule")
    p = problem.Problem(name="ram", dir=tmp_path, top="ram", kind="seq", spec_text="", reference_files=[f], clock="clk")
    m = metrics.measure(p, [f], pnr=False)
    assert m.other and any("RAM" in k for k in m.other), m.cells          # the memory maps to block RAM, which the LUT count alone would not show
    assert any("NOT comparable" in n for n in m.notes)


def test_dead_latch_on_an_unused_loop_variable_is_not_a_latch_failure_but_a_real_latch_is():
    dead = ("module popcount8(input [7:0] in, output reg [3:0] count); integer i;\n  always @* begin count = 0; if (in[7]) begin for (i = 0; i < 8; i = i + 1) count = count + in[i]; end else begin count = in[0] + in[1] + in[2] + in[3] + in[4] + in[5] + in[6]; end end\nendmodule")
    s = ev("popcount8", dead)
    assert s.stages["synthesizable"].status == "pass", s.stages["synthesizable"].detail       # 'i' is only assigned in one branch (dead latch, drives nothing)
    real = "module popcount8(input [7:0] in, output reg [3:0] count); always @* if (in[0]) count = 4'd1; endmodule"
    assert ev("popcount8", real).stages["synthesizable"].status == "fail"
