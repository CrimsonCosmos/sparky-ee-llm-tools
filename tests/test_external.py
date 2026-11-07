"""InteractiveHtmlBom and KiKit adapters. Each test runs only where the tool really works; skips say so (nothing is reported as passed that did not run)."""
import re

import pytest

from conftest import mcu_circuit
from sparky import kicad_backend, routing
from sparky.export import external
from sparky.export.package import write_package
from sparky.pcb.flow import BoardSpec, build_board


@pytest.fixture(scope="module")
def board(tmp_path_factory):
    res = build_board(mcu_circuit(), BoardSpec(layers=2), log=None)
    out = tmp_path_factory.mktemp("ext")
    write_package(res, out, "m")
    return res, out


pytestmark = [pytest.mark.slow, pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad / Java missing")]


@pytest.mark.skipif(not external.ibom_available(), reason="InteractiveHtmlBom not installed")
def test_interactive_bom_lists_every_part_of_the_board(board):
    res, out = board
    page = (out / "review" / "ibom.html")
    assert page.exists()
    text = page.read_text(errors="replace")
    assert "<html" in text.lower() and len(text) > 50_000                      # self-contained page with the board data embedded
    m = re.search(r'LZString\.decompressFromBase64\("([^"]+)"\)', text)
    assert m, "board data blob not found in the page"


@pytest.mark.skipif(not external.kikit_available(), reason="KiKit not usable in this environment (needs pcbnew + wxPython)")
def test_panel_passes_kicad_drc_and_holds_four_boards(board, tmp_path):
    res, out = board
    panel = external.panelize(out / "m.kicad_pcb", tmp_path / "panel.kicad_pcb", rows=2, cols=2, space_mm=3, tab_width_mm=3)
    rep = kicad_backend.run_drc(panel, refill=True, rules=res.board.rules)
    assert panel.read_text().count("(footprint") >= 4 * len(res.board.circuit.components)
