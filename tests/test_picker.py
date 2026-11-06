import json
import sqlite3

import pytest

from sparky.picker import Catalog, Need, recipes
from sparky.picker.catalog import parse_prices, price_at, quantity


@pytest.fixture
def cat(tmp_path):
    p = tmp_path / "db.sqlite3"
    c = sqlite3.connect(p)
    c.execute("""create table jlc_components (lcsc INTEGER PRIMARY KEY, category TEXT, subcategory TEXT, mfr TEXT, package TEXT, manufacturer TEXT,
                 library_type TEXT, preferred INTEGER, description TEXT, datasheet TEXT, stock INTEGER, price TEXT, attributes TEXT)""")
    rows = [
        (1, "Inductors", "Power Inductors", "L-A", "1210", "Acme", "expand", 0, "", "ds1", 5000, "1-99:0.50,100-:0.30", {"Inductance": "4.7uH", "Current - Saturation(Isat)": "4A", "DC Resistance(DCR)": "30mΩ"}),
        (2, "Inductors", "Power Inductors", "L-B", "1210", "Acme", "expand", 1, "", "ds2", 900, "1-:0.60", {"Inductance": "4.7uH", "Current - Saturation(Isat)": "6A", "DC Resistance(DCR)": "20mΩ"}),
        (3, "Inductors", "Power Inductors", "L-C", "1210", "Acme", "expand", 0, "", "", 50, "1-:0.10", {"Inductance": "4.7uH", "Current - Saturation(Isat)": "9A"}),      # low stock
        (4, "Inductors", "Power Inductors", "L-D", "1210", "Acme", "expand", 0, "", "", 5000, "1-:0.20", {"Inductance": "10uH", "Current - Saturation(Isat)": "9A"}),  # wrong L
        (5, "Transistors", "MOSFETs", "Q-A", "TO-220", "Acme", "expand", 0, "", "", 2000, "1-:2.0", {"Type": "N-Channel", "Drain to Source Voltage": "100V", "Current - Continuous Drain(Id)": "180A", "RDS(on)": "4.5mΩ@10V"}),
        (6, "Transistors", "MOSFETs", "Q-B", "SOT-23", "Acme", "base", 0, "", "", 9000, "1-:0.05", {"Type": "P-Channel", "Drain to Source Voltage": "30V", "Current - Continuous Drain(Id)": "5A"}),
    ]
    for r in rows:
        c.execute("insert into jlc_components values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (*r[:12], json.dumps(r[12])))
    c.commit()
    c.close()
    return Catalog(p)


def test_inductor_filters_stock_value_and_isat_then_ranks_basic_preferred_then_price(cat):
    res = cat.pick(recipes.inductor(4.7e-6, 3.5), qty=1)
    assert [c.mpn for c in res] == ["L-B", "L-A"]                 # L-C (stock 50) and L-D (10 uH) excluded; preferred first
    assert cat.pick(recipes.inductor(4.7e-6, 5.0))[0].mpn == "L-B"  # Isat filter
    assert cat.pick(recipes.inductor(4.7e-6, 3.5), qty=100)[1].unit_price == 0.30   # price tier at qty 100


def test_inductance_window(cat):
    # 4.7 uH design: 10 uH part is above the +50 % cap and excluded; a smaller-than-design part would be excluded by the minimum
    assert {c.mpn for c in cat.pick(recipes.inductor(4.7e-6, 3.5), limit=10)} == {"L-A", "L-B"}
    assert [c.mpn for c in cat.pick(recipes.inductor(9e-6, 3.5), limit=10)] == ["L-D"] and cat.pick(recipes.inductor(5.5e-6, 3.5, max_over=0.1)) == []


def test_dcr_and_units_with_conditions(cat):
    assert [c.mpn for c in cat.pick(recipes.inductor(4.7e-6, 3.5, dcr_max_ohm=0.025))] == ["L-B"]
    q = cat.pick(recipes.mosfet(60, 100, rds_on_max_ohm=0.005))
    assert [c.mpn for c in q] == ["Q-A"] and q[0].url == "https://www.lcsc.com/product-detail/C5.html"


def test_channel_equals_and_basic_first(cat):
    assert cat.pick(Need("MOSFETs", equals={"Type": "P-Channel"}, min_stock=10))[0].mpn == "Q-B"
    assert cat.pick(Need("MOSFETs", min_stock=10))[0].library_type == "base"


def test_helpers():
    assert parse_prices("1-49:0.0887,50-149:0.0707,6000-:0.0451") == [(1, 0.0887), (50, 0.0707), (6000, 0.0451)]
    assert price_at([(1, 1.0), (100, 0.5)], 250) == 0.5
    assert quantity("680mV@5A").to("V").magnitude == pytest.approx(0.68)
    assert quantity("-55℃~+125℃") is None and quantity("4.7µH").to("uH").magnitude == pytest.approx(4.7)


def test_missing_database_has_actionable_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="parts download"):
        Catalog(tmp_path / "nope.sqlite3")


def test_tvs_standoff_window(tmp_path):
    p = tmp_path / "t.sqlite3"
    db = sqlite3.connect(p)
    db.execute("""create table jlc_components (lcsc INTEGER PRIMARY KEY, category TEXT, subcategory TEXT, mfr TEXT, package TEXT, manufacturer TEXT,
                 library_type TEXT, preferred INTEGER, description TEXT, datasheet TEXT, stock INTEGER, price TEXT, attributes TEXT)""")
    for i, (v, price) in enumerate([(58, 0.10), (90, 0.02), (64, 0.05), (36, 0.01)], 1):
        db.execute("insert into jlc_components values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (i, "Circuit Protection", "ESD And Surge Protection (TVS/ESD)", f"T{v}", "SMB", "x", "expand", 0, "", "", 5000,
                                                                                    f"1-:{price}", json.dumps({"Reverse Stand-Off Voltage (Vrwm)": f"{v}V"})))
    db.commit()
    db.close()
    got = [c.mpn for c in Catalog(p).pick(recipes.tvs(58))]
    assert got == ["T64", "T58"]                      # 90 V excluded (too loose), 36 V excluded (too low); cheaper in-window first
