"""Inner-layer (stripline) impedance by atlc against Cohn's exact zero-thickness result (a closed form independent of atlc), plus the physics a solver must respect."""
import math

import pytest
from scipy.special import ellipk

from sparky import krt
from sparky.analysis import diffpair
from sparky.constraints import ControlledImpedance, DiffPair
from sparky.pcb.rules import rules_for

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not diffpair.available(), reason="atlc needed")]

ER, B = 4.5, 0.4


def cohn(w: float, b: float, er: float) -> float:
    """Zero-thickness symmetric stripline, Cohn (1954): Z0 = (eta0 / 4 sqrt(er)) K(k) / K(k'), k = sech(pi w / 2b)."""
    k = 1 / math.cosh(math.pi * w / (2 * b))
    return 94.2478 / math.sqrt(er) * ellipk(k * k) / ellipk(1 - k * k)


@pytest.mark.parametrize("w", [0.08, 0.2, 0.3])
def test_atlc_stripline_matches_cohns_exact_result_for_a_thin_strip(w):
    t = 0.005
    z = diffpair.stripline(w, (B - t) / 2, (B - t) / 2, t, ER)
    assert z == pytest.approx(cohn(w, B, ER), rel=0.04)                  # atlc runs 2-3 % under: the strip has 5 um of thickness, Cohn has none


def test_thicker_copper_lowers_impedance_and_an_off_centre_strip_is_lower_than_a_centred_one():
    thin = diffpair.stripline(0.2, 0.1975, 0.1975, 0.005, ER)
    thick = diffpair.stripline(0.2, 0.1825, 0.1825, 0.035, ER)
    assert thick < thin
    off = diffpair.stripline(0.2, 0.08, 0.3150, 0.005, ER)               # same plane spacing, strip nearer the lower plane
    assert off < thin


def test_uncoupled_stripline_pair_is_twice_the_single_ended_impedance():
    single = diffpair.stripline(0.12, 0.1825, 0.1825, 0.035, ER)
    far = diffpair.stripline_pair(0.12, 1.6, 0.1825, 0.1825, 0.035, ER)
    assert far.z_diff_ohm == pytest.approx(2 * single, rel=0.05)
    near = diffpair.stripline_pair(0.12, 0.12, 0.1825, 0.1825, 0.035, ER)
    assert near.z_diff_ohm < far.z_diff_ohm and near.z_odd_ohm < near.z_even_ohm


def _board(layer: str, width: float, gap: float | None = None) -> str:
    def seg(net, y):
        return f'(segment (start 5 {y}) (end 25 {y}) (width {width}) (layer "{layer}") (net "{net}"))'
    if gap is None:
        return f'(kicad_pcb (net 0 "") (net 1 "SIG") {seg("SIG", 10)})'
    return f'(kicad_pcb (net 0 "") (net 1 "D_P") (net 2 "D_N") {seg("D_P", 10)} {seg("D_N", 10 + width + gap)})'


def test_inner_layer_net_is_graded_through_check_impedance():
    r = rules_for(4, "jlcpcb-4l")
    from sparky.export.kicad import layer_z_mm
    z = layer_z_mm(r, 4)
    order = sorted(z.items(), key=lambda kv: kv[1])
    hb, ha, tl = krt._plane_gaps("In1.Cu", order, r)
    expect = diffpair.stripline(0.15, hb, ha, tl, r.dielectric_er)
    ok = krt.check_impedance(_board("In1.Cu", 0.15), [ControlledImpedance(["SIG"], expect, 10.0)], r)[0]
    assert ok["ok"] is True and "stripline" in ok["note"] and ok["z_ohm"][0] == pytest.approx(expect, abs=0.1)
    assert krt.check_impedance(_board("In1.Cu", 0.15), [ControlledImpedance(["SIG"], expect * 1.4, 10.0)], r)[0]["ok"] is False


def test_inner_layer_pair_is_graded_as_stripline():
    r = rules_for(4, "jlcpcb-4l")
    row = krt.check_impedance(_board("In1.Cu", 0.15, 0.2), [DiffPair("D_P", "D_N", 0.15, 0.2, 0.1, impedance_ohm=100)], r)[0]
    assert row["ok"] in (True, False) and "stripline" in row["note"] and 60 < row["z_ohm"][0] < 160
