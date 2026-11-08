"""kicad-cli review / test / mechanical exports of a routed board, each opened independently (not by the code that wrote it)."""
import json
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader

from conftest import mcu_circuit
from sparky import kicad_backend, routing
from sparky.export.package import write_package
from sparky.pcb.flow import BoardSpec, build_board

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad >= 8 / Java missing")]


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    res = build_board(mcu_circuit(), BoardSpec(layers=2), log=None)
    out = tmp_path_factory.mktemp("x")
    return res, out, write_package(res, out, "m")


def test_nothing_failed_silently(pkg):
    _, out, files = pkg
    report = (out / "REPORT.md").read_text()
    assert "Exports that did not run" not in report
    for rel in ("review/layers.pdf", "review/render_top.png", "review/render_bottom.png", "review/schematic.pdf", "review/stats.json", "test/netlist.d356",
                "test/board.odb", "test/board.cad", "mechanical/board.glb", "mechanical/board.wrl", "mechanical/outline.dxf"):
        assert rel in files and files[rel].stat().st_size > 100, rel


def test_renders_are_images_of_the_asked_size(pkg):
    for n in ("review/render_top.png", "review/render_bottom.png"):
        w, h = Image.open(pkg[2][n]).size                                   # KiCad trims the canvas a little; the aspect ratio is what we asked for
        assert w / h == pytest.approx(4 / 3, rel=0.01) and 1200 <= w <= 1600


def test_layer_pdf_has_the_layers_and_the_parts(pkg):
    pages = PdfReader(str(pkg[2]["review/layers.pdf"])).pages
    assert len(pages) >= 10                                                         # copper, silk, mask, paste, fab, courtyard on both faces
    text = " ".join(p.extract_text() or "" for p in pages)
    assert all(r in text for r in ("U1", "J1", "C1"))                               # reference designators are on the fab/silk pages
    assert len(PdfReader(str(pkg[2]["review/schematic.pdf"])).pages) >= 1


def test_etest_netlist_equals_the_circuits_connectivity(pkg):
    res, _, files = pkg
    got = {}
    for line in files["test/netlist.d356"].read_text().splitlines():
        if line[:3] in ("317", "327"):
            net, ref, pad = line[3:20].strip(), line[20:26].strip(), line[26:31].strip().lstrip("-")
            got[f"{ref}.{pad}"] = net
    expect = res.board.circuit.pin_net_map()
    assert {k: v for k, v in got.items() if k in expect} == expect                   # every connected pin, same net as the circuit says


def test_mechanical_files(pkg):
    files = pkg[2]
    assert files["mechanical/board.glb"].read_bytes()[:4] == b"glTF"
    assert files["mechanical/board.wrl"].read_text()[:20].startswith("#VRML")
    dxf = files["mechanical/outline.dxf"].read_text()
    assert "ENTITIES" in dxf and ("LINE" in dxf or "LWPOLYLINE" in dxf)
    assert "misc/info" in zipfile.ZipFile(files["test/board.odb"]).namelist()


def test_stats_agree_with_our_own_numbers(pkg):
    res, _, files = pkg
    d = json.loads(files["review/stats.json"].read_text())
    assert float(d["board"]["width"].split()[0]) == pytest.approx(res.board.width, abs=0.01)
    assert d["pads"]["smd"] + d["pads"]["through_hole"] == sum(len(c.footprint.pads) for c in res.board.circuit.components.values())


def test_fab_notes_drill_table_matches_the_vias_in_the_copper(pkg):
    import re
    res, out, files = pkg
    text = (out / "FAB_NOTES.md").read_text()
    assert "Not a verification" in text and "## Stackup" in text
    rows = re.findall(r"\| (\S+\.drl) \| ([\d.]+) \| (\d+) \|", text)
    total = sum(int(n) for _, _, n in rows)
    # independent count from the routed board: every plated/unplated hole = pad drills + via drills
    pcb = (out / "m.kicad_pcb").read_text()
    vias = len(re.findall(r"\n\t\(via", pcb))
    holes = sum(1 for c in res.board.circuit.components.values() for p in c.footprint.pads if p.drill > 0)
    assert total == vias + holes
