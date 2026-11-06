"""ngspice vs. hand calculation of an ideal synchronous buck: ripple = (Vin-Vout)*D/(fsw*L), D = Vout/Vin; peak = Iout + ripple/2."""
import pytest

from sparky.toolchain import ngspice
from sparky.verify.spice import simulate_buck

pytestmark = pytest.mark.skipif(ngspice() is None, reason="ngspice not installed")


def test_buck_steady_state_matches_hand_calculation():
    vin, vout, iout, fsw, L, C = 14.0, 3.3, 2.0, 580e3, 4.7e-6, 44e-6
    sim = simulate_buck(vin, vout, iout, fsw, L, C, 0.0, ron=1e-3, cycles=600, settle_cycles=550, points_per_cycle=200)
    ripple = (vin - vout) * (vout / vin) / (fsw * L)                    # 0.9252 A
    assert sim.il_ripple_pp == pytest.approx(ripple, rel=0.02)
    assert sim.il_peak == pytest.approx(iout + ripple / 2, rel=0.02)
    assert sim.il_avg == pytest.approx(iout, rel=0.02)
    assert sim.vout_avg == pytest.approx(vout, rel=0.02)               # open-loop duty = Vout/Vin; small drop from switch/diode


def test_the_check_can_fail():
    # a wrong inductance (half) must change the ripple by ~2x, so a mismatch with the design equation would be caught
    a = simulate_buck(14.0, 3.3, 2.0, 580e3, 4.7e-6, 44e-6, 0.0, ron=1e-3, cycles=400, settle_cycles=350, points_per_cycle=200)
    b = simulate_buck(14.0, 3.3, 2.0, 580e3, 2.35e-6, 44e-6, 0.0, ron=1e-3, cycles=400, settle_cycles=350, points_per_cycle=200)
    assert b.il_ripple_pp / a.il_ripple_pp == pytest.approx(2.0, rel=0.05)
