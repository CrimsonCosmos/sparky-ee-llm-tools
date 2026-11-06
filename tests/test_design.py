from conftest import mcu_circuit


def test_pin_electrical_types_come_from_the_kicad_symbol():
    c = mcu_circuit()
    pins = {p.name + p.number: p.etype for p in c.components["U1"].part.pins}
    assert pins["VDD24"] == "power_in" and pins["VSS23"] == "power_in" and pins["VBAT1"] == "power_in"      # STM32F103 symbol: supply/ground pins
    assert pins["PA930"] == "bidir"                                                                          # GPIO


def test_placement_hints_are_derived_from_topology():
    c = mcu_circuit()
    dec = {k.cap: k.pin for k in c.constraints if hasattr(k, "cap")}
    assert sorted(dec.values()) == ["U1.24", "U1.36", "U1.48", "U1.9"]          # the 4 caps are spread over 4 distinct IC supply pins on +3V3 (VDD, VDD, VDD, VDDA)
    assert c.net_attrs["GND"].role == "ground" and c.net_attrs["+3V3"].role == "power"
