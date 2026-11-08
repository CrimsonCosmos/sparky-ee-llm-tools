"""Thermal adapter checked against physical invariants that do not depend on the solver's internals:
  * conservation: heat in == sum of source powers (heat-balance error ~ 0)
  * lower bound:  with convection only, h on both faces, the PEAK rise >= P / (2 h A)   (the uniform-spread best case)
  * linearity:    without radiation, doubling the power doubles every rise"""
import pytest

from conftest import mcu_circuit
from sparky import routing
from sparky.analysis import thermal
from sparky.export.package import write_package
from sparky.pcb.flow import BoardSpec, build_board
from sparky.toolchain import kicad_has

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not (thermal.available() and routing.available() and kicad_has("pcb_drc")),
                                                    reason="needs pcb-analysis with compiled kernels, KiCad >= 8, Java")]

H, PITCH = 10.0, 0.5            # test inputs: convection coefficient (W/m2K) and grid pitch (mm)


@pytest.fixture(scope="module")
def board(tmp_path_factory):
    res = build_board(mcu_circuit(), BoardSpec(layers=2), log=None)
    out = tmp_path_factory.mktemp("th")
    write_package(res, out, "mcu")
    half = 0.5 * min(res.board.courtyard("U1")[2] - res.board.courtyard("U1")[0], res.board.courtyard("U1")[3] - res.board.courtyard("U1")[1])
    return res, out, half


def run(board, p, emissivity):
    res, out, half = board
    return thermal.run_thermal(res.pcb_path, {"U1": p}, out / "kicad_positions.csv", ambient_c=25.0, h_w_m2k=H, emissivity=emissivity, pitch_mm=PITCH,
                               source_half_mm={"U1": half})


def test_energy_is_conserved_and_peak_respects_the_uniform_spread_bound(board):
    res = board[0]
    p = 0.5
    r = run(board, p, 0.0)
    area_m2 = res.board.width * res.board.height * 1e-6
    assert r.heat_in_w == pytest.approx(p, rel=1e-6) and abs(r.balance_error_w) < 1e-6
    assert r.peak_rise_k >= p / (2 * H * area_m2) * (1 - 1e-3)          # cannot be cooler than a perfectly uniform board


def test_rise_is_linear_in_power_without_radiation(board):
    a, b = run(board, 0.25, 0.0), run(board, 0.5, 0.0)
    assert b.peak_rise_k / a.peak_rise_k == pytest.approx(2.0, rel=0.01)


def test_radiation_lowers_the_peak(board):
    assert run(board, 0.5, 0.9).peak_rise_k < run(board, 0.5, 0.0).peak_rise_k

