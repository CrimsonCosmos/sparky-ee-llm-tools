"""Sign-off verdicts: flow metrics (absent = NOT RUN) and our own tool re-runs, each shown to FAIL on a deliberately broken layout, netlist or constraint."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from silicon import flow, signoff, toolchain
from silicon.signoff import FAIL, NOT_RUN, PASS


def _result(metrics, resolved=None, tmp=None):
    return flow.FlowResult(True, tmp, 1.0, metrics, resolved or {})


# ---- no tools needed -------------------------------------------------------------------------------------------------------------------
def test_an_absent_metric_is_not_run_never_a_pass():
    full = {"klayout__drc_error__count": 0, "magic__drc_error__count": 0, "design__lvs_error__count": 0, "antenna__violating__nets": 0,
            "timing__setup__ws": 1.0, "timing__hold__ws": 0.1}
    assert signoff.clean(signoff.from_metrics(_result(full, {"KLAYOUT_DRC_RUNSET": "x"})))
    gf = dict(full)
    del gf["klayout__drc_error__count"]                                           # what gf180mcuD does
    v = {x.check: x for x in signoff.from_metrics(_result(gf))}
    assert v["drc.klayout"].status == NOT_RUN and "runset" in v["drc.klayout"].detail
    assert not signoff.clean(signoff.from_metrics(_result(gf)))                   # unchecked is not signed off
    assert signoff.from_metrics(_result({}))[0].status == NOT_RUN


def test_failures_and_negative_slack_are_failures():
    m = {"klayout__drc_error__count": 3, "magic__drc_error__count": 0, "design__lvs_error__count": 0, "antenna__violating__nets": 0,
         "timing__setup__ws": -0.2, "timing__hold__ws": 0.0}
    v = {x.check: x for x in signoff.from_metrics(_result(m, {"KLAYOUT_DRC_RUNSET": "x"}))}
    assert v["drc.klayout"].status == FAIL and v["timing.setup"].status == FAIL and v["timing.hold"].status == PASS    # slack exactly 0 meets timing
    assert not signoff.clean(list(v.values()))


def test_comparison_demands_agreement_and_skips_what_did_not_run():
    a = [signoff.Verdict("drc.magic", PASS, "flow", ""), signoff.Verdict("timing.setup", PASS, "flow", "", 1.000), signoff.Verdict("drc.klayout", NOT_RUN, "flow", "")]
    b = [signoff.Verdict("drc.magic", FAIL, "rerun", ""), signoff.Verdict("timing.setup", PASS, "rerun", "", 1.004), signoff.Verdict("drc.klayout", PASS, "rerun", "")]
    c = {v.check: v for v in signoff.compare(a, b)}
    assert c["agreement.drc.magic"].status == FAIL                                  # one engine says clean, the other not
    assert c["agreement.timing.setup"].status == PASS                               # within 0.01 ns
    assert c["agreement.drc.klayout"].status == NOT_RUN
    assert signoff.compare(a, [signoff.Verdict("timing.setup", PASS, "rerun", "", 1.2)])[0].status == FAIL


def test_lyrdb_items_are_counted(tmp_path):
    f = tmp_path / "r.lyrdb"
    f.write_text("<report-database><categories><category><name>x</name></category></categories><items><item><category>x</category></item><item><category>x</category></item></items></report-database>")
    assert signoff.count_lyrdb_items(f) == 2
    f.write_text("<report-database><items/></report-database>")
    assert signoff.count_lyrdb_items(f) == 0


# ---- slow: the real tools ---------------------------------------------------------------------------------------------------------------
@pytest.mark.slow
def test_reruns_agree_with_the_flow_on_a_clean_counter(counter_run):
    s, r = counter_run
    assert r.ok
    v = signoff.signoff(r)
    assert signoff.clean(v), [x for x in v if x.status != PASS]
    assert {x.check for x in v if x.source == "rerun"} == {"drc.klayout", "drc.magic", "lvs.netgen", "timing.setup", "timing.hold"}
    assert all(x.status == PASS for x in v if x.check.startswith("agreement"))
    setup = next(x for x in v if x.check == "timing.setup" and x.source == "rerun")
    assert setup.value == pytest.approx(r.metric("timing__setup__ws__corner:nom_tt_025C_1v80"), abs=0.01)         # OpenSTA on our own inputs reproduces the flow's number


def _tamper_gds(src: Path, dst: Path, top: str) -> None:
    """Add a 0.05 um square on met1 (68/20): far below the minimum width, so any DRC engine must object."""
    code = ("import sys, klayout.db as db\nly = db.Layout()\nly.read(sys.argv[1])\ntop = ly.cell(sys.argv[3])\nl = ly.layer(68, 20)\n"
            "top.shapes(l).insert(db.DBox(5.0, 5.0, 5.05, 5.05))\nly.write(sys.argv[2])\n")
    subprocess.run([toolchain.python(), "-c", code, str(src), str(dst), top], check=True, env=toolchain.env())


@pytest.mark.slow
def test_both_drc_engines_object_to_a_sliver_added_to_the_layout(counter_run, tmp_path):
    s, r = counter_run
    bad = tmp_path / "bad.gds"
    _tamper_gds(r.artifacts["gds"], bad, "counter")
    k, m = signoff.rerun_klayout_drc(r, bad), signoff.rerun_magic_drc(r, bad)
    assert k.status == FAIL and k.value >= 1, k
    assert m.status == FAIL and m.value >= 1, m


@pytest.mark.slow
def test_lvs_objects_to_a_device_missing_from_the_layout(counter_run, tmp_path):
    s, r = counter_run
    lines = Path(r.artifacts["spice"]).read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("X") and "dfrtp" in l)          # delete one flip-flop from the extracted layout netlist
    bad = tmp_path / "bad.spice"
    bad.write_text("\n".join(lines[:i] + lines[i + 1:]) + "\n")
    v = signoff.rerun_lvs(r, layout_spice=bad)
    assert v.status == FAIL, v
    assert signoff.rerun_lvs(r).status == PASS                                                # the untouched layout still matches


@pytest.mark.slow
def test_a_tightened_clock_gives_negative_slack_and_the_comparison_notices(counter_run, tmp_path):
    s, r = counter_run
    sdc = tmp_path / "tight.sdc"
    sdc.write_text(r.artifacts["sdc"].read_text().replace("-period 20.0000", "-period 0.2000"))
    setup = next(v for v in signoff.rerun_sta(r, sdc=sdc) if v.check == "timing.setup")
    assert setup.status == FAIL and setup.value < 0
    flow_v = signoff.from_metrics(r)
    cmp = signoff.compare(flow_v, [setup])
    assert cmp[0].status == FAIL                                                              # the flow claims +14 ns, the re-run on the tightened constraint -ve: disagreement is reported
