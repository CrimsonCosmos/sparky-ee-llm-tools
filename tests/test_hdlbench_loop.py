"""The loop with scripted proposers (no model needed): feedback reaches the next prompt, the archive keeps everything and the best, dedup and budgets work, the held-out vectors never appear in prompts."""
import json

import pytest

from hdlbench import loop, problem, proposers, sim, tools
from hdlbench.archive import Archive

pytestmark = pytest.mark.skipif(not (tools.which("yosys") and tools.which("iverilog") and tools.which("sby")), reason="yosys/iverilog/sby not available")

WRONG = "module popcount8(input [7:0] in, output [3:0] count); assign count = in[0] + in[1] + in[2] + in[3];  endmodule"       # ignores the top nibble
GOOD = "module popcount8(input [7:0] in, output [3:0] count); assign count = in[0] + in[1] + in[2] + in[3] + in[4] + in[5] + in[6] + in[7]; endmodule"


def cfg(**kw):
    return loop.LoopConfig(candidates_per_iteration=1, workers=1, **kw)


def test_extract_verilog_takes_the_last_fenced_module():
    t = "thoughts\n```verilog\nmodule a; endmodule\n```\nfinal:\n```verilog\nmodule b(input x); endmodule\n```"
    assert "module b" in proposers.extract_verilog(t) and "module a" not in proposers.extract_verilog(t)
    assert proposers.extract_verilog("no code here") == ""
    assert "module c" in proposers.extract_verilog("module c; endmodule")


def test_loop_fixes_a_wrong_design_using_feedback_and_stops_when_solved(tmp_path):
    p = problem.get("popcount8")
    prop = proposers.ScriptedProposer([WRONG, GOOD])
    r = loop.run(p, prop, loop.Budget(max_iterations=5), cfg(optimize=False), tmp_path / "arch.jsonl")
    assert r.solved and r.stopped == "solved" and r.candidates == 2
    assert "failing visible vectors" in prop.prompts[1] and "WRONG" not in prop.prompts[1] and "in[3] + in[4]" not in prop.prompts[1]       # the second prompt carries the first design's feedback
    assert "assign count = in[0] + in[1] + in[2] + in[3];" in prop.prompts[1]
    lines = [json.loads(x) for x in (tmp_path / "arch.jsonl").read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["solved"] is False and lines[1]["solved"] is True and lines[1]["parent"] == lines[0]["hash"]
    assert r.best.score.reward > lines[0]["reward"]


def test_heldout_vectors_never_appear_in_any_prompt(tmp_path):
    p = problem.get("popcount8")
    prop = proposers.ScriptedProposer([WRONG, WRONG.replace("in[3];", "in[3] + 4'd0;"), GOOD])
    loop.run(p, prop, loop.Budget(max_iterations=3), cfg(optimize=False))
    import re
    held = set(sim.vectors(p, "heldout")["in"])
    all_shown = set()
    for pr in prop.prompts:
        shown = {int(x, 16) for x in re.findall(r"in=0x([0-9a-f]+)", pr)}
        assert not shown & held
        all_shown |= shown
    assert all_shown                                                  # the check is not vacuous: failing visible vectors were shown


def test_duplicates_are_not_reevaluated_and_budgets_stop_the_run(tmp_path):
    p = problem.get("popcount8")
    prop = proposers.ScriptedProposer([WRONG])
    r = loop.run(p, prop, loop.Budget(max_iterations=10, max_candidates=3), cfg())
    assert r.stopped == "candidate budget" and len(r.archive.order) == 1 and r.candidates == 3                  # same text three times -> one evaluation
    prop = proposers.ScriptedProposer([WRONG])
    r = loop.run(p, prop, loop.Budget(max_iterations=10, max_seconds=0), cfg())
    assert r.stopped == "time budget" and r.candidates == 0
    prop = proposers.ScriptedProposer([WRONG]); prop.usage["input_tokens"] = 10
    assert loop.run(p, prop, loop.Budget(max_iterations=10, max_tokens=5), cfg()).stopped == "token budget"


def test_optimizing_run_continues_after_the_first_proof_and_keeps_the_better_design(tmp_path):
    p = problem.get("mulc45")
    ref_like = (p.dir / "reference.v").read_text()
    shift_add = "module mulc45(input [7:0] x, output [13:0] y); assign y = (x << 5) + (x << 3) + (x << 2) + x; endmodule"
    r = loop.run(p, proposers.ScriptedProposer([ref_like, shift_add]), loop.Budget(max_iterations=2), cfg(optimize=True))
    assert r.stopped == "iteration budget" and r.candidates == 2 and r.solved
    rewards = r.archive.rewards()
    assert r.best.score.reward == max(rewards)


def test_population_prompt_lists_the_top_designs(tmp_path):
    p = problem.get("popcount8")
    prop = proposers.ScriptedProposer([WRONG, GOOD, GOOD.replace("in[7];", "in[7] + 4'd0;")])
    loop.run(p, prop, loop.Budget(max_iterations=3), cfg(strategy="population", optimize=True, top_k=2))
    assert "### Design 1" in prop.prompts[-1] and "### Design 2" in prop.prompts[-1]


def test_unavailable_proposer_stops_cleanly(tmp_path):
    def boom(prompt, n):
        raise RuntimeError("no api key")
    r = loop.run(problem.get("popcount8"), proposers.ScriptedProposer(boom), loop.Budget(max_iterations=2), cfg())
    assert r.stopped.startswith("proposer unavailable") and r.candidates == 0


def test_archive_ranking_prefers_solved_then_reward():
    a = Archive()
    from hdlbench.score import Score
    s1, s2 = Score("p", "h1", reward=0.9, solved=False), Score("p", "h2", reward=0.5, solved=True)
    a.add(1, None, "x", s1); a.add(1, None, "y", s2)
    assert a.best().score.code_hash == "h2" and [e.score.code_hash for e in a.top(2)] == ["h2", "h1"]
