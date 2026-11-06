from conftest import mcu_circuit


def test_pin_electrical_types_come_from_the_kicad_symbol():
    c = mcu_circuit()
    pins = {p.name + p.number: p.etype for p in c.components["U1"].part.pins}
    assert pins["VDD24"] == "power_in" and pins["VSS23"] == "power_in" and pins["VBAT1"] == "power_in"      # STM32F103 symbol: supply/ground pins
    assert pins["PA930"] == "bidir"                                                                          # GPIO


def test_placement_hints_are_derived_from_topology():
    c = mcu_circuit()
    dec = {k.cap: k.pin for k in c.constraints if hasattr(k, "cap")}
    assert [dec[f"C{i}"] for i in (1, 2, 3, 4)] == ["U1.1", "U1.9", "U1.24", "U1.36"]   # 4 caps -> the first 4 of the IC's 5 supply pins in natural pin order (reproducible)
    assert c.net_attrs["GND"].role == "ground" and c.net_attrs["+3V3"].role == "power"
