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


@pytest.mark.slow
@pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad / Java missing")
def test_rounded_corners_and_silk_title_pass_kicad_drc(tmp_path):
    import re
    from sparky.export.package import write_package
    res = build_board(mcu_circuit(), BoardSpec(layers=2, corner_radius_mm=2.0, silk_text="mcu rev A"), log=None)
    assert res.success, res.drc.summary()
    write_package(res, tmp_path, "r")
    pcb = (tmp_path / "r.kicad_pcb").read_text()
    assert len(re.findall(r"\(gr_arc", pcb)) == 4 and 'gr_text "mcu rev A"' in pcb
    # the arcs really cut the corners: the outline polygon the board keeps has less area than the rectangle by (4 - pi) r^2
    b = res.board
    import math
    assert b.outline.area == pytest.approx(b.width * b.height - (4 - math.pi) * 2.0 ** 2, rel=2e-3)
    with pytest.raises(ValueError, match="does not fit"):
        b.set_corner_radius(1e3)


def test_stitch_pitch_is_a_twentieth_of_the_wavelength_in_the_board():
    from sparky.pcb.stitching import pitch_for_frequency
    # 1 GHz in FR-4 (er 4.5): lambda0 = 299.79 mm, er_eff = 2.75 -> lambda = 180.8 mm -> /20 = 9.04 mm
    assert pitch_for_frequency(1e9, 4.5) == pytest.approx(299.792458 / (2.75 ** 0.5) / 20, rel=1e-9)


@pytest.mark.slow
@pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad / Java missing")
def test_stitching_vias_are_added_clear_of_other_nets_and_kicad_agrees(tmp_path):
    from sparky.export.package import write_package
    res = build_board(mcu_circuit(), BoardSpec(layers=2, stitch_pitch_mm=3.0), log=None)
    assert res.success, res.drc.summary()                                       # KiCad DRC clean with the vias in, nothing left unconnected
    n = res.board.stats["stitch_vias"]
    assert n >= 4
    files = write_package(res, tmp_path, "s")
    from sparky import sexpr
    tree = sexpr.parse(files["kicad_pcb"].read_text())
    stitch = [v for v in sexpr.find_all(tree, "via") if sexpr.find(v, "net")[1] == "GND"]
    assert len(stitch) >= n


@pytest.mark.slow
@pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad / Java missing")
def test_the_ground_pours_really_end_up_in_the_board(tmp_path):
    """Regression: with KiCad >= 9 files (nets named on items, no net table) the pours were silently not added."""
    from sparky import sexpr
    from sparky.export.package import write_package
    res = build_board(mcu_circuit(), BoardSpec(layers=2), log=None)
    tree = sexpr.parse(write_package(res, tmp_path, "z")["kicad_pcb"].read_text())
    zones = [(sexpr.find(z, "net")[1], [str(x) for x in sexpr.find(z, "layer")[1:]] or [str(sexpr.find(z, "layers")[1])]) for z in sexpr.find_all(tree, "zone")]
    assert sorted(l for n, ls in zones if n == "GND" for l in ls) == ["B.Cu", "F.Cu"]
    assert all(sexpr.find(z, "filled_polygon") for z in sexpr.find_all(tree, "zone"))          # KiCad filled them (the saved board carries the fill)
