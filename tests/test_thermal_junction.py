"""Junction temperature arithmetic (pure function: runs without pcb-analysis)."""
import pytest

from sparky.analysis import thermal


def test_junction_temperature_is_ambient_plus_board_rise_plus_p_theta_and_never_guessed():
    tj = thermal.junction_temperatures({"U1": 0.5, "U2": 0.2}, {"U1": 12.0, "U2": 3.0}, {"U1": 40.0}, 25.0)
    assert tj["U1"] == pytest.approx(25.0 + 12.0 + 0.5 * 40.0)          # hand arithmetic: 57 C
    assert tj["U2"] is None                                               # no theta stored: no number
