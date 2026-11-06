import pytest

from sparky.recipes import build_recipe
from sparky.recipes.ctx import Ctx
from sparky.toolchain import kicad_library_dir

pytestmark = pytest.mark.skipif(kicad_library_dir("symbols") is None, reason="KiCad libraries not installed")


def test_connector_breakout_names_each_pin_and_flags_ground():
    c = build_recipe("connector_breakout", dict(symbol="Connector_Generic:Conn_01x04", footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
                                                pins=["VBAT", "GND", "CANH", "CANL"], kind="power"), ctx=Ctx())
    assert [c.net_of(f"J1.{i}") for i in (1, 2, 3, 4)] == ["VBAT", "GND", "CANH", "CANL"]
    assert c.ports["GND"].kind == "ground" and c.ports["CANH"].kind == "power"


def test_pin_count_mismatch_is_an_error():
    with pytest.raises(ValueError):
        build_recipe("connector_breakout", dict(symbol="Connector_Generic:Conn_01x04", footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
                                                pins=["A", "B"]), ctx=Ctx())
