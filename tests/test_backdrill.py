"""Back-drill planning and checking, against hand-worked stackup arithmetic; the drilled depth is cross-checked against the number KiCad itself prints in its drill file."""
import re
import subprocess

import pytest

from sparky import toolchain
from sparky.export.kicad import layer_z_mm
from sparky.pcb import backdrill
from sparky.pcb.rules import OZ_CU_MM, Backdrill, dielectric_gaps, rules_for

R = rules_for(6, "jlcpcb-6l", backdrill=Backdrill(0.2, 0.1, 0.45))
NAMES = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
Z = layer_z_mm(R, 6)


def _board(used_layers, net="N", at=(10, 10)):
    segs = "".join(f'(segment (start {at[0]} {at[1]}) (end {at[0] + 2} {at[1]}) (width 0.15) (layer "{l}") (net "{net}"))' for l in used_layers)
    via = f'(via (at {at[0]} {at[1]}) (size 0.5) (drill 0.25) (layers "F.Cu" "B.Cu") (net "{net}"))'
    return f"(kicad_pcb {segs} {via})"


def test_a_stub_longer_than_the_fab_allows_gets_the_nearest_end_layer_that_respects_the_tolerance_by_hand():
    cu_in, gap = R.copper_oz_inner * OZ_CU_MM, dielectric_gaps(R)[0]
    text = _board(["F.Cu", "In1.Cu"])                                  # signal goes F.Cu -> In1.Cu: stub below In1 is ~1.2 mm
    jobs, findings = backdrill.plan(text, R, {"N"}, NAMES, Z, [])
    assert not findings and len(jobs) == 1 and jobs[0].side == "bottom"
    # E = In1 leaves 0 (< tolerance 0.1); E = In2 leaves one dielectric gap plus In2's copper: 0.288 + 0.0175 = 0.3055 mm: the nearest that is >= 0.1 and <= 0.45
    assert jobs[0].end_layer == "In2.Cu" and jobs[0].residual_mm == pytest.approx(gap + cu_in, abs=1e-9)
    assert jobs[0].depth_mm == pytest.approx((R.thickness_mm - R.mask_thickness_mm) - (Z["In2.Cu"] + cu_in / 2), abs=1e-9)


def test_short_stubs_and_full_depth_vias_are_left_alone():
    assert backdrill.plan(_board(["F.Cu", "In4.Cu"]), R, {"N"}, NAMES, Z, [])[0] == []           # stub below In4 is only ~0.3 mm
    assert backdrill.plan(_board(["F.Cu", "B.Cu"]), R, {"N"}, NAMES, Z, [])[0] == []             # used end to end: nothing to remove


def test_a_fab_that_cannot_leave_a_short_enough_stub_is_reported():
    tight = rules_for(6, "jlcpcb-6l", backdrill=Backdrill(0.2, 0.1, 0.2))                          # longest stub 0.2 < the 0.3055 the nearest feasible end layer leaves
    jobs, findings = backdrill.plan(_board(["F.Cu", "In1.Cu"]), tight, {"N"}, NAMES, layer_z_mm(tight, 6), [])
    assert jobs == [] and "over the fab's" in findings[0].problem
    none = rules_for(6, "jlcpcb-6l")
    assert "offers no back-drilling" in backdrill.plan(_board(["F.Cu", "In1.Cu"]), none, {"N"}, NAMES, Z, [])[1][0].problem


def test_apply_writes_kicads_blocks_and_check_accepts_them_and_catches_a_bad_one():
    text = _board(["F.Cu", "In1.Cu"])
    jobs, _ = backdrill.plan(text, R, {"N"}, NAMES, Z, [])
    done = backdrill.apply(text, jobs, None, R.backdrill.oversize_mm)
    assert '(tertiary_drill\n\t\t\t(size 0.45)\n\t\t\t(layers "B.Cu" "In2.Cu")' in done                # via drill 0.25 + oversize 0.2
    assert backdrill.check(done, R, NAMES, Z, {"N"}) == []
    assert any("not back-drilled" in f.problem for f in backdrill.check(text, R, NAMES, Z, {"N"}))
    cut = done.replace('"B.Cu" "In2.Cu"', '"B.Cu" "In1.Cu"')                                          # stops at the face of the last used layer: no margin
    assert any("would cut or touch" in f.problem for f in backdrill.check(cut, R, NAMES, Z, {"N"}))
    deep = done.replace('"B.Cu" "In2.Cu"', '"B.Cu" "B.Cu"')                                          # leaves nothing... but also removes no barrel at all: residual over the limit
    assert any("over the fab's" in f.problem for f in backdrill.check(deep, R, NAMES, Z, {"N"}))
    assert backdrill.apply(done, jobs, None, 0.2) == done                                              # idempotent


def test_a_pad_at_the_via_counts_as_a_used_layer():
    text = _board(["F.Cu"])
    assert backdrill.plan(text, R, {"N"}, NAMES, Z, [(10, 10, {"B.Cu"})])[0] == []                  # a bottom pad uses the bottom: no stub below it


@pytest.mark.skipif(toolchain.kicad_python() is None or not toolchain.kicad_cli(), reason="needs KiCad")
def test_the_depth_we_compute_is_the_depth_kicad_writes_into_its_drill_file(tmp_path):
    from conftest import mcu_circuit
    from sparky.export.kicad import to_kicad_pcb
    from sparky.pcb.placement import PlaceOptions, place
    rules = rules_for(6, "jlcpcb-6l", backdrill=Backdrill(0.2, 0.1, 0.45))
    board = place(mcu_circuit(), rules, PlaceOptions(seed=1))
    raw = tmp_path / "a.kicad_pcb"
    raw.write_text(to_kicad_pcb(board))
    up = tmp_path / "up.kicad_pcb"                                                                     # let pcbnew rewrite it in the current file format first
    subprocess.run([toolchain.kicad_python(), "-c", "import pcbnew,sys; b=pcbnew.LoadBoard(sys.argv[1]); pcbnew.SaveBoard(sys.argv[2], b)", str(raw), str(up)], check=True, capture_output=True)
    z = layer_z_mm(rules, 6)
    names = board.layers
    text = up.read_text()
    net = "S_PA9"
    via = f'\t(via\n\t\t(at 10 10)\n\t\t(size 0.5)\n\t\t(drill 0.25)\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "{net}")\n\t\t(uuid "00000000-0000-4000-8000-000000000001")\n\t)\n'
    seg = f'\t(segment\n\t\t(start 10 10)\n\t\t(end 12 10)\n\t\t(width 0.15)\n\t\t(layer "In1.Cu")\n\t\t(net "{net}")\n\t\t(uuid "00000000-0000-4000-8000-000000000002")\n\t)\n'
    i = text.rstrip().rfind(")")
    text = text[:i] + via + seg + text[i:]
    jobs, findings = backdrill.plan(text, rules, {net}, names, z, [])
    assert not findings and jobs
    text = backdrill.apply(text, jobs, None, rules.backdrill.oversize_mm)
    final = tmp_path / "f.kicad_pcb"
    final.write_text(text)
    (tmp_path / "f.kicad_pro").write_text("{}")
    out = tmp_path / "drl"
    subprocess.run([toolchain.kicad_cli(), "pcb", "export", "drill", "-o", str(out) + "/", "--format", "excellon", "--excellon-separate-th", str(final)], check=True, capture_output=True)
    stub = [float(m.group(1)) for f in out.glob("*backdrill*.drl") for m in re.finditer(r"Backdrill stub ([\d.]+)mm", f.read_text())]
    assert stub and stub[0] == pytest.approx(jobs[0].depth_mm, abs=0.005)                              # KiCad's own number for the drilled depth


@pytest.mark.slow
def test_signal_vias_of_a_routed_board_are_back_drilled_and_the_package_has_the_drill_files(tmp_path):
    from conftest import mcu_circuit
    from sparky import kicad_backend, routing
    if not (kicad_backend.available() and routing.available()):
        pytest.skip("KiCad / Java missing")
    from sparky.constraints import BackdrillNets
    from sparky.export.package import write_package
    from sparky.pcb.flow import BoardSpec, build_board
    c = mcu_circuit()
    c.constrain(BackdrillNets([n for n in c.nets if n.startswith("S_")]))
    res = build_board(c, BoardSpec(layers=6, time_budget_s=300, rule_overrides={"backdrill": Backdrill(0.2, 0.1, 0.45)}), log=None)
    assert res.success, res.drc.summary()
    row = next(r for r in res.routing_checks if r["kind"] == "backdrill")
    assert row["ok"], row["findings"]
    assert row["count"] >= 1                                                                            # at least one signal via really needed (and got) a back-drill
    files = write_package(res, tmp_path, "b")
    text = (tmp_path / "b.kicad_pcb").read_text()
    assert row["count"] == text.count("(tertiary_drill") + text.count("(backdrill")
    if row["count"]:
        assert any("backdrill" in k for k in files)                                                    # KiCad exported a drill file for them
    assert "Back-drilling" in (tmp_path / "REPORT.md").read_text()
