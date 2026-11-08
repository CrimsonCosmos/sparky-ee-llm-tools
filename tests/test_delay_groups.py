"""Byte-lane grouping (KiCadRoutingTools' classifier), delay matching and our independent propagation-delay measurement."""
import math

import pytest

from sparky import krt
from sparky.analysis import delay, lines
from sparky.constants import C0_M_PER_S
from sparky.export.kicad import layer_z_mm
from sparky.pcb.rules import rules_for

R = rules_for(4, "jlcpcb-4l")


def _seg(net, layer, x0, x1, w=0.15, y=1.0):
    return f'(segment (start {x0} {y}) (end {x1} {y}) (width {w}) (layer "{layer}") (net "{net}"))'


def test_inner_layer_delay_is_the_tem_value_by_hand():
    z = layer_z_mm(R, 4)
    d = delay.net_delays_ps(f'(kicad_pcb {_seg("A", "In1.Cu", 0, 10)})', R, z)
    assert d["A"] == pytest.approx(10 * math.sqrt(R.dielectric_er) / C0_M_PER_S * 1e12 * 1e-3, rel=1e-9)       # 10 mm x sqrt(er) / c = 70.8 ps for er 4.5


@pytest.mark.skipif(not lines.available(), reason="scikit-rf needed")
def test_outer_layers_are_faster_than_inner_and_agree_with_the_hammerstad_value():
    z = layer_z_mm(R, 4)
    d = delay.net_delays_ps(f'(kicad_pcb {_seg("O", "F.Cu", 0, 10)} {_seg("I", "In1.Cu", 0, 10, y=3)})', R, z)
    assert d["O"] < d["I"]
    h = abs(z["In1.Cu"] - z["F.Cu"]) - (0.035 + 0.0175) / 2
    u = 0.15 / h                                                                      # Hammerstad-Jensen er_eff without the thickness term, by hand
    er_eff = (R.dielectric_er + 1) / 2 + (R.dielectric_er - 1) / 2 * (1 + 12 / u) ** -0.5
    assert d["O"] == pytest.approx(10 * math.sqrt(er_eff) / C0_M_PER_S * 1e12 * 1e-3, rel=0.04)


def test_via_barrels_count_at_the_dielectric_speed():
    z = layer_z_mm(R, 4)
    via = '(via (at 1 1) (size 0.5) (drill 0.25) (layers "F.Cu" "B.Cu") (net "V"))'
    d = delay.net_delays_ps(f"(kicad_pcb {via})", R, z)
    assert d["V"] == pytest.approx(abs(z["B.Cu"] - z["F.Cu"]) * math.sqrt(R.dielectric_er) / C0_M_PER_S * 1e12 * 1e-3, rel=1e-9)


@pytest.mark.skipif(krt.krt_dir() is None, reason="needs KiCadRoutingTools (SPARKY_KRT)")
def test_krts_classifier_groups_a_byte_lane_by_name():
    nets = [f"DDR_DQ{i}" for i in range(8)] + ["DDR_DQS0", "DDR_DM0", "DDR_A0", "DDR_A1", "SPI_MOSI", "GND"]
    groups = krt.dram_groups(nets)
    lane = next(g for g in groups if "DDR_DQ0" in g)
    assert set(lane) == {f"DDR_DQ{i}" for i in range(8)} | {"DDR_DQS0", "DDR_DM0"}          # data + strobe + mask in one lane
    assert not any("SPI_MOSI" in g or "GND" in g for g in groups)
    assert "DDR_A0" in next(g for g in groups if "DDR_A0" in g) and "DDR_DQ0" not in next(g for g in groups if "DDR_A0" in g)   # command/address is its own group


@pytest.mark.slow
@pytest.mark.skipif(not (krt.available() and lines.available()), reason="needs KiCadRoutingTools and scikit-rf")
def test_a_dram_byte_lane_is_found_and_matched_in_delay_through_the_flow():
    from sparky import kicad_backend
    from sparky.constraints import DramGroups
    from sparky.pcb.flow import BoardSpec, build_board
    from test_krt import BGA, HDR
    from sparky.footprints import resolve_footprint
    from sparky.model import Circuit, PartDef, PinDef
    c = Circuit("dram_lane")
    c.add(PartDef(mpn="BGA100", category="ic", footprint=BGA, pins=[PinDef(p.number, p.number, "bidir") for p in resolve_footprint(BGA).pads]), ref="U1")
    for ref in ("J1", "J2"):
        c.add(PartDef(mpn="HDR10", category="connector", footprint=HDR, pins=[PinDef(p.number, p.number, "passive") for p in resolve_footprint(HDR).pads]), ref=ref)
    for i, (net, ball) in enumerate({"DDR_DQ0": "A5", "DDR_DQ1": "A7", "DDR_DQ2": "B1", "DDR_DQ3": "C1", "DDR_DQS0": "B3"}.items(), 1):
        c.connect(net, f"U1.{ball}", f"J1.{i}")
    c.connect("GND", "U1.D4", "U1.D5", "U1.E4", "U1.E5", "J1.10", "J2.10")
    c.set_net("GND", role="ground")
    c.constrain(DramGroups(tolerance_mm=0.5, time_ps=5.0))
    res = build_board(c, BoardSpec(layers=6, time_budget_s=400), log=None)
    rows = [r for r in res.routing_checks if r["kind"] == "delay_group"]
    assert len(rows) == 1 and set(rows[0]["name"].split("+")) == {"DDR_DQ0", "DDR_DQ1", "DDR_DQ2", "DDR_DQ3", "DDR_DQS0"}     # the classifier's lane, not our guess
    assert rows[0]["delays_ps"] and min(rows[0]["delays_ps"]) > 20                                                            # really routed
    assert rows[0]["ok"] is (rows[0]["spread_ps"] <= 5.0)                                                                     # the verdict is the measurement against the limit
    assert not res.drc.unconnected
