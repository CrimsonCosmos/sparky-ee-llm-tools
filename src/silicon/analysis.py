"""Chip-level analyses read from the flow plus one independent re-derivation each. Limits are INPUTS (from the user's spec / programme); without a limit the verdict is NOT RUN
and the value is still reported.

  * power: ESTIMATE (vectorless switching activity, not a measurement). Re-derived by running OpenSTA `report_power` directly on the final netlist + SPEF at one corner and
    compared with that corner's report written by the flow. Same engine, independent wiring: it catches a wrong netlist/SPEF/corner, NOT a wrong activity model.
  * IR drop: the flow's static grid analysis (OpenROAD psm); compared against a limit given as a fraction of the supply.
  * clock skew: the flow's CTS-reported worst setup/hold skew, reported; limit optional."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import toolchain
from .flow import FlowResult
from .signoff import FAIL, NOT_RUN, PASS, Verdict, _run, _top

_TOTAL = re.compile(r"^Total\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", re.M)


def power_w_from_report(text: str) -> float | None:
    m = _TOTAL.search(text)
    return float(m.group(4)) if m else None


def flow_power_w(result: FlowResult, corner: str) -> float | None:
    """Total power at `corner` from the flow's own per-corner report (power.rpt of the post-PnR STA step)."""
    for rpt in sorted(result.run_dir.glob(f"*-openroad-stapostpnr/{corner}/power.rpt")):
        v = power_w_from_report(rpt.read_text())
        if v is not None:
            return v
    return None


def rerun_power_w(result: FlowResult, corner: str) -> float | None:
    lib_map = result.resolved.get("LIB") or {}
    lib = next((p[0] for pat, p in lib_map.items() if corner.endswith(pat.lstrip("*"))), None)
    top, tag = _top(result), corner.split("_")[0]
    spef = result.final / "spef" / tag / f"{top}.{tag}.spef"
    sdc, nl = result.artifacts.get("sdc"), result.artifacts.get("netlist")
    if not (lib and Path(lib).is_file() and spef.is_file() and sdc and nl):
        return None
    with tempfile.TemporaryDirectory(prefix="pwr_") as td:
        s = Path(td) / "p.tcl"
        s.write_text(f"read_liberty {lib}\nread_verilog {nl}\nlink_design {top}\nread_sdc {sdc}\nread_spef {spef}\nreport_power\nexit\n")
        _, text = _run([toolchain.which("sta"), "-no_splash", "-exit", str(s)])
    return power_w_from_report(text)


def power(result: FlowResult, corner: str | None = None, tol_rel: float = 0.02) -> list[Verdict]:
    """`tol_rel` bounds the disagreement between the flow's report and our OpenSTA run (both print 4 significant digits, so 2 % is far above rounding)."""
    corner = corner or result.resolved.get("DEFAULT_CORNER")
    if not corner:
        return [Verdict("power.estimate", NOT_RUN, "rerun", "no default corner in the resolved configuration")]
    ref, mine = flow_power_w(result, corner), rerun_power_w(result, corner)
    if ref is None or mine is None:
        return [Verdict("power.estimate", NOT_RUN, "rerun", f"cannot obtain both numbers at {corner} (flow report {ref}, OpenSTA re-run {mine})")]
    ok = abs(mine / ref - 1) <= tol_rel
    return [Verdict("power.estimate", PASS if ok else FAIL, "comparison",
                    f"ESTIMATE (vectorless activity) at {corner}: flow {ref:.4g} W, OpenSTA re-run {mine:.4g} W ({(mine / ref - 1) * 100:+.2f} %, tolerance {tol_rel * 100:g} %)", mine)]


def ir_drop(result: FlowResult, limit_fraction_of_supply: float | None = None, supply_v: float | None = None) -> Verdict:
    worst = result.metric("design_powergrid__drop__worst")
    if worst is None:
        return Verdict("ir_drop", NOT_RUN, "flow", "the flow reported no power-grid drop")
    if limit_fraction_of_supply is None or supply_v is None:
        return Verdict("ir_drop", NOT_RUN, "flow", f"worst static drop {worst * 1e3:.4g} mV reported; no limit/supply given, so no verdict")
    ok = worst <= limit_fraction_of_supply * supply_v
    return Verdict("ir_drop", PASS if ok else FAIL, "flow", f"worst static drop {worst * 1e3:.4g} mV vs limit {limit_fraction_of_supply * supply_v * 1e3:.4g} mV (flow analysis, not independently re-derived)", worst)


def clock_skew(result: FlowResult, limit_ns: float | None = None) -> Verdict:
    setup, hold = result.metric("clock__skew__worst_setup"), result.metric("clock__skew__worst_hold")
    if setup is None or hold is None:
        return Verdict("clock_skew", NOT_RUN, "flow", "the flow reported no clock skew")
    worst = max(abs(setup), abs(hold))
    if limit_ns is None:
        return Verdict("clock_skew", NOT_RUN, "flow", f"worst skew {worst:.3f} ns reported (setup {setup:.3f}, hold {hold:.3f}); no limit given, so no verdict")
    return Verdict("clock_skew", PASS if worst <= limit_ns else FAIL, "flow", f"worst skew {worst:.3f} ns vs limit {limit_ns:g} ns", worst)
