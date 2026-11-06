import json
import sqlite3

import pytest

from sparky import datasheet
from sparky.picker import Catalog
from sparky.recipes import build_recipe
from sparky.recipes.ctx import Ctx
from sparky.toolchain import kicad_library_dir

pytestmark = pytest.mark.skipif(kicad_library_dir("symbols") is None, reason="KiCad libraries not installed")

MLCC, RES, IND, DCDC = ("Multilayer Ceramic Capacitors MLCC - SMD/SMT", "Chip Resistor - Surface Mount", "Power Inductors", "DC-DC Converters")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_HOME", str(tmp_path / "home"))
    p = tmp_path / "db.sqlite3"
    c = sqlite3.connect(p)
    c.execute("""create table jlc_components (lcsc INTEGER PRIMARY KEY, category TEXT, subcategory TEXT, mfr TEXT, package TEXT, manufacturer TEXT,
                 library_type TEXT, preferred INTEGER, description TEXT, datasheet TEXT, stock INTEGER, price TEXT, attributes TEXT)""")
    rows = [(DCDC, "TPS563201DDCR", "SOT-23-THIN-6", {"Topology": "Buck", "Output Type": "Adjustable", "Output Current": "3A", "Voltage - Supply": "4.5V~17V"}),
            (IND, "IND-4R7", "1210", {"Inductance": "4.7uH", "Current - Saturation(Isat)": "5A"}),
            (RES, "R33K2", "0402", {"Resistance": "33.2kohm", "Tolerance": "1%"}), (RES, "R10K", "0402", {"Resistance": "10kohm", "Tolerance": "1%"}),
            (MLCC, "C100N", "0402", {"Capacitance": "100nF", "Voltage Rating": "25V"})]
    rows += [(MLCC, f"C{n}", "0805", {"Capacitance": n, "Voltage Rating": "50V"}) for n in ("2.2uF", "4.7uF", "10uF", "22uF", "47uF")]
    for i, (sub, mpn, pkg, attrs) in enumerate(rows, 1):
        c.execute("insert into jlc_components values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (i, "x", sub, mpn, pkg, "m", "base", 1, "", "", 9000, "1-:0.1", json.dumps(attrs)))
    c.commit()
    c.close()
    # SYNTHETIC datasheet numbers for this test (the values used in tests/test_eq_buck / TI TPS563201 typical application); real flows store these via `save`
    datasheet.save("TPS563201", dict(vref=0.768, fsw=580e3, **{"l_min@3.3V": 2.2e-6, "l_max@3.3V": 4.7e-6}, c_boot=100e-9, v_boot=5.0), "test fixture")
    return Catalog(p)


def test_buck_recipe_builds_and_spice_agrees_with_equations(env):
    c = build_recipe("buck", dict(symbol="Regulator_Switching:TPS563201", vin_min=9, vin_max=14, vout=3.3, iout=2.0, rail="+3V3"), ctx=Ctx(env))
    assert any(comp.part.mpn == "TPS563201DDCR" for comp in c.components.values())          # sourced by name from the catalogue
    assert c.ports["vout"].net == "+3V3" and c.ports["vin"].meta["vmax"] == 14
    values = sorted(comp.value for comp in c.components.values() if comp.part.category == "resistor")
    assert values == ["10k", "33.2k"]                       # hand calc: Rtop = 10k*(3.3/0.768 - 1) = 32.97k -> E96 33.2k
    assert "-> 3.318 V" in c.calcs["buck"]                    # Vout = 0.768*(1 + 33.2/10) = 3.3178 V
    ind = [comp for comp in c.components.values() if comp.part.category == "inductor"]
    assert [i.value for i in ind] == ["4.7u"]
    assert not [w for w in c.warnings if "SPICE disagrees" in w], c.warnings
    assert c.calcs["spice"].startswith("ngspice")          # ngspice is part of the test environment; "NOT VERIFIED" would hide a missing check


def test_missing_datasheet_numbers_are_requested_not_invented(env, tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_HOME", str(tmp_path / "empty"))
    with pytest.raises(datasheet.NeedsDatasheet) as e:
        build_recipe("buck", dict(symbol="Regulator_Switching:TPS563201", vin_min=9, vin_max=14, vout=3.3, iout=2.0, rail="+3V3"), ctx=Ctx(env))
    assert "vref" in e.value.missing and "fsw" in e.value.missing
