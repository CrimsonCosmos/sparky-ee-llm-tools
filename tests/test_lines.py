"""The scikit-rf adapter against Pozar's textbook microstrip design equation (independent of scikit-rf's own formulas)."""
import math

import pytest

from sparky.analysis import lines
from sparky import krt

pytestmark = pytest.mark.skipif(not lines.available(), reason="scikit-rf not installed")


def test_microstrip_agrees_with_pozars_design_equation():
    er = 4.2
    a = 50 / 60 * math.sqrt((er + 1) / 2) + (er - 1) / (er + 1) * (0.23 + 0.11 / er)
    w_over_d = 8 * math.exp(a) / (math.exp(2 * a) - 2)                      # Pozar, Microwave Engineering eq. 3.197
    z0, er_eff = lines.microstrip(w_over_d * 0.5, 0.5, 0.0, er)
    assert z0 == pytest.approx(50.0, rel=0.02) and 1 < er_eff < er


def test_width_solver_inverts_and_refuses_the_impossible():
    w = lines.microstrip_width_mm(50.0, 0.2, 0.035, 4.5)
    assert lines.microstrip(w, 0.2, 0.035, 4.5)[0] == pytest.approx(50.0, abs=0.05)
    with pytest.raises(ValueError):
        lines.microstrip_width_mm(1.0, 0.2, 0.035, 4.5)


def test_our_ipc2141_cross_check_and_scikit_rf_agree_within_the_formulas_stated_accuracy():
    # krt.microstrip_z0 is the quick IPC-2141 check used on routed boards: it must stay within a few percent of the library
    for w in (0.2, 0.3, 0.4):
        assert krt.microstrip_z0(w, 0.2, 0.035, 4.5) == pytest.approx(lines.microstrip(w, 0.2, 0.035, 4.5)[0], rel=0.08)


def test_coplanar_returns_a_physical_line():
    z0, er_eff = lines.coplanar(0.3, 0.2, 4.5, 1.6)
    assert 30 < z0 < 200 and 1 < er_eff < 4.5
