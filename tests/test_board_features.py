"""Fiducials, test points and corner-fixed mounting holes through placement, routing, KiCad DRC and schematic parity."""
import pytest

from conftest import mcu_circuit, needs_kicad_libs
from sparky import kicad_backend, routing
from sparky.export import bom
from sparky.pcb import features
from sparky.pcb.flow import BoardSpec, build_board
from sparky.pcb.placement import PlaceOptions, place
from sparky.pcb.rules import Rules

pytestmark = needs_kicad_libs


def test_fiducials_sit_in_three_different_corners_and_stay_out_of_the_bom():
    c = mcu_circuit()
    refs = features.add_fiducials(c, 3, Rules().fiducial_footprint)
    board = place(c, Rules(layers=2), PlaceOptions(seed=1))
    inset = Rules().corner_inset_mm
    pos = {r: (board.place[r].x, board.place[r].y) for r in refs}
    assert pos["FID1"] == pytest.approx((inset, inset)) and pos["FID2"] == pytest.approx((board.width - inset, inset)) and pos["FID3"] == pytest.approx((board.width - inset, board.height - inset))
    assert not any(r in bom.bom_jlcpcb(c) for r in refs) and "FID" not in bom.cpl_csv(board)


def test_test_points_join_the_named_nets_and_are_not_assembled():
    c = mcu_circuit()
    refs = features.add_test_points(c, ["+3V3", "GND"], "TestPoint:TestPoint_Pad_D1.5mm")
    pn = c.pin_net_map()
    assert [pn[f"{r}.1"] for r in refs] == ["+3V3", "GND"]
    assert "TP" not in bom.bom_jlcpcb(c)
    with pytest.raises(ValueError, match="no such net"):
        features.add_test_points(c, ["NOPE"], "TestPoint:TestPoint_Pad_D1.5mm")


def test_rails_means_power_and_ground_nets():
    assert features.rail_nets(mcu_circuit()) == ["+3V3", "GND"]


@pytest.mark.slow
@pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad / Java missing")
def test_board_with_fiducials_and_probe_pads_routes_and_passes_kicad(tmp_path):
    from sparky.export.package import write_package
    c = mcu_circuit()
    res = build_board(c, BoardSpec(layers=2, fiducials=3, test_points="rails"), log=None)
    assert res.success, res.drc.summary()
    assert len(mcu_circuit().components) == len(c.components)              # the caller's circuit is untouched
    write_package(res, tmp_path, "f")
    report = (tmp_path / "REPORT.md").read_text()
    assert "PARITY OK" in report and "ERC: **CLEAN**" in report
    cpl = (tmp_path / "cpl_jlcpcb.csv").read_text()
    assert "FID" not in cpl and "TP" not in cpl
    pcb = (tmp_path / "f.kicad_pcb").read_text()
    assert pcb.count("Fiducial_1mm_Mask2mm") == 3 and pcb.count("TestPoint_Pad_D1.5mm") == 2
