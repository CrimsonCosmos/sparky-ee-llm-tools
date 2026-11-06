import json
import sqlite3

import pytest

from sparky import datasheet
from sparky.picker import Catalog
from sparky.recipes import build_recipe
from sparky.recipes.ctx import Ctx
from sparky.toolchain import kicad_library_dir

pytestmark = pytest.mark.skipif(kicad_library_dir("symbols") is None, reason="KiCad libraries not installed")
SYM = "MCU_ST_STM32F1:STM32F103C8Tx"
ARGS = dict(symbol=SYM, vdd=3.3, signals={"UART_TX": {"function": "USART2_TX", "kind": "uart"}, "UART_RX": {"function": "USART2_RX", "kind": "uart"},
                                          "DBG_TX": {"pin": "PA9", "kind": "uart"}},
            swd_header_symbol="Connector_Generic:Conn_01x04", swd_header_footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_HOME", str(tmp_path / "home"))
    p = tmp_path / "db.sqlite3"
    c = sqlite3.connect(p)
    c.execute("""create table jlc_components (lcsc INTEGER PRIMARY KEY, category TEXT, subcategory TEXT, mfr TEXT, package TEXT, manufacturer TEXT,
                 library_type TEXT, preferred INTEGER, description TEXT, datasheet TEXT, stock INTEGER, price TEXT, attributes TEXT)""")
    rows = [("Multilayer Ceramic Capacitors MLCC - SMD/SMT", "C100N", "0402", {"Capacitance": "100nF", "Voltage Rating": "16V"}),
            ("Multilayer Ceramic Capacitors MLCC - SMD/SMT", "C4U7", "0603", {"Capacitance": "4.7uF", "Voltage Rating": "16V"}),
            ("Multilayer Ceramic Capacitors MLCC - SMD/SMT", "C10N", "0402", {"Capacitance": "10nF", "Voltage Rating": "16V"}),
            ("Multilayer Ceramic Capacitors MLCC - SMD/SMT", "C1U", "0603", {"Capacitance": "1uF", "Voltage Rating": "16V"}),
            ("Chip Resistor - Surface Mount", "R10K", "0402", {"Resistance": "10kohm", "Tolerance": "1%"})]
    for i, (sub, mpn, pkg, attrs) in enumerate(rows, 1):
        c.execute("insert into jlc_components values (?,?,?,?,?,?,?,?,?,?,?,?,?)", (i, "x", sub, mpn, pkg, "m", "base", 1, "", "", 9000, "1-:0.1", json.dumps(attrs)))
    c.commit()
    c.close()
    datasheet.save("STM32F103C8Tx", {"vdd_min": 2.0, "vdd_max": 3.6, "c_bypass_pin": 100e-9, "c_bulk": 4.7e-6, "c_nrst": 100e-9, "r_boot0": 10e3,
                                     "c_bypass@VDDA#1": 10e-9, "c_bypass@VDDA#2": 1e-6}, "test fixture (ST DS5319 Figure 14 for supply; NRST/BOOT0 values are test inputs)")
    return Catalog(p)


def test_supply_pins_bypass_swd_and_signals(env):
    c = build_recipe("mcu_core", ARGS, ctx=Ctx(env))
    u = next(r for r, comp in c.components.items() if comp.part.mpn == "STM32F103C8Tx")
    pn = {p.number: p.name for p in c.components[u].part.pins}
    net = lambda name: [n for num, nm in pn.items() if nm == name for n in [c.net_of(f"{u}.{num}")]]
    assert net("VBAT") == ["+3V3"] and net("VDDA") == ["+3V3"] and net("VDD") == ["+3V3"] * 3
    assert net("VSS") == ["GND"] * 3 and net("VSSA") == ["GND"]
    vals = sorted(x.value for x in c.components.values() if x.part.category == "capacitor")
    # ST DS5319 Figure 14: 100 nF on each of VBAT + 3 x VDD (4), VDDA gets 10 nF + 1 uF instead, 4.7 uF bulk, + the NRST capacitor (100 nF)
    assert vals.count("100n") == 5 and vals.count("10n") == 1 and vals.count("1u") == 1 and vals.count("4.7u") == 1
    assert net("PA2") == ["UART_TX"] and net("PA3") == ["UART_RX"] and net("PA9") == ["DBG_TX"]      # STM32F1: USART2 = PA2/PA3
    assert net("PA13") == ["SWDIO"] and net("PA14") == ["SWCLK"]                                      # STM32F1 SWD pins


def test_unknown_signal_function_is_an_error(env):
    with pytest.raises(LookupError):
        build_recipe("mcu_core", dict(ARGS, signals={"X": {"function": "NOSUCH_FUNCTION"}}), ctx=Ctx(env))


def test_ambiguous_function_requires_an_explicit_pin(env):
    with pytest.raises(LookupError):
        build_recipe("mcu_core", dict(ARGS, signals={"TX": {"function": "USART1_TX"}}), ctx=Ctx(env))    # PA9 or PB6 (remap)


def test_unspecified_nrst_and_boot0_values_are_warned_about_not_invented(env):
    datasheet.save("STM32F103C8Tx", {"vdd_min": 2.0, "vdd_max": 3.6, "c_bypass_pin": 100e-9, "c_bulk": 4.7e-6}, "test fixture")
    from sparky.datasheet import _file
    import json
    f = _file("STM32F103C8Tx")
    rec = json.loads(f.read_text())
    rec["values"] = {k: v for k, v in rec["values"].items() if k not in ("c_nrst", "r_boot0") and not k.startswith("c_bypass@")}
    f.write_text(json.dumps(rec))
    c = build_recipe("mcu_core", ARGS, ctx=Ctx(env))
    assert any("NRST capacitor NOT fitted" in w for w in c.warnings) and any("BOOT0 is FLOATING" in w for w in c.warnings)
    assert not [x for x in c.components.values() if x.part.category == "resistor"]               # no pull-down was invented
