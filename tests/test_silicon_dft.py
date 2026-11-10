"""OpenROAD scan insertion on the real counter run, checked independently: the chain traced in the netlist has one cell per flop, and a gate-level shift test passes; a cut chain must FAIL the shift test."""
import re

import pytest

from silicon import dft, toolchain

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not toolchain.available(), reason="silicon tools not configured")]


def test_scan_chain_is_inserted_traced_and_shifts_data(counter_run, tmp_path):
    spec, r = counter_run
    s = dft.insert_scan(r, tmp_path)
    assert s.ok, s.errors
    assert s.chains == 1 and s.scan_cells == 8                                   # the counter has 8 flip-flops
    chain = dft.trace_chain(s.netlist, s.ports)
    assert len([c for c in chain if not c.startswith("->")]) == 8 and chain[-1].startswith("->")     # traced from the netlist text, ends at scan_out
    ok, detail = dft.shift_test(r, s, tie={"rst_n": 1, "en": 0})
    assert ok, detail
    assert "NOT RUN" in dft.shift_test(r, s)[1]                                   # untied inputs are reported, not guessed


def test_a_cut_scan_chain_fails_the_shift_test(counter_run, tmp_path):
    spec, r = counter_run
    s = dft.insert_scan(r, tmp_path)
    assert s.ok
    text = s.netlist.read_text()
    chain = dft.trace_chain(s.netlist, s.ports)
    second = [c for c in chain if not c.startswith("->")][1]
    # disconnect the second cell's scan input (tie it low): the pattern can no longer pass through
    cut = re.sub(rf"(sky130_fd_sc_hd__\w+\s+{re.escape(second)}\s*\(.*?\.SCD\()[^)]*(\))", r"\g<1>1'b0\g<2>", text, count=1, flags=re.S)
    assert cut != text
    broken = tmp_path / "cut.v"
    broken.write_text(cut)
    ok, detail = dft.shift_test(r, s, tie={"rst_n": 1, "en": 0}, netlist=broken)
    assert not ok, detail
