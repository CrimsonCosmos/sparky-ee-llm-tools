"""Differential impedance by atlc (2-D field solver) and its use on a routed pair. References: scikit-rf's single-ended microstrip (a second, independent model)
for the uncoupled limit, and monotonic physics (closer coupling lowers Zdiff)."""
import pytest

from sparky import krt
from sparky.analysis import diffpair, lines
from sparky.constraints import DiffPair
from sparky.pcb.rules import Rules

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not (diffpair.available() and lines.available()), reason="atlc and scikit-rf needed")]

W, H, T, ER = 0.3, 0.2, 0.035, 4.5


def test_uncoupled_pair_is_twice_the_single_ended_impedance_of_an_independent_model():
    z_single = lines.microstrip(W, H, T, ER)[0]
    far = diffpair.microstrip_pair(W, 6.0, H, T, ER)                       # spacing 30 x the dielectric height: coupling negligible
    assert far.z_diff_ohm == pytest.approx(2 * z_single, rel=0.04)
    assert far.z_odd_ohm == pytest.approx(far.z_even_ohm, rel=0.03)


def test_closer_spacing_lowers_the_differential_impedance():
    near, mid = diffpair.microstrip_pair(W, 0.15, H, T, ER), diffpair.microstrip_pair(W, 0.4, H, T, ER)
    assert near.z_diff_ohm < mid.z_diff_ohm
    assert near.z_odd_ohm < near.z_even_ohm                                  # odd mode is always the lower one for coupled lines


def _routed(gap: float, width: float = W) -> str:
    y = 10.0
    def seg(net, yy):
        return f'(segment (start 5 {yy}) (end 25 {yy}) (width {width}) (layer "F.Cu") (net "{net}"))'
    return f'(kicad_pcb (net 0 "") (net 1 "D_P") (net 2 "D_N") {seg("D_P", y)} {seg("D_N", y + width + gap)})'


def _rules() -> Rules:
    r = Rules(layers=2, thickness_mm=0.2 + 0.035)        # 2-layer board whose dielectric between the coppers is H
    r.dielectric_er = ER
    return r


def test_routed_pair_is_graded_against_the_target():
    r = _rules()
    measured = diffpair.microstrip_pair(W, 0.2, H, T, ER).z_diff_ohm                      # what the geometry gives
    ok = krt.check_impedance(_routed(0.2), [DiffPair("D_P", "D_N", W, 0.2, 0.1, impedance_ohm=measured)], r)[0]
    assert ok["ok"] is True and "atlc" in ok["note"] and ok["z_ohm"][0] == pytest.approx(measured, rel=0.03)
    bad = krt.check_impedance(_routed(0.2), [DiffPair("D_P", "D_N", W, 0.2, 0.1, impedance_ohm=measured * 1.3)], r)[0]
    assert bad["ok"] is False                                                             # a 30 % miss is caught


def test_pair_with_no_outer_copper_is_not_verified():
    txt = '(kicad_pcb (net 1 "D_P") (net 2 "D_N") (segment (start 0 0) (end 1 0) (width 0.2) (layer "In1.Cu") (net "D_P")))'
    row = krt.check_impedance(txt, [DiffPair("D_P", "D_N", W, 0.2, 0.1, impedance_ohm=90)], _rules())[0]
    assert row["ok"] is None and "NOT verified" in row["note"]
