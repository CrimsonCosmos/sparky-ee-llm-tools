"""Integration: our placer -> KiCad file -> Freerouting -> KiCad DRC -> KiCad exports (needs KiCad >= 8, Java, pcbnew). Slow."""
import re
import glob
import warnings
import zipfile

import pytest

from conftest import mcu_circuit
from sparky import kicad_backend, routing
from sparky.export.package import write_package
from sparky.pcb.flow import BoardSpec, build_board

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad >= 8 / Java / pcbnew missing")]


@pytest.fixture(scope="module", params=[2, 4])
def built(request, tmp_path_factory):
    from sparky.parts.db import PartsLibrary
    lib = PartsLibrary(tmp_path_factory.mktemp("h"), auto_research=False)
    res = build_board(mcu_circuit(lib), BoardSpec(layers=request.param), log=None)
    out = tmp_path_factory.mktemp("pkg")
    return request.param, res, out, write_package(res, out, "mcu")


def test_routed_and_kicad_drc_clean(built):
    layers, res, _, _ = built
    assert res.success, res.drc.summary()
    assert not res.drc.errors and not res.drc.unconnected


def test_package_contents_parse_independently(built):
    layers, res, out, files = built
    gerbonara = pytest.importorskip("gerbonara")
    gerbers = sorted(glob.glob(str(out / "gerber" / "*")))
    assert len(gerbers) >= 8
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for f in gerbers:
            (gerbonara.ExcellonFile if f.endswith(".drl") else gerbonara.GerberFile).open(f)
    txt = (out / "mcu.kicad_pcb").read_text()
    assert len(re.findall(r"\(footprint\s", txt)) == len(res.board.circuit.components)
    assert re.search(r"\(segment\s", txt)                                           # Freerouting really routed something
    assert (out / "kicad_drc.json").exists() and "fab.zip" in files and "ipc2581" in files
    assert len(zipfile.ZipFile(files["fab.zip"]).namelist()) >= 8


def test_step_export(built):
    layers, res, out, files = built
    assert "step" in files and files["step"].stat().st_size > 1000
