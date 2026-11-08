import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sparky.footprints import kicad_available  # noqa: E402
from sparky.kicad_symbols import symbols_available  # noqa: E402

needs_kicad_libs = pytest.mark.skipif(not (symbols_available() and kicad_available()), reason="KiCad symbol/footprint libraries not installed")

def mcu_circuit(lib=None):
    """STM32F103 + bypass caps + header, built with SKiDL from KiCad's libraries (no parts catalogue)."""
    from sparky.circuits import reset, skidl
    from sparky.circuits.passives import passive
    from sparky.design import from_skidl
    reset()
    s = skidl()
    gnd, v33 = s.Net("GND"), s.Net("+3V3")
    u = s.Part("MCU_ST_STM32F1", "STM32F103C8Tx", footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm")
    for p in u.pins:
        if p.name in ("VDD", "VDDA", "VBAT"):
            p += v33
        if p.name in ("VSS", "VSSA"):
            p += gnd
    for _ in range(4):
        v33 & passive("C", 100e-9, "0402") & gnd
    j = s.Part("Connector_Generic", "Conn_01x08", footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
    for i, n in enumerate(["PA9", "PA10", "PA2", "PA3", "PB6", "PB7"]):
        sig = s.Net("S_" + n)
        sig += [p for p in u.pins if p.name == n][0]
        sig += j[i + 1]
    gnd += j[7]
    v33 += j[8]
    return from_skidl(name="mcu_test")


@pytest.fixture(scope="session")
def counter_run(tmp_path_factory):
    """One real LibreLane run of the 8-bit counter on sky130A, shared by the silicon tests (about 70 s). Skips when the silicon tools / PDK are not configured."""
    from pathlib import Path

    from silicon import flow, spec, toolchain
    if not toolchain.available() or toolchain.pdk_root() is None:
        pytest.skip("silicon tools / PDK root not configured (tools/silicon_setup.sh)")
    s = spec.SiliconSpec("counter", [Path(__file__).parent / "silicon_fixtures" / "counter.v"], "clk", 20.0, "sky130A", die_area_um=(0, 0, 120, 120))
    return s, flow.run_flow(s, tmp_path_factory.mktemp("counter"), tag="shared")
