"""JLCPCB placement corrections, expected values worked by hand from the documented rule (not from the code under test)."""
import pytest

from sparky.export import jlc_rotation as jr

TABLE = '"Regex To Match","Rotation","Delta X","Delta Y"\n"^LQFP-",270,0,0\n"^SOT-23",180,1,0\n"Lib:Special",90,0,2\n'
ROWS = jr.parse(TABLE)


def test_parse_and_first_match_semantics():
    assert [r.pattern for r in ROWS] == ["^LQFP-", "^SOT-23", "Lib:Special"]
    assert jr.lookup(ROWS, "Package_QFP:LQFP-48_7x7mm_P0.5mm").rotation == 270       # name part after the colon
    assert jr.lookup(ROWS, "Package_TO_SOT_SMD:SOT-23-5").pattern == "^SOT-23"
    assert jr.lookup(ROWS, "Lib:Special").rotation == 90                              # a ':' in the pattern matches the full id
    assert jr.lookup(ROWS, "Device:R_0402") is None


def test_top_part_adds_the_table_rotation_and_rotates_the_offset():
    row = jr.lookup(ROWS, "Package_TO_SOT_SMD:SOT-23-5")
    x, y, r = jr.correct(10.0, 20.0, 90.0, False, row)                  # offset (1, 0) rotated by 90 deg -> (0, 1); rotation 90 + 180 = 270
    assert (x, y, r) == pytest.approx((10.0, 21.0, 270.0))


def test_bottom_part_is_mirrored_as_documented():
    row = jr.lookup(ROWS, "Package_TO_SOT_SMD:SOT-23-5")
    x, y, r = jr.correct(10.0, 20.0, 0.0, True, row)                    # bottom: rotation 180 - 0 + 180 = 360 -> 0; offset (1, 0): (1*1 + 0, 0 - 0) = (1, 0)
    assert (x, y, r) == pytest.approx((11.0, 20.0, 0.0))
    assert jr.correct(0, 0, 90.0, True, None)[2] == pytest.approx(90.0)  # no row: 180 - 90


def test_no_row_on_top_changes_nothing():
    assert jr.correct(3.0, 4.0, 45.0, False, None) == (3.0, 4.0, 45.0)


def test_cpl_uses_the_rows_only_when_given(tmp_path):
    from conftest import mcu_circuit
    from sparky.export.bom import cpl_csv
    from sparky.pcb.placement import PlaceOptions, place
    from sparky.pcb.rules import Rules
    b = place(mcu_circuit(), Rules(layers=2), PlaceOptions(seed=1))
    plain = {l.split(",")[0]: l.split(",") for l in cpl_csv(b).strip().splitlines()[1:]}
    fixed = {l.split(",")[0]: l.split(",") for l in cpl_csv(b, ROWS).strip().splitlines()[1:]}
    assert float(fixed["U1"][5]) == pytest.approx((float(plain["U1"][5]) + 270) % 360)     # LQFP row
    assert fixed["C1"][5] == plain["C1"][5]                                                 # 0402 capacitor: no row
