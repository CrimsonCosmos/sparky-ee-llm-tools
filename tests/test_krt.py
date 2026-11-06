"""KiCadRoutingTools stage (differential pair, length group, BGA fanout) on a real board: place -> KRT -> KiCad DRC. Skew/spread are RE-MEASURED from the routed copper
(tracks + via barrels), never taken from the tool's own report. Skipped when KiCadRoutingTools (with its Rust router built) is not available."""
import pytest

from sparky import krt
from sparky.constraints import DiffPair, Fanout, LengthGroup
from sparky.footprints import kicad_available, resolve_footprint
from sparky.model import Circuit, PartDef, PinDef
from sparky.toolchain import kicad_has

BGA = "Package_BGA:BGA-100_11.0x11.0mm_Layout10x10_P1.0mm_Ball0.5mm_Pad0.4mm_NSMD"
HDR = "Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical"


def make_circuit():
    c = Circuit("krt_test")
    c.add(PartDef(mpn="BGA100", category="ic", footprint=BGA, pins=[PinDef(p.number, p.number, "bidir") for p in resolve_footprint(BGA).pads]), ref="U1")
    for ref in ("J1", "J2"):
        c.add(PartDef(mpn="HDR10", category="connector", footprint=HDR, pins=[PinDef(p.number, p.number, "passive") for p in resolve_footprint(HDR).pads]), ref=ref)
    for i, (net, ball) in enumerate({"S1": "A5", "S2": "A7", "S3": "B1", "S4": "C1"}.items(), 3):
        c.connect(net, f"U1.{ball}", f"J1.{i}")
    c.connect("DATA_P", "J1.1", "J2.1")
    c.connect("DATA_N", "J1.2", "J2.2")
    c.connect("GND", "U1.D4", "U1.D5", "U1.E4", "U1.E5", "J1.10", "J2.10")
    c.set_net("GND", role="ground")
    return c


pytestmark = [pytest.mark.slow, pytest.mark.skipif(not (krt.available() and kicad_available() and kicad_has("pcb_drc")), reason="needs KiCadRoutingTools (SPARKY_KRT), KiCad >= 8")]


def test_pair_group_and_fanout_are_routed_and_re_measured():
    from sparky.pcb.flow import BoardSpec, build_board
    c = make_circuit()
    c.constrain(Fanout("U1"))
    c.constrain(DiffPair("DATA_P", "DATA_N", width_mm=0.2, gap_mm=0.2, skew_mm=0.1))
    c.constrain(LengthGroup(["S1", "S2", "S3"], tolerance_mm=1.0))
    res = build_board(c, BoardSpec(layers=4, time_budget_s=300), log=None)
    by = {r["name"]: r for r in res.routing_checks}
    assert not res.drc.unconnected and not res.drc.errors, res.drc.summary()           # fanout stayed inside the standard fab tier (no via-in-pad)
    pair = by["DATA_P/DATA_N"]
    assert pair["lengths_mm"][0] > 5 and pair["lengths_mm"][1] > 5                       # really routed (a 0.0 length would mean nothing was measured)
    assert pair["ok"] and pair["kicad"] is None                                          # KiCad's own DRC rule for the pair was not violated
    assert pair["skew_mm"] <= 0.1 + 0.05                                                 # our centre-line cross-check agrees to within a few hundredths of a mm
    grp = by["S1+S2+S3"]                                                                 # reported honestly, met or not
    assert min(grp["lengths_mm"]) > 3 and (grp["ok"] or grp["kicad"] is not None)       # unmet means KiCad flagged it, with its own measured skew


def test_a_pair_below_the_fab_limits_is_refused_with_a_reason():
    from sparky.pcb.flow import BoardSpec, build_board
    c = make_circuit()
    c.constrain(DiffPair("DATA_P", "DATA_N", width_mm=0.05, gap_mm=0.05, skew_mm=0.1))
    with pytest.raises(RuntimeError, match="below this fab profile's limits"):
        build_board(c, BoardSpec(layers=4, time_budget_s=60), log=None)


def test_net_names_must_follow_a_pairing_convention():
    with pytest.raises(ValueError):
        krt.check_pair_names("A", "B")
    krt.check_pair_names("LVDS_CLK_P", "LVDS_CLK_N")


def test_controlled_impedance_net_is_checked_against_the_independent_formula():
    from sparky.constraints import ControlledImpedance
    from sparky.pcb.flow import BoardSpec, build_board
    c = make_circuit()
    c.constrain(ControlledImpedance(["S4"], 50.0, 10.0))
    res = build_board(c, BoardSpec(layers=4, time_budget_s=200), log=None)
    row = next(r for r in res.routing_checks if r["kind"] == "impedance")
    assert row["ok"] and row["z_ohm"][0] > 42 and row["z_ohm"][1] < 58                    # IPC-2141 on the routed widths lands near 50 ohm
