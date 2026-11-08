"""Silicon packaging: bundle + manifest + REPORT.md; ready only when everything passed; programme limits and failures flip the verdict."""
import hashlib
import json
from pathlib import Path

import pytest

from silicon import report, toolchain
from silicon.signoff import FAIL, NOT_RUN, PASS, Verdict

from silicon.spec import SiliconSpec

FIX = Path(__file__).parent / "silicon_fixtures"


def _spec():
    return SiliconSpec(design_name="counter", verilog_files=[FIX / "counter.v"], clock_port="clk", clock_period_ns=20.0, pdk="sky130A", reset_port="rst_n")

tools = pytest.mark.skipif(not toolchain.available(), reason="silicon tools not configured")


@pytest.mark.slow
@tools
def test_package_bundle_hashes_and_ready_flag_follow_the_verdicts(counter_run, tmp_path):
    _, r = counter_run
    s = _spec()
    good = [Verdict("x", PASS, "flow", "ok")]
    m = report.package(s, r, tmp_path / "a", verdicts=good)
    assert m["tapeout_ready"] is True and all(c["ok"] for c in m["crosschecks"])
    gds = tmp_path / "a" / m["files"]["gds"]["file"]
    assert hashlib.sha256(gds.read_bytes()).hexdigest() == m["files"]["gds"]["sha256"]
    assert "Tapeout ready" in (tmp_path / "a" / "REPORT.md").read_text()
    bad = report.package(s, r, tmp_path / "b", verdicts=good + [Verdict("y", FAIL, "flow", "broken")])
    assert bad["tapeout_ready"] is False
    unchecked = report.package(s, r, tmp_path / "c", verdicts=good + [Verdict("z", NOT_RUN, "flow", "skipped")])
    assert unchecked["tapeout_ready"] is False                                             # unchecked is not clean
    tiny = report.Programme("t", "test input", max_die_um=(1.0, 1.0))
    m2 = report.package(s, r, tmp_path / "d", verdicts=good, programme=tiny)
    assert m2["tapeout_ready"] is False and any(v["check"] == "programme.die" and v["status"] == FAIL for v in m2["verdicts"])
    wrong = report.Programme("t", "test input", pdk="gf180mcuD")
    assert any(v.check == "programme.pdk" and v.status == FAIL for v in report.programme_checks(s, r, wrong))
    json.loads((tmp_path / "a" / "manifest.json").read_text())
