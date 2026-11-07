"""Network EMC (copper port network + capacitor + source) vs Ott's small-loop formula E = 1.316e-14 f^2 A I / r on a 30 x 20 mm rectangular loop
(V trace out, G trace back, a capacitor closing the far end, the source at the near end). Also the harmonic amplitude of a trapezoid by hand."""
import math

import pytest

from sparky.analysis import emc_network as en
from sparky.sexpr import Q, dump

pytestmark_slow = pytest.mark.slow


def test_trapezoid_harmonics_by_hand():
    # ipk 2 A, duty 0.25, fsw 500 kHz (T = 2 us), rise 10 ns. At f = 1 MHz: pi f d T = pi*1e6*0.25*2e-6 = pi/2 -> sinc = 2/pi = 0.63662
    #   pi f tr = pi * 1e6 * 1e-8 = 0.0314159 -> sinc ~ 0.999836 ; |c| = 2*2*0.25*0.63662*0.999836 = 0.63651 A
    assert en.trapezoid_harmonic_a(2.0, 0.25, 500e3, 10e-9, 1e6) == pytest.approx(0.63651, rel=1e-4)


def test_envelope_corners_by_hand():
    # ipk 2 A, d 0.25, T 2 us (500 kHz), tr 10 ns. Corners: 1/(pi d T) = 636.6 kHz and 1/(pi tr) = 31.83 MHz.
    f = en.trapezoid_envelope_a
    assert f(2.0, 0.25, 500e3, 10e-9, 100e3) == pytest.approx(2 * 2.0 * 0.25)                    # flat region: 2*ipk*d = 1 A
    assert f(2.0, 0.25, 500e3, 10e-9, 6.366e6) == pytest.approx(1.0 / 10.0, rel=1e-3)           # 10x above the first corner: -20 dB (1/10)
    assert f(2.0, 0.25, 500e3, 10e-9, 318.3e6) == pytest.approx(1.0 / (318.3e6 * math.pi * 0.25 * 2e-6) / 10.0, rel=1e-3)    # 10x above the second corner: another 1/10
    assert f(2.0, 0.25, 500e3, 10e-9, 100e6) / f(2.0, 0.25, 500e3, 10e-9, 1000e6) == pytest.approx(100.0, rel=1e-6)           # -40 dB/decade beyond both corners
    # the exact line at n*fsw never exceeds the envelope
    for n in range(1, 40):
        assert en.trapezoid_harmonic_a(2.0, 0.25, 500e3, 10e-9, n * 500e3) <= f(2.0, 0.25, 500e3, 10e-9, n * 500e3) * (1 + 1e-9) * 1.0001 or n * 500e3 < 7e5


def _loop_board(path, w=30.0, h=20.0, track=0.5):
    from sparky.export.kicad import _layers_def, _stackup
    from sparky.pcb.rules import Rules

    def fp(ref, x, y, net, nid):
        return ["footprint", Q("T:pad"), ["layer", Q("F.Cu")], ["at", x, y], ["property", Q("Reference"), Q(ref)],
                ["pad", Q("1"), "smd", "rect", ["at", 0, 0], ["size", 1.0, 1.0], ["layers", Q("F.Cu"), Q("F.Mask")], ["net", nid, Q(net)]]]
    seg = lambda a, b, n: ["segment", ["start", *a], ["end", *b], ["width", track], ["layer", Q("F.Cu")], ["net", n]]
    root = ["kicad_pcb", ["version", 20221018], ["generator", Q("test")], ["general", ["thickness", 1.6]], ["paper", Q("A4")], _layers_def(2),
            ["setup", _stackup(Rules(layers=2), 2)], ["net", 0, Q("")], ["net", 1, Q("V")], ["net", 2, Q("G")], ["net", 3, Q("DUMMY")],
            fp("P1", 0, 0, "V", 1), fp("P2", w, 0, "V", 1), fp("P3", 0, h, "G", 2), fp("P4", w, h, "G", 2),
            seg((0, 0), (w, 0), 1), seg((0, h), (w, h), 2), ["segment", ["start", -3, -3], ["end", -2, -3], ["width", 0.5], ["layer", Q("B.Cu")], ["net", 3]]]
    m = 5.0
    c = [(-m, -m), (w + m, -m), (w + m, h + m), (-m, h + m)]
    root += [["gr_line", ["start", *c[i]], ["end", *c[(i + 1) % 4]], ["stroke", ["width", 0.1], ["type", "default"]], ["layer", Q("Edge.Cuts")]] for i in range(4)]
    path.write_text(dump(root) + "\n")


@pytest.mark.slow
@pytest.mark.skipif(not en.available(), reason="needs pcb-analysis with compiled kernels and kicad-cli")
def test_loop_emission_matches_otts_formula(tmp_path):
    from sparky.pcb.rules import rules_for
    w, h, f, amps = 30.0, 20.0, 30e6, 0.1
    pcb = tmp_path / "loop.kicad_pcb"
    _loop_board(pcb, w, h)
    pads = [en.LoopPad("P1.1", "V", 0, 0, "F.Cu", 0.5, 0.5), en.LoopPad("P2.1", "V", w, 0, "F.Cu", 0.5, 0.5),
            en.LoopPad("P3.1", "G", 0, h, "F.Cu", 0.5, 0.5), en.LoopPad("P4.1", "G", w, h, "F.Cu", 0.5, 0.5)]
    refs = {"P2.1": "P1.1", "P4.1": "P3.1"}
    # a large, low-inductance capacitor closes the far end; the source drives the near end
    parts = [en.CapPart("C1", "P2.1", "P4.1", 10e-6, 5e-3, 0.5e-9)]
    out = en.run_hot_loop(pcb, pads=pads, references=refs, parts=parts, source_hi="P1.1", source_lo="P3.1", amps_by_f={f: amps}, rules=rules_for(2, "generic"),
                          plating_mm=0.02, pitch_mm=0.5, distance_m=10.0, limit="CISPR32_CLASS_B")
    ott = 20 * math.log10(1.316e-14 * f * f * (w * h * 1e-6) * amps / 10.0 * 1e6)
    assert abs(out[0].field_dbuv_per_m - ott) < 2.0, (out[0].field_dbuv_per_m, ott)
