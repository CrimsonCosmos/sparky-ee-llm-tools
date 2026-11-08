import re

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


def test_microstrip_formula_by_hand():
    # IPC-2141: Z0 = 87/sqrt(er+1.41) * ln(5.98 h / (0.8 w + t)); er 4.5, h 0.4917, w 0.9104, t 0.035
    # sqrt(5.91) = 2.4310 ; 87/2.4310 = 35.787 ; 5.98*0.4917 = 2.9404 ; 0.8*0.9104+0.035 = 0.76332 ; ln(3.8520) = 1.3484 ; Z0 = 48.25 ohm
    assert krt.microstrip_z0(0.9104, 0.4917, 0.035, 4.5) == pytest.approx(48.25, abs=0.05)
    assert krt.microstrip_z0(0.5, 0.4917, 0.035, 4.5) > krt.microstrip_z0(0.9, 0.4917, 0.035, 4.5)     # narrower trace -> higher impedance


def test_hot_loop_follows_ref_renumbering():
    from sparky.constraints import HotLoop
    h = HotLoop("U1", "3", "1", ["C1", "C2"], 2.0, 0.3, 5e5, 5e-9)
    h.remap({"U1": "buck.U1", "C1": "buck.C1"}, {})
    assert h.ref == "buck.U1" and h.cap_refs == ["buck.C1", "C2"]


def test_restore_nets_puts_krt_copper_back_and_names_the_nets():
    from sparky import krt
    krt_text = '(kicad_pcb (net 0 "") (net 1 "A") (net 2 "B")\n\t(segment (start 0 0) (end 5 0) (width 0.2) (layer "F.Cu") (net 1))\n\t(segment (start 0 1) (end 9 1) (width 0.2) (layer "F.Cu") (net 2))\n)\n'
    routed = ('(kicad_pcb\n\t(segment (start 0 0) (end 2 0) (width 0.2) (layer "F.Cu") (net "A"))\n\t(segment (start 2 0) (end 5.5 0.5) (width 0.2) (layer "F.Cu") (net "A"))\n'
              '\t(segment (start 0 3) (end 4 3) (width 0.2) (layer "F.Cu") (net "B"))\n)\n')
    out = krt.restore_nets(krt_text, routed, {"A"})
    segs = [(a, b) for a, b in re.findall(r'\(segment \(start ([\d. ]+)\) \(end ([\d. ]+)\)[^\n]*\(net "A"\)', out)]
    assert segs == [("0 0", "5 0")]                                                     # the second router's two pieces replaced by KRT's one
    assert '(start 0 3) (end 4 3)' in out and out.count('(net "B")') == 1               # the other net is untouched (and KRT's B copper is not imported)
    assert out.rstrip().endswith(")") and out.count("(") == out.count(")")


def test_restore_nets_writes_ids_into_a_file_that_has_a_net_table():
    from sparky import krt
    krt_text = '(kicad_pcb (net 0 "") (net 7 "A")\n\t(segment (start 0 0) (end 5 0) (width 0.2) (layer "F.Cu") (net 7))\n)\n'
    base = '(kicad_pcb (net 0 "") (net 1 "A") (net 2 "B")\n)\n'
    out = krt.restore_nets(krt_text, base, {"A"})
    assert '(layer "F.Cu") (net 1))' in out                                              # A is net 1 in THIS file, not 7 as in KRT's
