"""Hierarchical flow with macros: harden a block, use it twice in a parent, and require the parent to pass sign-off with our INDEPENDENT re-runs (including hierarchical STA). A bad placement must fail."""
from pathlib import Path

import pytest

from silicon import flow, macros, signoff, toolchain
from silicon.spec import SiliconSpec

FIX = Path(__file__).parent / "silicon_fixtures"
pytestmark = [pytest.mark.slow, pytest.mark.skipif(not toolchain.available(), reason="silicon tools not configured")]


@pytest.fixture(scope="module")
def hardened(tmp_path_factory):
    d = tmp_path_factory.mktemp("hier")
    s = SiliconSpec(design_name="counter", verilog_files=[FIX / "counter.v"], clock_port="clk", clock_period_ns=20.0, pdk="sky130A", die_area_um=(0, 0, 120, 120), reset_port="rst_n",
                    extra={"DESIGN_IS_CORE": False, "FP_PDN_CORE_RING": False, "RT_MAX_LAYER": "met4", "PL_TARGET_DENSITY_PCT": 50})
    r = flow.run_flow(s, d / "counter", tag="m")
    assert r.ok, (r.failed_step, r.errors[:3])
    return d, macros.from_flow(r)


def parent(d, mv, placements, tag):
    s = SiliconSpec(design_name="top2", verilog_files=[FIX / "top2.v"], clock_port="clk", clock_period_ns=20.0, pdk="sky130A", die_area_um=(0, 0, 420, 420), reset_port="rst_n")
    return flow.run_flow(macros.with_macros(s, {"counter": (mv, placements)}, "VPWR", "VGND"), d / f"top_{tag}", tag=tag)


def test_macro_view_is_read_from_the_files_not_typed(hardened):
    _, mv = hardened
    assert mv.size_um == (120.0, 120.0) and mv.lib and mv.spef and mv.nl and mv.gds
    power, ground = macros.lef_power_pins(mv.lef[0])
    assert power == ["VPWR"] and ground == ["VGND"]


def test_parent_with_two_macros_passes_signoff_with_independent_reruns(hardened):
    d, mv = hardened
    r = parent(d, mv, {"ca": (60, 60, "N"), "cb": (230, 60, "N")}, "ok")
    assert r.ok, (r.failed_step, r.errors[:3])
    verdicts = signoff.signoff(r)
    assert signoff.clean(verdicts), [(v.check, v.status, v.detail) for v in verdicts if v.status != "pass"]
    assert {v.check for v in verdicts} >= {"agreement.timing.hold", "agreement.lvs.netgen", "agreement.drc.klayout"}
    # the macros stay macros: the parent's netlist instantiates the module twice instead of flattening its cells
    nl = r.artifacts["netlist"].read_text()
    assert nl.count("counter ca") + nl.count("counter cb") == 2


def test_overlapping_macro_placement_is_rejected(hardened):
    d, mv = hardened
    r = parent(d, mv, {"ca": (60, 60, "N"), "cb": (100, 60, "N")}, "bad")           # the two 120 um macros overlap by 80 um
    assert not r.ok or not signoff.clean(signoff.signoff(r))
