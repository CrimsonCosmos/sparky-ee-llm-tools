"""Silicon flow phase 1: spec -> configuration, result parsing, and the independent cross-checks (each shown to FAIL on a deliberately broken input)."""
import json
import shutil
from pathlib import Path

import pytest

from silicon import crosscheck, flow, pdk, spec, toolchain

FIX = Path(__file__).parent / "silicon_fixtures"


def _spec(**kw):
    base = dict(design_name="counter", verilog_files=[FIX / "counter.v"], clock_port="clk", clock_period_ns=20.0, pdk="sky130A", die_area_um=(0, 0, 120, 120))
    base.update(kw)
    return spec.SiliconSpec(**base)


# ---- no tools needed ---------------------------------------------------------------------------------------------------------------------
def test_spec_becomes_a_librelane_config_without_inventing_values():
    cfg = _spec().to_config()
    assert cfg == {"DESIGN_NAME": "counter", "VERILOG_FILES": [str((FIX / "counter.v").resolve())], "CLOCK_PORT": "clk", "CLOCK_PERIOD": 20.0,
                   "FP_SIZING": "absolute", "DIE_AREA": [0, 0, 120, 120]}
    util = _spec(die_area_um=None, core_utilization_pct=35).to_config()
    assert util["FP_SIZING"] == "relative" and util["FP_CORE_UTIL"] == 35 and "DIE_AREA" not in util
    none = _spec(die_area_um=None).to_config()
    assert "FP_SIZING" not in none and "FP_CORE_UTIL" not in none                # nothing chosen: LibreLane's own defaults apply, not ours
    assert _spec(extra={"PL_TARGET_DENSITY_PCT": 40}).to_config()["PL_TARGET_DENSITY_PCT"] == 40


def test_bad_specs_are_refused():
    with pytest.raises(ValueError, match="unknown PDK"):
        _spec(pdk="tsmc4").to_config()
    with pytest.raises(FileNotFoundError):
        _spec(verilog_files=[FIX / "nope.v"]).to_config()
    with pytest.raises(ValueError, match="clock period"):
        _spec(clock_period_ns=0).to_config()
    with pytest.raises(ValueError, match="not both"):
        _spec(core_utilization_pct=40).to_config()
    assert pdk.profile("gf180mcuD").family == "gf180mcu"


def test_a_metric_the_run_did_not_report_is_none_not_zero(tmp_path):
    run = tmp_path / "run"
    (run / "final").mkdir(parents=True)
    (run / "final" / "metrics.json").write_text(json.dumps({"design__lvs_error__count": 0}))
    r = flow.parse_run(run, 0, 1.0, None, "")
    assert r.ok and r.metric("design__lvs_error__count") == 0
    assert r.metric("klayout__drc_error__count") is None                        # not reported (as on gf180mcuD): NOT measured
    bad = flow.parse_run(run, 1, 1.0, None, "Running 'OpenROAD.GlobalPlacement'\n[ERROR] boom")
    assert not bad.ok and bad.failed_step == "OpenROAD.GlobalPlacement" and bad.errors == ["[ERROR] boom"]
    assert not flow.parse_run(tmp_path / "empty", 0, 1.0, None, "").ok            # exit code 0 without metrics is not a success


def test_parsers_by_hand():
    sdc = "create_clock -name clk -period 20.0000 [get_ports {clk}]\ncreate_clock -name aux -period 5 [get_ports {aux}]\n"
    assert crosscheck.sdc_clock_period_ns(sdc, "clk") == 20.0 and crosscheck.sdc_clock_period_ns(sdc, "aux") == 5.0
    assert crosscheck.sdc_clock_period_ns(sdc, "nope") is None
    nl = """module m (a, b);
  input a;
  output b;
  wire w; // not an instance
  /* nor
     comment */
  sky__nand2_1 _1_ (.A(a),
    .B(a),
    .Y(w));
  sky__dff_1 \\q[0]$reg (.D(w), .Q(b));
  assign b = w;
endmodule"""
    assert crosscheck.netlist_instances(nl) == {"sky__nand2_1": 1, "sky__dff_1": 1}
    lib = 'cell ("x__inv_1") {\n area : 1; }\ncell ("x__dff_1") {\n ff ("IQ","IQ_N") { clocked_on : "CLK"; }\n}\ncell ("x__lat_1") { latch ("IQ","IQ_N") { } }'
    assert crosscheck.liberty_sequential_cells(lib) == {"x__dff_1", "x__lat_1"}


def test_without_tools_a_run_is_not_run_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_SILICON_ENV", str(tmp_path / "absent.json"))
    monkeypatch.setenv("PATH", str(tmp_path))
    toolchain.clear_cache()
    r = flow.run_flow(_spec(), tmp_path / "w")
    assert not r.ok and r.not_run and "not installed" in r.not_run
    assert crosscheck.run_checks(_spec(), r)[0].ok is None                       # nothing ran: a NOT RUN check, never a pass
    toolchain.clear_cache()


# ---- slow: the real flow -----------------------------------------------------------------------------------------------------------------
tools = pytest.mark.skipif(not toolchain.available() or toolchain.pdk_root() is None, reason="silicon tools / PDK root not configured (tools/silicon_setup.sh)")


@pytest.mark.slow
@tools
def test_counter_runs_through_the_flow_and_every_cross_check_agrees(counter_run):
    s, r = counter_run
    assert r.ok, (r.failed_step, r.errors)
    assert r.metric("design__lvs_error__count") == 0 and r.metric("magic__drc_error__count") == 0
    checks = crosscheck.run_checks(s, r)
    assert [c.name for c in checks] == ["die size", "clock period", "instance count", "flip-flops"]
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def _copy(r, tmp_path):
    """A copy of the result with its own SDC/netlist so a check can be shown to fail on tampered data."""
    new = flow.FlowResult(r.ok, r.run_dir, r.seconds, dict(r.metrics), r.resolved, dict(r.artifacts), r.log_path)
    for k in ("sdc", "netlist"):
        dst = tmp_path / r.artifacts[k].name
        shutil.copy(r.artifacts[k], dst)
        new.artifacts[k] = dst
    return new


@pytest.mark.slow
@tools
def test_each_cross_check_fails_on_a_deliberately_broken_input(counter_run, tmp_path):
    s, r = counter_run
    by = lambda checks: {c.name: c for c in checks}
    bad = _copy(r, tmp_path)
    bad.artifacts["sdc"].write_text(bad.artifacts["sdc"].read_text().replace("-period 20.0000", "-period 10.0000"))
    assert by(crosscheck.run_checks(s, bad))["clock period"].ok is False                      # SDC disagrees with the spec
    bad = _copy(r, tmp_path)
    lines = bad.artifacts["netlist"].read_text().splitlines()
    i = next(n for n, l in enumerate(lines) if l.strip().startswith("sky130_fd_sc_hd__decap_3"))
    bad.artifacts["netlist"].write_text("\n".join(lines[:i] + lines[i + 1:]))                 # one instance's line deleted (its pin lines stay: only the count changes)
    assert by(crosscheck.run_checks(s, bad))["instance count"].ok is False
    bad = _copy(r, tmp_path)
    bad.metrics["design__die__area"] = 99999.0                                                  # the flow's report disagrees with the layout
    assert by(crosscheck.run_checks(s, bad))["die size"].ok is False
    assert by(crosscheck.run_checks(_spec(verilog_files=[FIX / "counter9.v"]), r))["flip-flops"].ok is False   # RTL says 9 flops, the layout has 8
    gone = _copy(r, tmp_path)
    gone.resolved = {}
    assert by(crosscheck.run_checks(s, gone))["flip-flops"].ok is None                          # no Liberty to read: NOT RUN, not a pass
