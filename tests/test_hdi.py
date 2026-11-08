"""Blind / buried / micro vias: stackup arithmetic, DSN patching, via typing, rule text and the independent span checker (fast, no KiCad), then the whole flow
through Freerouting and KiCad DRC with the drill files opened by gerbonara (slow)."""
import re

import pytest

from conftest import mcu_circuit
from sparky import kicad_backend, routing
from sparky.export.kicad import layer_z_mm
from sparky.pcb import hdi
from sparky.pcb.rules import OZ_CU_MM, ViaSpan, dielectric_gaps, rules_for

LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
R = rules_for(6, "generic-hdi-6l")
MICRO, BLIND, BURIED = R.via_spans[0], R.via_spans[2], R.via_spans[3]


def test_micro_via_gap_is_what_its_aspect_ratio_allows_by_hand():
    cu_out, cu_in = R.copper_oz_outer * OZ_CU_MM, R.copper_oz_inner * OZ_CU_MM
    # micro 1-2: drill 0.1 mm x aspect 1 = 0.1 mm centre-to-centre; the copper between the centres takes (35 + 17.5) / 2 um
    assert dielectric_gaps(R)[0] == pytest.approx(0.1 - (cu_out + cu_in) / 2)
    # blind 1-3: drill 0.2 x 1 = 0.2 mm over two gaps, crossing In1's copper plus half of each end layer's
    assert dielectric_gaps(R)[1] == pytest.approx((0.2 - (cu_in + (cu_out + cu_in) / 2)) / 2)
    assert all(g <= c + 1e-12 for g, c in zip(dielectric_gaps(R), [0.1 - (cu_out + cu_in) / 2, None, None, None, None]) if c is not None)
    total = sum(dielectric_gaps(R)) + 2 * cu_out + 4 * cu_in + 2 * R.mask_thickness_mm
    assert total == pytest.approx(R.thickness_mm, abs=1e-6)                    # the board is exactly as thick as the stackup says
    z = layer_z_mm(R, 6)
    assert z["In1.Cu"] - z["F.Cu"] == pytest.approx(0.1)                       # the exporter's z positions give the micro via exactly its allowed depth


def test_a_span_the_fab_cannot_stock_is_refused():
    thin = ViaSpan("micro", 0, 1, 0.3, 0.05, 0.1, 1.0)                         # 50 um depth leaves a 24 um dielectric: below what the profile stocks
    with pytest.raises(ValueError, match="thinner than the fab stocks"):
        rules_for(6, "generic-hdi-6l", via_spans=(thin,))
    with pytest.raises(ValueError, match="smaller than the copper"):
        rules_for(6, "generic-hdi-6l", via_spans=(ViaSpan("micro", 0, 1, 0.3, 0.02, 0.1, 1.0),))


DSN = """(pcb x
  (structure
    (via "Via[0-5]_500:250_um")
    (rule (width 150))
  )
  (library
    (padstack "Via[0-5]_500:250_um"
      (shape (circle F.Cu 500))
      (attach off)
    )
  )
  (network
    (class kicad_default A (circuit (use_via "Via[0-5]_500:250_um")) (rule (width 150)))
  )
)
"""


def test_dsn_offers_partial_vias_before_the_through_via():
    out = hdi.patch_dsn(DSN, R.via_spans, LAYERS)
    assert '(via "Via[0-1]_300:100_um" "Via[4-5]_300:100_um" "Via[0-2]_400:200_um" "Via[1-4]_350:200_um" "Via[0-5]_500:250_um")' in out
    assert '(use_via "Via[0-1]_300:100_um"' in out and out.count("(padstack ") == 5
    blind = re.search(r'\(padstack "Via\[0-2\]_400:200_um"(.*?)\(attach off\)', out, re.S).group(1)
    assert [m for m in re.findall(r"circle (\S+) 400", blind)] == ["F.Cu", "In1.Cu", "In2.Cu"]       # copper on the span's layers only
    assert hdi.patch_dsn(DSN, (), LAYERS) == DSN


def _via(kind, a, b, size=0.3, drill=0.1, at="1 2"):
    return f'\t(via{(" " + kind) if kind else ""}\n\t\t(at {at})\n\t\t(size {size})\n\t\t(drill {drill})\n\t\t(layers "{a}" "{b}")\n\t\t(net "GND")\n\t)\n'


def test_via_types_are_restored_from_the_span_table():
    text = "(kicad_pcb\n" + _via("buried", "F.Cu", "In1.Cu") + _via("buried", "F.Cu", "In2.Cu", 0.4, 0.2) + _via("buried", "In1.Cu", "In4.Cu", 0.35, 0.2) + _via("", "F.Cu", "B.Cu", 0.5, 0.25) + ")\n"
    out = hdi.retype_vias(text, R.via_spans, 6, LAYERS)
    assert re.findall(r"\(via( \w+)?\n", out) == [" micro", " blind", " buried", ""]


def test_dru_gives_each_type_its_own_limits():
    d = hdi.dru_text(R.via_spans, R)
    assert "Via_Type == 'Through'" in d and f"(min {R.min_drill:g}mm)" in d            # through vias keep the through limits
    assert "Via_Type == 'Micro'" in d and "(constraint hole_size (min 0.1mm))" in d
    assert "Via_Type == 'Blind/buried'" in d and "(constraint hole_size (min 0.2mm))" in d


def test_checker_finds_what_the_table_forbids():
    z = layer_z_mm(R, 6)
    good = "(kicad_pcb\n" + _via("micro", "F.Cu", "In1.Cu") + ")\n"
    assert hdi.check(good, R, z, LAYERS) == ([], {"micro 1-2": 1})
    wrong_span = "(kicad_pcb\n" + _via("blind", "F.Cu", "In3.Cu", 0.4, 0.2) + ")\n"
    assert "not one the fab offers" in hdi.check(wrong_span, R, z, LAYERS)[0][0].problem
    wrong_type = "(kicad_pcb\n" + _via("blind", "F.Cu", "In1.Cu") + ")\n"
    assert "typed blind" in hdi.check(wrong_type, R, z, LAYERS)[0][0].problem
    small = "(kicad_pcb\n" + _via("micro", "F.Cu", "In1.Cu", 0.3, 0.05) + ")\n"
    assert "below the span's" in hdi.check(small, R, z, LAYERS)[0][0].problem
    deep = "(kicad_pcb\n" + _via("buried", "In1.Cu", "In4.Cu", 0.35, 0.05) + ")\n"                 # 0.05 mm drill through a 1.1 mm span: aspect far over 8
    assert any("aspect ratio" in f.problem for f in hdi.check(deep, R, z, LAYERS)[0])


# ---- slow: the real flow ---------------------------------------------------------------------------------------------------------------
needs = pytest.mark.skipif(not (kicad_backend.available() and routing.available()), reason="KiCad / Java missing")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from sparky.export.package import write_package
    from sparky.pcb.flow import BoardSpec, build_board
    res = build_board(mcu_circuit(), BoardSpec(layers=6, profile="generic-hdi-6l"), log=None)
    out = tmp_path_factory.mktemp("hdi")
    return res, out, write_package(res, out, "h")


@pytest.mark.slow
@needs
def test_hdi_board_routes_clean_with_partial_vias_and_matching_drill_files(built):
    gerbonara = pytest.importorskip("gerbonara")
    res, out, files = built
    assert res.success, res.drc.summary()
    chk = next(c for c in res.routing_checks if c["kind"] == "via_span")
    assert chk["ok"] and sum(chk["counts"].values()) > 0                    # partial vias really were used, all inside the table
    # the drill files: one per span in use, hole count and size equal to the vias in the copper (opened independently)
    n_micro = chk["counts"]["micro 1-2"]
    drl = gerbonara.ExcellonFile.open(str(out / "gerber" / "h-front-in1.drl"))
    assert len(drl.objects) == n_micro and all(o.aperture.diameter == pytest.approx(0.1) for o in drl.objects)
    assert "Partial-depth vias" in (out / "REPORT.md").read_text()


@pytest.mark.slow
@needs
def test_kicad_drc_and_our_checker_both_object_to_a_via_the_fab_cannot_drill(built, tmp_path):
    res, out, _ = built
    txt = (out / "h.kicad_pcb").read_text()
    bad = re.sub(r"(\(via micro\s+\(at [^)]*\)\s+\(size )0\.3(\)\s+\(drill )0\.1\)", r"\g<1>0.3\g<2>0.05)", txt, count=1)
    assert bad != txt
    (tmp_path / "bad.kicad_pcb").write_text(bad)
    for ext in (".kicad_pro", ".kicad_dru"):
        (tmp_path / f"bad{ext}").write_bytes((out / f"h{ext}").read_bytes()) if (out / f"h{ext}").exists() else None
    rep = kicad_backend.run_drc(tmp_path / "bad.kicad_pcb", refill=False, rules=res.board.rules)
    assert any(v["type"] in ("microvia_drill_out_of_range", "hole_size", "drill_out_of_range", "annular_width") for v in rep.errors)
    found, _ = hdi.check(bad, res.board.rules, layer_z_mm(res.board.rules, 6), LAYERS)
    assert any("below the span's" in f.problem for f in found)


def test_without_spans_the_stackup_is_unchanged_and_sums_to_the_thickness():
    r = rules_for(6)
    g = dielectric_gaps(r)
    assert g == pytest.approx([g[0]] * 5)                                         # equal cores, as before
    assert sum(g) + 2 * r.copper_oz_outer * OZ_CU_MM + 4 * r.copper_oz_inner * OZ_CU_MM + 2 * r.mask_thickness_mm == pytest.approx(r.thickness_mm)


def test_a_board_too_thick_for_its_via_aspect_ratios_gets_thinner_not_wrong():
    assert R.thickness_mm < 1.6                                                   # the thin micro/blind gaps plus the buried span's cap bound the board (derived, ~1.4 mm)
    assert max(dielectric_gaps(R)[2:4]) <= (R.via_spans[3].drill * R.via_spans[3].max_aspect) / 3


def test_stacked_partial_vias_are_flagged_unless_the_fab_allows_them():
    z = layer_z_mm(R, 6)
    stack = "(kicad_pcb\n" + _via("micro", "F.Cu", "In1.Cu", at="5 5") + _via("buried", "In1.Cu", "In4.Cu", 0.35, 0.2, at="5 5") + ")\n"
    assert any("stacked" in f.problem for f in hdi.check(stack, R, z, LAYERS)[0])
    apart = "(kicad_pcb\n" + _via("micro", "F.Cu", "In1.Cu", at="5 5") + _via("buried", "In1.Cu", "In4.Cu", 0.35, 0.2, at="5.6 5") + ")\n"
    assert not any("stacked" in f.problem for f in hdi.check(apart, R, z, LAYERS)[0])
    from dataclasses import replace
    ok = tuple(replace(sp, stackable=True) for sp in R.via_spans)
    r2 = rules_for(6, "generic-hdi-6l", via_spans=ok)
    assert not any("stacked" in f.problem for f in hdi.check(stack, r2, z, LAYERS)[0])


def test_lamination_sequence_follows_the_span_table():
    steps = hdi.lamination_sequence(R, LAYERS)
    assert steps[0].startswith("laminate layers In1.Cu..In4.Cu") and "buried span 2-5" in steps[0]      # buried span first
    assert steps[1].startswith("press the remaining layers") and steps[-1] == "drill and plate the through holes"
    assert sum("(laser)" in t for t in steps) == 2                                                     # two micro spans
    assert hdi.lamination_sequence(rules_for(6), LAYERS) == ["press the remaining layers on (final lamination)", "drill and plate the through holes"]


@pytest.mark.slow
@needs
@pytest.mark.skipif(not __import__("sparky.krt", fromlist=["x"]).available(), reason="needs KiCadRoutingTools (SPARKY_KRT)")
def test_hdi_board_with_a_pair_and_a_length_group_stages_krt_then_freerouting():
    """KiCadRoutingTools routes the constrained nets with through vias; Freerouting finishes the rest with the partial-depth padstacks. The constraints are
    re-measured from the FINAL copper, so a router that disturbed them would fail here."""
    from sparky.constraints import DiffPair, Fanout, LengthGroup
    from sparky.pcb.flow import BoardSpec, build_board
    from test_krt import make_circuit
    c = make_circuit()
    c.constrain(Fanout("U1"))
    c.constrain(DiffPair("DATA_P", "DATA_N", width_mm=0.2, gap_mm=0.2, skew_mm=0.1))
    c.constrain(LengthGroup(["S1", "S2", "S3"], tolerance_mm=1.0))
    res = build_board(c, BoardSpec(layers=6, profile="generic-hdi-6l", time_budget_s=400), log=print)
    by = {r["name"]: r for r in res.routing_checks}
    assert not res.drc.unconnected and not res.drc.errors, res.drc.summary()
    pair = by["DATA_P/DATA_N"]
    assert pair["lengths_mm"][0] > 5 and pair["ok"] and pair["skew_mm"] <= 0.15              # the pair survived Freerouting's pass
    assert by["partial-depth vias"]["ok"]


def test_existing_copper_of_chosen_nets_is_protected_in_the_dsn():
    from sparky.routing import protect_nets
    dsn = ('(pcb x (wiring\n    (wire (path F.Cu 300  1 2  3 4)(net S1)(type route))\n    (wire (path F.Cu 300  1 2\n  3 4)(net GND)(type route))\n'
           '    (via "Via[0-5]_500:250_um" 5 6 (net S1)(type route))\n  )\n)\n')
    out = protect_nets(dsn, {"S1"})
    assert out.count("(type protect)") == 2 and out.count("(type route)") == 1                # S1's wire and via fixed, GND's wire left rippable
    assert "(net GND)(type route)" in out and protect_nets(dsn, set()) == dsn


def test_dsn_with_several_through_via_sizes_is_patched_too():
    dsn = DSN.replace('(via "Via[0-5]_500:250_um")', '(via "Via[0-5]_600:300_um" "Via[0-5]_500:250_um")').replace('(use_via "Via[0-5]_500:250_um")', '(use_via "Via[0-5]_600:300_um")')
    out = hdi.patch_dsn(dsn, R.via_spans, LAYERS)
    assert out.index('"Via[0-1]_300:100_um"') < out.index('"Via[0-5]_600:300_um"')                       # partial vias first in both lists
    assert '(use_via "Via[0-1]_300:100_um" "Via[4-5]_300:100_um" "Via[0-2]_400:200_um" "Via[1-4]_350:200_um" "Via[0-5]_600:300_um")' in out
