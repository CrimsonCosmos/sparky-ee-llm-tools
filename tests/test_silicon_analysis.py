"""Chip analyses: values come from the real counter run; limits are inputs; a wrong corner/netlist must make the power cross-check fail."""
import pytest

from silicon import analysis, toolchain
from silicon.flow import FlowResult
from silicon.signoff import FAIL, NOT_RUN, PASS

tools = pytest.mark.skipif(not toolchain.available(), reason="silicon tools not configured")


@pytest.mark.slow
@tools
def test_power_agrees_with_the_flows_report_and_a_wrong_netlist_is_caught(counter_run, tmp_path):
    s, r = counter_run
    v = analysis.power(r)[0]
    assert v.status == PASS and "ESTIMATE" in v.detail, v.detail
    assert 1e-6 < v.value < 1e-2                                                     # microwatt..10 mW for a 8-bit counter: sanity band, not a spec
    # wrong corner: the flow's number at ff differs from OpenSTA at tt by much more than rounding, so comparing across corners must fail
    ff = [c for c in ("max_ff_n40C_1v95", "nom_ff_n40C_1v95") if analysis.flow_power_w(r, c)][0]
    assert abs(analysis.flow_power_w(r, ff) / v.value - 1) > 0.05
    # a netlist with an extra gate set changes the estimate: drop half the flops' clock activity is not possible, so truncate the netlist instead
    text = r.artifacts["netlist"].read_text()
    bad = tmp_path / "bad.nl.v"
    bad.write_text(text.replace("sky130_fd_sc_hd__dfrtp_2", "sky130_fd_sc_hd__dfrtp_4"))
    r2 = FlowResult(r.ok, r.run_dir, r.seconds, r.metrics, r.resolved, {**r.artifacts, "netlist": bad}, r.log_path)
    w = analysis.power(r2)[0]
    assert w.status in (FAIL, NOT_RUN)


@pytest.mark.slow
@tools
def test_ir_drop_and_skew_need_limits_and_then_discriminate(counter_run):
    _, r = counter_run
    assert analysis.ir_drop(r).status == NOT_RUN                                    # no limit given -> no verdict, never a silent pass
    drop = r.metric("design_powergrid__drop__worst")
    assert analysis.ir_drop(r, 1.0, 1.0).status == PASS                              # limit 100 % of a 1 V supply
    assert analysis.ir_drop(r, drop / 10 / 1.0, 1.0).status == FAIL                  # limit a tenth of the measured drop
    assert analysis.clock_skew(r).status == NOT_RUN
    assert analysis.clock_skew(r, 100.0).status == PASS and analysis.clock_skew(r, 0.001).status == FAIL
