"""SKiDL layer: needs KiCad's symbol libraries (skipped when absent)."""
import pytest

from sparky.toolchain import kicad_library_dir

pytestmark = [pytest.mark.skipif(kicad_library_dir("symbols") is None, reason="KiCad libraries not installed")]


def test_buck_circuit_from_equations_uses_kicad_pins_by_name(tmp_path):
    from sparky.circuits import skidl
    from sparky.circuits.buck import build_buck, pins
    from sparky.equations.buck import BuckSpec, design_buck
    s = skidl()
    s.reset()
    d = design_buck(BuckSpec(9, 14, 3.3, 2.0, 580e3, 0.768, l_range=(2.2e-6, 4.7e-6)))
    vin, vout, gnd = s.Net("VIN"), s.Net("+3V3"), s.Net("GND")
    blk = build_buck(d, ic_lib="Regulator_Switching", ic_name="TPS563201", vin=vin, vout=vout, gnd=gnd)
    ic = blk.parts["ic"]
    assert [p.name for p in pins(ic, "vin")] == ["VIN"] and [p.name for p in pins(ic, "boot")] == ["VBST"]
    assert ic["VIN"].net is vin and ic["GND"].net is gnd and ic["EN"].net is vin
    assert blk.parts["inductor"].value == "4.7u" and blk.parts["r_top"].value == "33.2k"
    out = tmp_path / "buck.net"
    s.generate_netlist(file_=str(out))
    txt = out.read_text()
    assert "TPS563201" in txt and '"+3V3"' in txt and "4.7u" in txt
