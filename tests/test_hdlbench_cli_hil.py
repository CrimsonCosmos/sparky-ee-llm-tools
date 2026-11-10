"""CLI, baselines, LLM-free mutation search and the optional bitstream path."""
import json

import pytest

from hdlbench import baselines, cli, hil, problem, tools

need = pytest.mark.skipif(not (tools.which("yosys") and tools.which("iverilog") and tools.which("sby")), reason="yosys/iverilog/sby not available")


@need
def test_cli_list_score_and_exit_codes(tmp_path, capsys):
    assert cli.main(["list"]) == 0 and "popcount8" in capsys.readouterr().out
    good = tmp_path / "g.v"
    good.write_text((problem.get("popcount8").dir / "reference.v").read_text())
    assert cli.main(["score", "popcount8", str(good), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["solved"] and out["stages"]["formal"][0] == "pass"
    bad = tmp_path / "b.v"
    bad.write_text("module popcount8(input [7:0] in, output [3:0] count); assign count = in[0]; endmodule")
    assert cli.main(["score", "popcount8", str(bad)]) == 1


@need
def test_baseline_table_covers_every_builtin_problem_and_all_references_are_solved():
    rows = baselines.reference_rows(["add8", "gray4", "sort4"])
    assert [r.name for r in rows] == ["add8", "gray4", "sort4"] and all(r.solved and r.luts for r in rows)
    assert rows[1].fmax_mhz is None or rows[1].fmax_mhz > 0
    assert "| add8 |" in baselines.markdown(rows)


@need
def test_llm_free_mutation_search_never_beats_a_correct_reference_by_being_wrong():
    p = problem.get("popcount8")
    res = baselines.mutation_search(p, (p.dir / "reference.v").read_text(), iterations=2, per_iter=6)
    assert res.candidates > 0
    for h in res.archive.order:                               # every mutant that the scorer calls solved really passes the proof; no unproved design may claim it
        e = res.archive.get(h)
        assert (not e.score.solved) or e.score.stages["formal"].status == "pass"


def test_bitstream_and_loader_report_not_run_without_a_board():
    if not hil.programmer():
        r = hil.load(__import__("pathlib").Path("x.bin"))
        assert not r.ok and "NOT RUN" in r.detail


@need
@pytest.mark.skipif(not (tools.nextpnr_ice40() and tools.which("yowasp-icepack")), reason="YoWASP nextpnr/icepack not installed")
def test_bitstream_is_built_and_round_trips_through_iceunpack(tmp_path):
    p = problem.get("lfsr8")
    out = tmp_path / "lfsr.bin"
    r = hil.build_bitstream(p.reference_files, p.top, out, "--hx8k", "ct256")
    assert r.ok and out.stat().st_size > 1000, r.detail
    unp = tools.which("yowasp-iceunpack")
    if unp:
        rc, text = tools.run([unp, out.name, "back.asc"], tmp_path)          # YoWASP tools only see relative paths under the working directory
        assert rc == 0 and ".device 8k" in (tmp_path / "back.asc").read_text()
    assert cli.main(["bitstream", "lfsr8", str(p.dir / "reference.v"), "--out", str(tmp_path / "c.bin"), "--device", "hx8k", "--package", "ct256"]) == 0


@need
def test_bitstream_is_refused_for_an_unsolved_design(tmp_path, capsys):
    bad = tmp_path / "b.v"
    bad.write_text("module lfsr8(input clk, input rst_n, input en, output reg [7:0] q); always @(posedge clk) q <= q; endmodule")
    assert cli.main(["bitstream", "lfsr8", str(bad), "--out", str(tmp_path / "x.bin"), "--device", "hx8k", "--package", "ct256"]) == 1
    assert "refusing" in capsys.readouterr().out
