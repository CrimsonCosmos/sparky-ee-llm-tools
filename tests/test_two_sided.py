"""Two-sided assembly. The flipped footprint geometry is checked against KiCad itself (pcbnew's own pad positions and layers), not against our own mirror code."""
import json
import subprocess
import sys
import textwrap

import pytest

from conftest import mcu_circuit, needs_kicad_libs
from sparky import toolchain
from sparky.constraints import Side
from sparky.export.bom import cpl_csv
from sparky.export.kicad import to_kicad_pcb
from sparky.pcb.placement import PlaceOptions, place
from sparky.pcb.rules import Rules

pytestmark = needs_kicad_libs

_DUMP = textwrap.dedent("""
    import json, sys, pcbnew
    b = pcbnew.LoadBoard(sys.argv[1])
    out = {}
    for f in b.GetFootprints():
        for p in f.Pads():
            pos = p.GetPosition()
            out[f.GetReference() + "." + p.GetNumber()] = [pos.x / 1e6, pos.y / 1e6, f.GetSide() == pcbnew.B_Cu, p.IsOnLayer(pcbnew.B_Cu), p.IsOnLayer(pcbnew.F_Cu)]
    print(json.dumps(out))
""")


def _bottom_board(rules=None, rot=0):
    c = mcu_circuit()
    caps = [r for r in c.components if r.startswith("C")]
    c.constraints.append(Side(caps + ["U1"] if "U1" in c.components else caps))
    board = place(c, rules or Rules(layers=2), PlaceOptions(seed=3))
    for ref in board.place:
        if board.place[ref].side == "bottom":
            board.place[ref].rot = rot
    return board


@pytest.mark.skipif(toolchain.kicad_python() is None, reason="no interpreter with pcbnew")
@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_flipped_pads_land_where_kicad_puts_them(tmp_path, rot):
    board = _bottom_board(rot=rot)
    flipped = [r for r, p in board.place.items() if p.side == "bottom"]
    assert flipped, "nothing was put on the bottom"
    f = tmp_path / "b.kicad_pcb"
    f.write_text(to_kicad_pcb(board))
    got = json.loads(subprocess.run([toolchain.kicad_python(), "-c", _DUMP, str(f)], capture_output=True, text=True, check=True).stdout)
    for pad in board.pads():
        x, y, fp_bottom, on_b, on_f = got[f"{pad.ref}.{pad.number}"]
        assert fp_bottom == (pad.side == "bottom")
        if pad.drill == 0:                                  # an SMD pad sits on the face of its part
            assert on_b == fp_bottom and on_f != fp_bottom
        # KiCad's y grows downward like ours; its absolute pad position must equal the model's
        assert (x, y) == pytest.approx((pad.x, pad.y), abs=2e-3), f"{pad.ref}.{pad.number} rot {rot}"


def test_opposite_faces_do_not_compete_for_area():
    same = mcu_circuit()
    two = mcu_circuit()
    two.constraints.append(Side([r for r in two.components if r.startswith("C")]))
    a = place(same, Rules(layers=2), PlaceOptions(seed=3, fixed_outline=False))
    b = place(two, Rules(layers=2), PlaceOptions(seed=3, fixed_outline=False))
    assert any(p.side == "bottom" for p in b.place.values()) and all(p.side == "top" for p in a.place.values())
    assert b.width * b.height <= a.width * a.height * 1.0001      # moving the caps under the board cannot need more area
    assert b.stats["place_overlap_mm2"] < 1e-3


def test_a_fab_that_assembles_one_face_refuses_and_says_so():
    c = mcu_circuit()
    c.constraints.append(Side([r for r in c.components if r.startswith("C")]))
    board = place(c, Rules(layers=2, assembly_sides=1), PlaceOptions(seed=3))
    assert all(p.side == "top" for p in board.place.values())
    assert any("bottom-side assembly requested" in w for w in board.warnings)


def test_through_hole_parts_stay_on_top():
    c = mcu_circuit()
    j = [r for r, k in c.components.items() if any(p.thru for p in k.footprint.pads)]
    assert j
    c.constraints.append(Side(j))
    board = place(c, Rules(layers=2), PlaceOptions(seed=3))
    assert all(board.place[r].side == "top" for r in j)


def test_cpl_agrees_with_kicads_own_position_file(tmp_path):
    cli = toolchain.kicad_cli()
    if not cli:
        pytest.skip("kicad-cli missing")
    board = _bottom_board(rot=90)
    board.place["C1"].rot = 270
    f = tmp_path / "b.kicad_pcb"
    f.write_text(to_kicad_pcb(board))
    subprocess.run([cli, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "-o", str(tmp_path / "pos.csv"), str(f)],
                   capture_output=True, check=True)
    ref = {r[0].strip('"'): r for r in (l.split(",") for l in (tmp_path / "pos.csv").read_text().splitlines()[1:])}
    ours = {r[0]: r for r in (l.split(",") for l in cpl_csv(board).strip().splitlines()[1:])}
    assert ours.keys() == ref.keys()
    assert {r[-1] for r in ours.values()} == {"Top", "Bottom"}
    for name, o in ours.items():
        k = ref[name]
        assert float(o[3][:-2]) == pytest.approx(float(k[3]), abs=1e-3)
        assert float(o[4][:-2]) - board.height == pytest.approx(float(k[4]), abs=1e-3)      # KiCad: y up from the page origin; ours from the board corner
        assert float(o[5]) == pytest.approx(float(k[5]) % 360, abs=1e-6), name
        assert o[6].lower() == k[6].strip().lower()


# ---- slow: through the whole flow ------------------------------------------------------------------------------------------------------------
from sparky import kicad_backend, routing  # noqa: E402
from sparky.analysis import thermal  # noqa: E402
from sparky.export.package import write_package  # noqa: E402
from sparky.pcb.flow import BoardSpec, build_board  # noqa: E402


@pytest.mark.slow
@pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad >= 8 / Java missing")
def test_bypass_caps_on_the_back_route_and_pass_kicad_drc(tmp_path):
    c = mcu_circuit()
    c.constraints.append(Side([r for r in c.components if r.startswith("C")]))
    res = build_board(c, BoardSpec(layers=2), log=None)
    assert res.success, res.drc.summary()
    assert not res.drc.errors and not res.drc.unconnected
    files = write_package(res, tmp_path, "tw")
    txt = (tmp_path / "tw.kicad_pcb").read_text()
    assert txt.count('(layer "B.Cu")') >= 4                                # footprints on the back
    pos = (tmp_path / "kicad_positions.csv").read_text()
    assert pos.lower().count("bottom") == 4
    assert files["fab.zip"].stat().st_size > 1000


@pytest.mark.slow
@pytest.mark.skipif(not (thermal.available() and routing.available() and kicad_backend.available()), reason="needs pcb-analysis kernels, KiCad, Java")
def test_a_heater_on_the_back_warms_the_board_like_the_same_heater_on_the_front(tmp_path):
    peaks = {}
    for side in ("top", "bottom"):
        c = mcu_circuit()
        c.constraints.append(Side(["U1"], side))
        res = build_board(c, BoardSpec(layers=2), log=None)
        out = tmp_path / side
        out.mkdir()
        write_package(res, out, "m")
        u = res.board.courtyard("U1")
        half = 0.5 * min(u[2] - u[0], u[3] - u[1])
        assert res.board.place["U1"].side == side
        r = thermal.run_thermal(res.pcb_path, {"U1": 0.5}, out / "kicad_positions.csv", ambient_c=25.0, h_w_m2k=10.0, emissivity=0.0, pitch_mm=0.5,
                                source_half_mm={"U1": half})
        assert r.heat_in_w == pytest.approx(0.5, rel=1e-6)
        peaks[side] = r.peak_rise_k
    # convection acts on both faces alike and there is no radiation, so the face a part is on cannot change its temperature much
    assert peaks["bottom"] == pytest.approx(peaks["top"], rel=0.15)
