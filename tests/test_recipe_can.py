import json
import sqlite3

import pytest

from sparky import datasheet
from sparky.picker import Catalog
from sparky.recipes import build_recipe
from sparky.recipes.ctx import Ctx
from sparky.toolchain import kicad_library_dir

pytestmark = pytest.mark.skipif(kicad_library_dir("symbols") is None, reason="KiCad libraries not installed")
SYM = "Interface_CAN_LIN:SN65HVD230"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_HOME", str(tmp_path / "home"))
    p = tmp_path / "db.sqlite3"
    c = sqlite3.connect(p)
    c.execute("""create table jlc_components (lcsc INTEGER PRIMARY KEY, category TEXT, subcategory TEXT, mfr TEXT, package TEXT, manufacturer TEXT,
                 library_type TEXT, preferred INTEGER, description TEXT, datasheet TEXT, stock INTEGER, price TEXT, attributes TEXT)""")
    rows = [("Multilayer Ceramic Capacitors MLCC - SMD/SMT", "C100N", "0402", {"Capacitance": "100nF", "Voltage Rating": "16V"}),
            ("Chip Resistor - Surface Mount", "R120", "0402", {"Resistance": "120ohm", "Tolerance": "1%"})]
    for i, (sub, mpn, pkg, attrs) in enumerate(rows, 1):
        c.execute("insert into jlc_components values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (i, "x", sub, mpn, pkg, "m", "base", 1, "", "", 9000, "1-:0.1", json.dumps(attrs)))
    c.commit()
    c.close()
    datasheet.save("SN65HVD230", dict(vcc_min=3.0, vcc_max=3.6, bitrate_max=1e6, c_bypass=100e-9, icc_max=0.017), "SYNTHETIC test fixture")
    return Catalog(p)


def test_can_node_connects_every_signal_pin_by_name(env):
    c = build_recipe("can_node", dict(symbol=SYM, vcc=3.3, bitrate=500e3, terminated=True), ctx=Ctx(env))
    u = next(r for r, comp in c.components.items() if comp.part.mpn == "SN65HVD230")
    nets = {p.name: c.net_of(f"{u}.{p.number}") for p in c.components[u].part.pins}
    assert nets == {"D": "TXD", "GND": "GND", "VCC": "VCC", "R": "RXD", "Vref": None, "CANL": "CANL", "CANH": "CANH", "Rs": "GND"}
    r = [comp for comp in c.components.values() if comp.part.category == "resistor"]
    assert len(r) == 1 and {c.net_of(f"{r[0].ref}.1"), c.net_of(f"{r[0].ref}.2")} == {"CANH", "CANL"}


def test_supply_and_bitrate_are_checked_against_the_datasheet(env):
    with pytest.raises(ValueError):
        build_recipe("can_node", dict(symbol=SYM, vcc=5.0, bitrate=500e3), ctx=Ctx(env))
    with pytest.raises(ValueError):
        build_recipe("can_node", dict(symbol=SYM, vcc=3.3, bitrate=2e6), ctx=Ctx(env))
