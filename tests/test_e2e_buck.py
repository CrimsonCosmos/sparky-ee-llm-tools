"""End-to-end with the REAL catalogue (skipped when it is not downloaded) and datasheet numbers taken from TI's official TPS563201 datasheet
(https://www.ti.com/lit/ds/symlink/tps563201.pdf): VFBTH 768 mV, Fsw 580 kHz, Table 7-2 (L 2.2-4.7 uH at 3.3 V), 0.1 uF VBST cap, VBST-SW <= 5.5 V, HS Rds(on) 95 mOhm."""
import pytest

from sparky import datasheet, routing
from sparky.picker.catalog import db_path
from sparky.toolchain import kicad_has

pytestmark = [pytest.mark.slow, pytest.mark.skipif(not db_path().exists() or not routing.available() or not kicad_has("pcb_drc"),
                                                    reason="needs the downloaded parts catalogue, KiCad >= 8, Java and pcbnew")]


def test_real_buck_routes_clean_and_spice_agrees(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_HOME", str(tmp_path / "home"))
    datasheet.save("TPS563201", {"vref": 0.768, "fsw": 580e3, "l_min@3.3V": 2.2e-6, "l_max@3.3V": 4.7e-6, "c_boot": 1e-7, "v_boot": 5.5, "rds_on": 0.095, "en_to_vin": 1},
                   "TI TPS563201 datasheet (ti.com/lit/ds/symlink/tps563201.pdf): Electrical Characteristics, Table 7-2, VBST pin description")
    from sparky.export.package import write_package
    from sparky.pcb.flow import BoardSpec, build_board
    from sparky.recipes import build_recipe
    c = build_recipe("buck", dict(symbol="Regulator_Switching:TPS563201", vin_min=9, vin_max=14, vout=3.3, iout=2.0, rail="+3V3"))
    assert not [w for w in c.warnings if "SPICE disagrees" in w], c.warnings
    res = build_board(c, BoardSpec(layers=2), log=None)
    assert res.success and not res.drc.errors and not res.drc.unconnected, res.drc.summary()
    files = write_package(res, tmp_path / "pkg", "buck")
    assert files["fab.zip"].exists() and "step" in files
    bom = (tmp_path / "pkg" / "bom.csv").read_text()
    assert "TPS563201" in bom and "LCSC #" in bom
