"""riscv-formal on PicoRV32: the clean core passes the instruction checks and the project's own deliberately buggy variants make them FAIL. Needs a riscv-formal checkout (RISCV_FORMAL), sby and PicoRV32's source
(PICORV32_V); skipped otherwise. Solver: ABC bmc3 (z3 through smtbmc needed >18 minutes for one check)."""
import os
from pathlib import Path

import pytest

from silicon import riscv_formal as rf

ROOT, PICO = os.environ.get("RISCV_FORMAL"), os.environ.get("PICORV32_V")
pytestmark = [pytest.mark.slow, pytest.mark.skipif(not (ROOT and PICO and Path(PICO).is_file()), reason="RISCV_FORMAL / PICORV32_V not set")]
CHECKS = ["insn_add_ch0", "insn_beq_ch0"]


def run(tmp_path, bug):
    return rf.run(tmp_path / f"bug_{bug}", "picorv32", {"picorv32.v": Path(PICO)}, CHECKS, root=ROOT, bug=bug, solver="bmc3", timeout=900)


def test_verdict_parsing_uses_the_engine_line():
    assert rf.verdict("summary: engine_0 (abc bmc3) returned FAIL\nDONE (ERROR, rc=16)") == "fail"
    assert rf.verdict("summary: engine_0 (abc bmc3) returned PASS\nDONE (PASS, rc=0)") == "pass"
    assert rf.verdict("DONE (ERROR, rc=16)") == "error"


def test_clean_core_passes_and_testbug_variants_fail(tmp_path):
    clean = run(tmp_path, None)
    assert clean.all_pass, clean.not_run or clean.by_name()
    for bug in ("003", "005"):                                   # bugs 001/002 are not detected by these two checks (recorded in docs/GPU_HOLES_PLAN.md)
        r = run(tmp_path, bug)
        assert "fail" in r.by_name().values(), (bug, r.not_run or r.by_name())


def test_register_file_bugs_are_caught_by_the_reg_check_at_a_shallow_bound(tmp_path):
    """Testbugs 001 (write to the wrong register) and 002 (write a corrupted value) damage the register FILE, which the per-instruction checks do not see; riscv-formal's `reg` check does.
    Bound reduced to `reg 6 12` (default `15 25` timed out for the clean core after 25 minutes): a shorter, weaker proof."""
    kw = dict(checks=["reg_ch0"], root=ROOT, solver="bmc3", timeout=900, depth={"reg": "6 12"})
    clean = rf.run(tmp_path / "clean", "picorv32", {"picorv32.v": Path(PICO)}, **kw)
    assert clean.all_pass, clean.not_run or clean.by_name()
    for bug in ("001", "002"):
        r = rf.run(tmp_path / bug, "picorv32", {"picorv32.v": Path(PICO)}, bug=bug, **kw)
        assert r.by_name() == {"reg_ch0": "fail"}, (bug, r.not_run or r.by_name())
