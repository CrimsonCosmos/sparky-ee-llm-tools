"""RTL verification: Verilator lint finds seeded RTL faults; RTL-vs-gate-level equivalence passes on the real netlist and FAILS on a seeded RTL bug and on a mutated netlist."""
import re
import shutil
from pathlib import Path

import pytest

from silicon import rtl, signoff, toolchain
from silicon.signoff import FAIL, PASS
from silicon.spec import SiliconSpec

FIX = Path(__file__).parent / "silicon_fixtures"
tools = pytest.mark.skipif(not toolchain.available(), reason="silicon tools not configured (tools/silicon_setup.sh)")


def _spec(top="counter", files=("counter.v",), **kw):
    base = dict(design_name=top, verilog_files=[FIX / f for f in files], clock_port="clk", clock_period_ns=20.0, pdk="sky130A", reset_port="rst_n")
    base.update(kw)
    return SiliconSpec(**base)


@tools
@pytest.mark.parametrize("fixture,top,code", [("lint_width.v", "lint_width", "WIDTHTRUNC"), ("lint_latch.v", "lint_latch", "LATCH"),
                                              ("lint_undriven.v", "lint_undriven", "UNDRIVEN"), ("lint_unused.v", "lint_unused", "UNUSEDSIGNAL")])
def test_lint_flags_each_seeded_rtl_fault_by_its_code(fixture, top, code):
    rep = rtl.lint(_spec(top, (fixture,), clock_port="a" if top != "lint_latch" else "en", reset_port=None))
    assert code in rep.by_code, rep.text
    assert rep.verdict.status == FAIL


@tools
def test_lint_passes_clean_rtl():
    rep = rtl.lint(_spec())
    assert (rep.errors, rep.warnings) == (0, 0) and rep.verdict.status == PASS


@tools
def test_ports_come_from_the_design_and_vectors_are_reproducible():
    pts = rtl.ports(_spec())
    assert pts == {"clk": ("input", 1), "rst_n": ("input", 1), "en": ("input", 1), "q": ("output", 8)}
    a, b = rtl.random_vectors(_spec(), pts, 50, 7), rtl.random_vectors(_spec(), pts, 50, 7)
    assert a == b and rtl.random_vectors(_spec(), pts, 50, 8) != a
    assert a["rst_n"][:3] == [0, 0, 0] and set(a["rst_n"][3:]) == {1}                      # reset asserted (active low) for the first cycles, then released
    assert "clk" not in a


@tools
def test_simulation_of_the_rtl_is_the_counter_by_hand():
    s = _spec()
    pts = rtl.ports(s)
    vec = {"rst_n": [0, 0, 1, 1, 1, 1], "en": [0, 0, 1, 1, 0, 1]}
    out = rtl.simulate(s, s.verilog_files, vec, pts, 6)
    assert [int(l, 16) for l in out] == [0, 0, 1, 2, 2, 3]                                   # held in reset, then +1 on each enabled edge


@tools
def test_rtl_vs_buggy_rtl_is_a_mismatch_and_rtl_vs_itself_is_not(tmp_path):
    s = _spec()
    pts = rtl.ports(s)
    vec = rtl.random_vectors(s, pts, 100, 3)
    good = rtl.simulate(s, s.verilog_files, vec, pts, 100)
    bug = tmp_path / "bug.v"
    bug.write_text((FIX / "counter.v").read_text().replace("q + 8'd1", "q + 8'd2"))
    assert rtl.first_mismatch(good, good, ["q"]) is None
    msg = rtl.first_mismatch(good, rtl.simulate(s, [bug], vec, pts, 100), ["q"])
    assert msg and msg.startswith("cycle ") and "q differ" in msg


@pytest.mark.slow
@tools
def test_gate_level_equals_rtl_and_fails_on_a_seeded_bug_and_a_mutated_netlist(counter_run, tmp_path):
    s, r = counter_run
    s = _spec()
    assert r.ok
    ok = rtl.equivalence(s, r, cycles=300, seed=5)
    assert ok.status == PASS, ok.detail
    bug = tmp_path / "bug.v"
    bug.write_text((FIX / "counter.v").read_text().replace("q + 8'd1", "q + 8'd2"))
    assert rtl.equivalence(s, r, reference_rtl=[bug], cycles=300, seed=5).status == FAIL      # the layout is not the buggy RTL
    mutated = tmp_path / "mutated.nl.v"
    text = r.artifacts["netlist"].read_text()
    assert "sky130_fd_sc_hd__xor2_2" in text
    mutated.write_text(text.replace("sky130_fd_sc_hd__xor2_2", "sky130_fd_sc_hd__or2_2"))   # same ports (A,B,X), different function: every xor2_2 gate (a single-gate mutation was NOT detected by 300 random cycles - equivalence is only as strong as the stimulus) silently changed in the netlist
    from silicon import flow
    bad = flow.FlowResult(r.ok, r.run_dir, r.seconds, r.metrics, r.resolved, {**r.artifacts, "netlist": mutated}, r.log_path)
    m = rtl.equivalence(s, bad, cycles=300, seed=5)
    assert m.status == FAIL and "MISMATCH" in m.detail
