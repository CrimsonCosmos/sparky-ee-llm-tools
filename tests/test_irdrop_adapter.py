"""kicad-irdrop through our adapter, against a hand calculation: a straight 20 x 1 mm, 35 um copper track carrying 2 A has R = L / (sigma w t)
with sigma = 5.8e7 S/m (copper, as kicad-irdrop assumes) = 9.85 mOhm, so the drop is 19.7 mV."""
import pytest

from sparky.analysis import irdrop
from sparky.analysis.irdrop import Rail
from sparky.sexpr import Q, dump

pytestmark_needs = pytest.mark.skipif(not irdrop.available(), reason="kicad-irdrop not installed in the KiCad Python")


def test_rail_spec():
    assert Rail("3V3", "U1", 1.5, ["U2"]).spec() == "3V3:U1:1.5:U2::"
    assert Rail("1V0", "R1", 3, ["U3", "U4"], sense="R9", vnom=1.0).spec() == "1V0:R1:3:U3+U4:R9:1"


def _straight_track_board(path, length=20.0, width=1.0, t=0.035):
    from sparky.export.kicad import _layers_def, _stackup
    from sparky.pcb.rules import Rules
    rules = Rules(layers=2)
    def fp(ref, x):
        return ["footprint", Q("T:pad"), ["layer", Q("F.Cu")], ["at", x, 0], ["property", Q("Reference"), Q(ref)],
                ["pad", Q("1"), "smd", "rect", ["at", 0, 0], ["size", 1.0, 1.0], ["layers", Q("F.Cu"), Q("F.Mask")], ["net", 1, Q("N1")]]]
    root = ["kicad_pcb", ["version", 20221018], ["generator", Q("test")], ["general", ["thickness", 1.6]], ["paper", Q("A4")],
            _layers_def(2),
            ["setup", _stackup(rules, 2)], ["net", 0, Q("")], ["net", 1, Q("N1")], fp("J1", 0.0), fp("J2", length),
            ["segment", ["start", 0, 0], ["end", length, 0], ["width", width], ["layer", Q("F.Cu")], ["net", 1]]]
    path.write_text(dump(root) + "\n")


@pytest.mark.skipif(not irdrop.available(), reason="kicad-irdrop not installed in the KiCad Python")
def test_straight_track_matches_L_over_sigma_w_t(tmp_path):
    pcb = tmp_path / "t.kicad_pcb"
    _straight_track_board(pcb)
    out = irdrop.run_irdrop(pcb, [Rail("N1", "J1", 2.0, ["J2"])], grid=0.05, budget_pct=2.0)
    r = out["rails"][0]
    expected_mohm = 0.020 / (5.8e7 * 0.001 * 35e-6) * 1e3        # 9.85 mOhm
    assert r["r_eff_mohm"] == pytest.approx(expected_mohm, rel=0.10)      # the track sits on a 0.05 mm raster; pads add a little current-crowding resistance
    assert r["worst_drop_mv"] == pytest.approx(2.0 * expected_mohm, rel=0.10)


def test_rails_are_derived_from_ports_and_topology():
    from conftest import mcu_circuit
    c = mcu_circuit()
    assert irdrop.rails_from_circuit(c) == []                                   # no declared rail current -> nothing to analyse
    c.add_port("vcc", "+3V3", "power", "out", voltage=3.3, current_a=0.2)
    (rail,) = irdrop.rails_from_circuit(c)
    # source = the connector that feeds the +3V3 net (no power-output pin, no inductor); load = the MCU (power-input pins on the net)
    assert (rail.net, rail.source, rail.loads, rail.amps, rail.vnom) == ("+3V3", "J1", ["U1"], 0.2, 3.3)
