import pytest

from sparky import krt
from sparky.constraints import DiffPair, LengthGroup
from sparky.sexpr import Q, dump


def board(segs, vias=()):
    root = ["kicad_pcb", ["net", 0, Q("")], ["net", 1, Q("A")], ["net", 2, Q("B")]]
    root += [["segment", ["start", *a], ["end", *b], ["width", 0.2], ["layer", Q("F.Cu")], ["net", n]] for n, a, b in segs]
    root += [["via", ["at", 0, 0], ["size", 0.5], ["drill", 0.3], ["layers", Q(a), Q(b)], ["net", n]] for n, a, b in vias]
    return dump(root)


def test_lengths_are_segments_plus_via_barrels_by_hand():
    # net A: 3-4-5 triangle legs 3 + 4 = 7 mm of track ; a through via F.Cu -> B.Cu at heights 0.0 and 1.6 mm adds 1.6 mm
    t = board([(1, (0, 0), (3, 0)), (1, (3, 0), (3, 4))], vias=[(1, "F.Cu", "B.Cu")])
    L = krt.net_lengths(t, {"F.Cu": 0.0, "B.Cu": 1.6})
    assert L["A"] == pytest.approx(8.6)
    assert krt.net_lengths(t)["A"] == pytest.approx(7.0)                      # without a stackup the via adds nothing


def test_verify_takes_the_verdict_from_kicads_drc_and_keeps_our_measurement():
    t = board([(1, (0, 0), (10, 0)), (2, (0, 1), (10.4, 1))])
    cons = [DiffPair("A", "B", 0.2, 0.2, 0.1), LengthGroup(["A", "B"], 0.3)]
    violated = [{"description": "Skew between tracks out of range (rule 'sparky_0' max skew 0.1000 mm; actual -0.4000 mm; target net length 10.4)"}]
    rows = krt.verify(t, cons, {}, violated)
    assert rows[0]["ok"] is False and rows[0]["kicad"] == pytest.approx(0.4) and rows[0]["skew_mm"] == pytest.approx(0.4)    # KiCad flagged the pair
    assert rows[1]["ok"] is True and rows[1]["kicad"] is None                                                             # no violation for rule sparky_1
    assert not krt.verify(t, [LengthGroup(["A", "MISSING"], 5.0)], {}, [])[0]["ok"]                                       # an unrouted net can never meet a group


def test_dru_has_one_named_rule_per_constraint_and_kicad_syntax():
    txt = krt.dru_text([DiffPair("DATA_P", "DATA_N", 0.2, 0.2, 0.1), LengthGroup(["S1", "S2"], 0.5)])
    assert txt.startswith("(version 1)")
    assert "(rule \"sparky_0\"" in txt and "A.inDiffPair('DATA')" in txt and "(constraint skew (max 0.1mm))" in txt
    assert "(rule \"sparky_1\"" in txt and "A.NetName == 'S1' || A.NetName == 'S2'" in txt and "(max 0.5mm)" in txt


def test_constraints_follow_net_renames_and_merges():
    d = DiffPair("X_P", "X_N", 0.2, 0.2, 0.1)
    d.rename_net("X_P", "mcu.X_P")
    d.remap({}, {"X_N": "mcu.X_N"})
    g = LengthGroup(["a", "b"], 0.5)
    g.remap({}, {"a": "blk.a", "b": "blk.b"})
    assert (d.p, d.n) == ("mcu.X_P", "mcu.X_N") and g.nets == ["blk.a", "blk.b"]
