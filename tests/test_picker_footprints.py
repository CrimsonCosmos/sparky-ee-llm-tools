import pytest

from sparky.footprints import kicad_dir
from sparky.picker import footprints as f

pytestmark = pytest.mark.skipif(kicad_dir() is None, reason="KiCad footprint libraries not installed")


def test_chip_codes_resolve_through_the_library_not_a_table():
    assert f.chip("Capacitor_SMD", "C", "0603") == "Capacitor_SMD:C_0603_1608Metric"      # 0603 imperial = 1608 metric, supplied by the library name
    assert f.chip("Resistor_SMD", "R", "0402") == "Resistor_SMD:R_0402_1005Metric"
    assert f.chip("Capacitor_SMD", "C", "9999") is None                                    # unknown: no guess


def test_electrolytic_takes_same_diameter_nearest_height_at_or_above():
    assert f.electrolytic("SMD,D10xL10.2mm") == "Capacitor_SMD:CP_Elec_10x10.5"           # 10 mm can: 10.5 is the nearest library height >= 10.2
    assert f.electrolytic("SMD,D6.3xL7.7mm") == "Capacitor_SMD:CP_Elec_6.3x7.7"
    assert f.electrolytic("through hole") is None


def test_diode_package_token_names_the_footprint():
    assert f.diode("SMB(DO-214AA)") == "Diode_SMD:D_SMB" and f.diode("DO-214AC(SMA)") == "Diode_SMD:D_SMA"
    assert f.diode("SOT-23") is None
