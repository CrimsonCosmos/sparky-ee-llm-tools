import json
import sqlite3

import pytest

from sparky.picker import Catalog
from sparky.recipes import build_recipe, registry
from sparky.recipes.ctx import Ctx


@pytest.fixture
def cat(tmp_path):
    p = tmp_path / "db.sqlite3"
    c = sqlite3.connect(p)
    c.execute("""create table jlc_components (lcsc INTEGER PRIMARY KEY, category TEXT, subcategory TEXT, mfr TEXT, package TEXT, manufacturer TEXT,
                 library_type TEXT, preferred INTEGER, description TEXT, datasheet TEXT, stock INTEGER, price TEXT, attributes TEXT)""")
    rows = [
        (1, "Capacitors", "Multilayer Ceramic Capacitors MLCC - SMD/SMT", "CAP-A", "0603", "A", "base", 1, "", "", 9000, "1-:0.01", {"Capacitance": "10uF", "Voltage Rating": "50V"}),
        (2, "Capacitors", "Multilayer Ceramic Capacitors MLCC - SMD/SMT", "CAP-B", "0603", "A", "base", 1, "", "", 9000, "1-:0.01", {"Capacitance": "22uF", "Voltage Rating": "10V"}),
        (3, "Power Management", "Voltage Regulators - Linear, Low Drop Out (LDO) Regulators", "AMS1117-3.3", "SOT-223", "A", "base", 1, "", "", 9000, "1-:0.2",
         {"Output Voltage": "3.3V", "Output Current": "1A"}),
    ]
    for r in rows:
        c.execute("insert into jlc_components values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (*r[:12], json.dumps(r[12])))
    c.commit()
    c.close()
    return Catalog(p)


def test_recipes_are_discovered_from_files():
    assert "ldo" in registry()


def test_ldo_builds_from_catalogue_and_kicad_symbol(cat):
    c = build_recipe("ldo", dict(vin_min=4.5, vin_max=5.5, vout=3.3, iout=0.3, c_in=10e-6, c_out=22e-6, rail="+3V3"), ctx=Ctx(cat))
    mpns = {comp.part.mpn for comp in c.components.values()}
    assert "AMS1117-3.3" in mpns                                    # real KiCad symbol, pins by name
    assert c.ports["vout"].voltage == 3.3
    ref = next(r for r, comp in c.components.items() if comp.part.mpn == "AMS1117-3.3")
    assert sum(1 for pins in c.nets.values() for p in pins if p.startswith(ref + ".")) == 3       # IN, OUT, GND all connected
    # dissipation = (5.5 - 3.3) * 0.3 = 0.66 W  (hand calculation)
    assert "0.66 W" in c.calcs["ldo"]
