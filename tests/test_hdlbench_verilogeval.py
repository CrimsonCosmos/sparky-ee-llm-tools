"""VerilogEval-layout import. The synthetic test checks the conversion logic; the real-data test runs only when HDLBENCH_VERILOGEVAL_DIR points at NVlabs/verilog-eval's dataset_spec-to-rtl (not shipped here)."""
import os
from pathlib import Path

import pytest

from hdlbench import score, tools, verilogeval

pytestmark = pytest.mark.skipif(not (tools.which("yosys") and tools.which("iverilog")), reason="yosys/iverilog not available")

PROMPT = "I would like you to implement a module named TopModule with the following interface.\n - input  a\n - input  b\n - output y\n"
REF = "module RefModule (input a, input b, output y);\n  assign y = a & b;\nendmodule\n"
SEQ_REF = "module RefModule (input clk, input a, output reg q);\n  always @(posedge clk) q <= ~a;\nendmodule\n"


def test_import_comb_and_seq_and_score_them(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "P1_and_prompt.txt").write_text(PROMPT); (src / "P1_and_ref.sv").write_text(REF)
    (src / "P2_inv_prompt.txt").write_text(PROMPT); (src / "P2_inv_ref.sv").write_text(SEQ_REF)
    (src / "P3_orphan_prompt.txt").write_text(PROMPT)
    probs, skipped = verilogeval.import_directory(src, tmp_path / "out")
    assert [p.name for p in probs] == ["P1_and", "P2_inv"] and len(skipped) == 1 and "P3_orphan" in skipped[0]
    comb, seq = probs
    assert comb.kind == "comb" and comb.top == "TopModule" and seq.kind == "seq" and seq.clock == "clk" and seq.formal_mode == "off"
    good = score.evaluate(comb, "module TopModule(input a, input b, output y); assign y = ~(~a | ~b); endmodule")
    assert good.solved and good.solved_by == "formal proof"
    assert not score.evaluate(comb, "module TopModule(input a, input b, output y); assign y = a | b; endmodule").solved
    s = score.evaluate(seq, "module TopModule(input clk, input a, output reg q); always @(posedge clk) q <= ~a; endmodule")
    assert s.solved and s.solved_by.startswith("tests only") and "NOT a proof" in s.stages["formal"].detail
    bad = score.evaluate(seq, "module TopModule(input clk, input a, output reg q); always @(posedge clk) q <= a; endmodule")
    assert not bad.solved


def test_reference_without_refmodule_is_refused(tmp_path):
    (tmp_path / "x_prompt.txt").write_text("p"); (tmp_path / "x_ref.sv").write_text("module Foo(input a); endmodule")
    probs, skipped = verilogeval.import_directory(tmp_path, tmp_path / "out")
    assert not probs and "RefModule" in skipped[0]


@pytest.mark.skipif(not os.environ.get("HDLBENCH_VERILOGEVAL_DIR"), reason="HDLBENCH_VERILOGEVAL_DIR not set")
def test_real_verilogeval_files_import_and_their_references_are_solved(tmp_path):
    probs, skipped = verilogeval.import_directory(Path(os.environ["HDLBENCH_VERILOGEVAL_DIR"]), tmp_path / "out", limit=6)
    assert probs, skipped
    for p in probs:
        s = score.evaluate(p, (p.dir / "reference.v").read_text(), do_metrics=False)
        assert s.solved, f"{p.name}: {s.feedback}"
