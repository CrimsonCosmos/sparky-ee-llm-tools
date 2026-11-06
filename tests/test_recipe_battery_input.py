import json
import sqlite3

import pytest

from sparky.picker import Catalog
from sparky.recipes import build_recipe
from sparky.recipes.ctx import Ctx, NoPart
from sparky.toolchain import kicad_library_dir

pytestmark = pytest.mark.skipif(kicad_library_dir("symbols") is None, reason="KiCad libraries not installed")

ARGS = dict(vbat_nom=48, vbat_max=54.6, vbat_min=39, ibat_max=8, c_bulk=400e-6, connector_symbol="Connector:Screw_Terminal_01x02",
            connector_footprint="Connector_AMASS:AMASS_XT60PW-M_1x02_P7.20mm_Horizontal", connector_rating_a=30)


@pytest.fixture
def cat(tmp_path):
    p = tmp_path / "db.sqlite3"
    c = sqlite3.connect(p)
    c.execute("""create table jlc_components (lcsc INTEGER PRIMARY KEY, category TEXT, subcategory TEXT, mfr TEXT, package TEXT, manufacturer TEXT,
                 library_type TEXT, preferred INTEGER, description TEXT, datasheet TEXT, stock INTEGER, price TEXT, attributes TEXT)""")
    tvs = "ESD And Surge Protection (TVS/ESD)"
    rows = [(tvs, "TVS-58-BI", "DO-214AA(SMB)", {"Polarity": "Bidirectional", "Reverse Stand-Off Voltage (Vrwm)": "58V"}),
            (tvs, "TVS-58-UNI", "SMB(DO-214AA)", {"Polarity": "Unidirectional", "Reverse Stand-Off Voltage (Vrwm)": "58V"}),
            (tvs, "TVS-90-UNI", "SMB(DO-214AA)", {"Polarity": "Unidirectional", "Reverse Stand-Off Voltage (Vrwm)": "90V"}),
            ("Fuses", "FUSE-10A", "1206", {"Current Rating": "10A", "Voltage Rating (DC)": "75V"}),
            ("Aluminum Electrolytic Capacitors - SMD", "EC-220-100", "SMD,D10xL10.2mm", {"Capacitance": "220uF", "Voltage Rating": "100V"})]
    for i, (sub, mpn, pkg, attrs) in enumerate(rows, 1):
        c.execute("insert into jlc_components values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (i, "x", sub, mpn, pkg, "m", "base", 1, "", "", 9000, "1-:0.1", json.dumps(attrs)))
    c.commit()
    c.close()
    return Catalog(p)


def test_battery_input_picks_unidirectional_tvs_just_above_working_voltage_and_sized_parts(cat):
    c = build_recipe("battery_input", ARGS, ctx=Ctx(cat))
    mpns = sorted(comp.part.mpn for comp in c.components.values())
    # stand-off >= 54.6 * 1.02 = 55.7 V: the 58 V unidirectional part (not the bidirectional one, not the 90 V one)
    assert "TVS-58-UNI" in mpns and "TVS-58-BI" not in mpns and "TVS-90-UNI" not in mpns
    assert "FUSE-10A" in mpns                       # >= 8 A * 1.25 = 10 A
    assert sum(1 for m in mpns if m == "EC-220-100") == 2        # 400 uF needs 2 x 220 uF; 220 uF alone is short
    tvs = next(r for r, comp in c.components.items() if comp.part.mpn == "TVS-58-UNI")
    assert c.net_of(f"{tvs}.1") == "VBAT" or c.net_of(f"{tvs}.2") == "VBAT"
    assert c.components[tvs].footprint.name == "D_SMB"           # footprint from the catalogue package via the KiCad library


def test_connector_rating_is_checked(cat):
    with pytest.raises(ValueError):
        build_recipe("battery_input", dict(ARGS, ibat_max=28), ctx=Ctx(cat))        # 28 A > 0.8 * 30 A


def test_no_suitable_tvs_is_an_error(cat):
    with pytest.raises(NoPart):
        build_recipe("battery_input", dict(ARGS, vbat_max=120.0), ctx=Ctx(cat))
