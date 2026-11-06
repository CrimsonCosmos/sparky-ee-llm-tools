import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sparky.parts.db import PartsLibrary  # noqa: E402


@pytest.fixture
def lib(tmp_path):
    return PartsLibrary(tmp_path / "home", auto_research=False)


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
