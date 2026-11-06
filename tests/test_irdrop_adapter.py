from sparky.analysis.irdrop import Rail


def test_rail_spec():
    assert Rail("3V3", "U1", 1.5, "U2").spec() == "3V3:U1:1.5:U2::"
    assert Rail("1V0", "R1", 3, "U3", sense="R9", vnom=1.0).spec() == "1V0:R1:3:U3:R9:1"
