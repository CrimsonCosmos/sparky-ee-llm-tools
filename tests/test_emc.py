"""EMC adapter vs Ott's small-loop far-field formula  E = 1.316e-14 * f^2 * A * I / r  (V/m, free space, f in Hz, A in m^2, I in A, r in m):
a 30 x 20 mm rectangular loop (A = 6e-4 m^2) carrying 0.1 A at 30 MHz, observed at 10 m. Measured agreement: 0.04 dB (30x20 mm) and 0.12 dB (10x6 mm); the tests require 1 dB. The field must scale linearly with current. Also: the check can fail (a loop 10x smaller must read ~20 dB lower)."""
import math

import pytest

from sparky.analysis import emc
from sparky.sexpr import Q, dump

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not emc.available(), reason="needs pcb-analysis with compiled kernels and kicad-cli")]


def loop_board(path, w_mm, h_mm, track=0.5):
    from sparky.export.kicad import _layers_def, _stackup
    from sparky.pcb.rules import Rules

    def fp(ref, x, y):
        return ["footprint", Q("T:pad"), ["layer", Q("F.Cu")], ["at", x, y], ["property", Q("Reference"), Q(ref)],
                ["pad", Q("1"), "smd", "rect", ["at", 0, 0], ["size", 1.0, 1.0], ["layers", Q("F.Cu"), Q("F.Mask")], ["net", 1, Q("N1")]]]
    seg = lambda a, b: ["segment", ["start", *a], ["end", *b], ["width", track], ["layer", Q("F.Cu")], ["net", 1]]
    pts = [(0, 0), (w_mm, 0), (w_mm, h_mm), (0, h_mm)]
    root = ["kicad_pcb", ["version", 20221018], ["generator", Q("test")], ["general", ["thickness", 1.6]], ["paper", Q("A4")],
            _layers_def(2), ["setup", _stackup(Rules(layers=2), 2)],
            ["net", 0, Q("")], ["net", 1, Q("N1")], ["net", 2, Q("DUMMY")], fp("J1", 0, 0), fp("J2", 0, h_mm)] + [seg(pts[i], pts[i + 1]) for i in range(3)]
    root.append(["segment", ["start", -3, -3], ["end", -2, -3], ["width", 0.5], ["layer", Q("B.Cu")], ["net", 2]])      # the library wants copper on every layer
    m = 5.0                                                              # board outline 5 mm beyond the loop
    corners = [(-m, -m), (w_mm + m, -m), (w_mm + m, h_mm + m), (-m, h_mm + m)]
    root += [["gr_line", ["start", *corners[i]], ["end", *corners[(i + 1) % 4]], ["stroke", ["width", 0.1], ["type", "default"]], ["layer", Q("Edge.Cuts")]] for i in range(4)]
    path.write_text(dump(root) + "\n")


def ott_dbuv(f, area, amps, r):
    return 20 * math.log10(1.316e-14 * f * f * area * amps / r * 1e6)


def field(tmp_path, w, h, amps):
    pcb = tmp_path / f"l{w}x{h}.kicad_pcb"
    loop_board(pcb, w, h)
    return emc.run_emc(pcb, emc.Source("loop", 30e6, amps, 0, 0, 0, h), distance_m=10.0, limit="CISPR32_CLASS_B", pitch_mm=0.5, pad_half_mm=0.6)


def test_matches_otts_small_loop_formula_within_1_dB(tmp_path):
    r = field(tmp_path, 30, 20, 0.1)
    assert abs(r.field_dbuv_per_m - ott_dbuv(30e6, 30e-3 * 20e-3, 0.1, 10.0)) < 1.0, (r.field_dbuv_per_m, ott_dbuv(30e6, 6e-4, 0.1, 10.0))


def test_field_is_linear_in_current_and_scales_with_loop_area(tmp_path):
    a, b = field(tmp_path, 30, 20, 0.1), field(tmp_path, 30, 20, 0.2)
    assert b.field_dbuv_per_m - a.field_dbuv_per_m == pytest.approx(6.02, abs=0.1)           # 2x current = +6.02 dB
    small = field(tmp_path, 10, 6, 0.1)                                                       # exactly 10x smaller area (60 mm^2); multiples of the pitch keep the track connected on the raster
    assert a.field_dbuv_per_m - small.field_dbuv_per_m == pytest.approx(20.0, abs=1.0)       # area 10x -> +20 dB
