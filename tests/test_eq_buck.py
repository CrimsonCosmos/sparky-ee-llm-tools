import math

from sparky.equations.buck import BuckSpec, design_buck, feedback_divider, type2a_compensation, uvlo_divider
from sparky.equations.eseries import E12, E6, nearest, round_up


def test_nearest_and_round_up():
    assert nearest(4.8e3, E12) == 4.7e3 and round_up(7.25e-6, E6) == 10e-6 and round_up(1e-6, E6) == 1e-6


def test_feedback_divider_matches_ti_tps563201_table():
    # TPS563201 datasheet Table 7-2: 3.3 V -> R1 = 33.2 k, R2 = 10 k (Vref = 0.768 V)
    r1, r2, v = feedback_divider(3.3, 0.768)
    assert (round(r1 / 1e3, 1), r2) == (33.2, 10e3) and abs(v - 3.3) < 0.03
    r1, _, _ = feedback_divider(5.0, 0.768)
    assert round(r1 / 1e3, 1) == 54.9


def test_buck_reproduces_tps54360b_example_regime():
    # TI example: 5 V / 3.5 A, fsw 600 kHz, Vin up to 60 V: calc L ~ 7.3 uH (KIND 0.3 -> nearest standard 8.2 uH), Cout >= ~29 uF
    d = design_buck(BuckSpec(8.5, 60, 5.0, 3.5, 600e3, 0.8))
    assert 7e-6 <= d.inductance <= 10e-6 and d.inductor_isat_min > 4.0
    assert d.cout_required > 20e-6 and abs(d.vout_actual - 5) < 0.1


def test_datasheet_inductor_window_is_enforced():
    d = design_buck(BuckSpec(9, 14, 3.3, 2.0, 580e3, 0.768, l_range=(2.2e-6, 4.7e-6)))
    assert 2.2e-6 <= d.inductance <= 4.7e-6 and any("clamped" in n for n in d.notes)


def test_compensation_in_datasheet_range():
    c = type2a_compensation(5.0, 3.5, 58.3e-6, 600e3, 0.8, 350e-6, 12.0)
    assert 8e3 <= c["r_comp"] <= 16e3                      # TI example: 13 k
    assert 4e-9 <= c["c_comp"] <= 10e-9                    # TI example: 6.8 nF
    assert math.isclose(c["f_pole_mod"], 1912, rel_tol=0.05)   # TI example: 1912 Hz


def test_uvlo_with_and_without_hysteresis_current():
    a = uvlo_divider(8.5, 1.2, 3.4e-6)                      # TI TPS54360B example: ~523 k / 84.5 k
    assert 4e5 < a["r_top"] < 6e5 and 7e4 < a["r_bottom"] < 1e5
    b = uvlo_divider(39, 1.4)
    assert b["r_bottom"] == 100e3 and b["v_start"] > b["v_stop"]
