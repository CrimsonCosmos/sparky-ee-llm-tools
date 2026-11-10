"""Sign-off verdicts for a finished flow, from two sources that must agree:

  * the flow's own metrics (LibreLane's DRC/LVS/timing counters), where an ABSENT metric means NOT RUN, never zero (on gf180mcuD LibreLane completes without any KLayout DRC
    because the PDK configuration names no KLayout runset: reading the missing counter as 0 would call an unchecked layout clean);
  * our own re-run of each tool on the flow's artifacts, invoked directly with the PDK's own decks (KLayout DRC, Magic DRC, Netgen LVS with the PDK's setup, OpenSTA on the
    final netlist + SDC + SPEF), so a fault in LibreLane's wrapper cannot confirm itself.
A re-run that cannot be made says why (`not_run`)."""
from __future__ import annotations

import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import toolchain
from .flow import FlowResult

PASS, FAIL, NOT_RUN = "pass", "fail", "not_run"


@dataclass
class Verdict:
    check: str                 # e.g. 'drc.klayout', 'lvs.netgen', 'timing.setup', 'agreement.drc.klayout'
    status: str                # pass | fail | not_run
    source: str                # 'flow' | 'rerun' | 'comparison'
    detail: str
    value: float | None = None


def _status(ok: bool) -> str:
    return PASS if ok else FAIL


# ---- from the flow's metrics ---------------------------------------------------------------------------------------------------------------
def from_metrics(result: FlowResult, corner: str | None = None) -> list[Verdict]:
    m, out = result.metrics, []

    def count(check: str, key: str, what: str, hint: str = ""):
        v = m.get(key)
        out.append(Verdict(check, NOT_RUN, "flow", f"{what}: the flow reported no '{key}'{hint}") if v is None else Verdict(check, _status(v == 0), "flow", f"{what}: {v} error(s)", float(v)))

    runset_missing = not result.resolved.get("KLAYOUT_DRC_RUNSET")
    count("drc.klayout", "klayout__drc_error__count", "KLayout DRC", " (the PDK configuration names no KLayout DRC runset: LibreLane skipped it)" if runset_missing else "")
    count("drc.magic", "magic__drc_error__count", "Magic DRC")
    count("lvs.netgen", "design__lvs_error__count", "Netgen LVS")
    count("antenna", "antenna__violating__nets", "antenna check")
    corner = corner or result.resolved.get("DEFAULT_CORNER")
    for kind in ("setup", "hold"):
        key = f"timing__{kind}__ws__corner:{corner}" if corner else f"timing__{kind}__ws"
        v = m.get(key)
        out.append(Verdict(f"timing.{kind}", NOT_RUN, "flow", f"{kind} timing: the flow reported no '{key}'") if v is None else
                   Verdict(f"timing.{kind}", _status(v >= 0), "flow", f"worst {kind} slack {v:.3f} ns at {corner or 'all corners'}", float(v)))
    return out


# ---- our own re-runs -------------------------------------------------------------------------------------------------------------------------
def _top(result: FlowResult) -> str:
    return result.resolved["DESIGN_NAME"]


def _run(cmd: list[str], env: dict | None = None, timeout: int = 1800) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env or toolchain.env(), input="")
    return r.returncode, r.stdout + r.stderr


def count_lyrdb_items(path: Path) -> int:
    """Violations in a KLayout report database: the `<item>` entries of `<items>`."""
    root = ET.parse(path).getroot()
    items = root.find("items")
    return 0 if items is None else len(items.findall("item"))


def _pdk_runset(result: FlowResult) -> str | None:
    """The PDK's own KLayout DRC runset (libs.tech/klayout/drc/*_mr.drc) when the flow's configuration does not name one (e.g. DRC steps substituted out)."""
    root, pdk = result.resolved.get("PDK_ROOT"), result.resolved.get("PDK")
    if not (root and pdk):
        return None
    hits = sorted(Path(root).rglob(f"{pdk}/libs.tech/klayout/drc/{pdk}_mr.drc"))
    return str(hits[0]) if hits else None


def _pdk_drc_defines(result: FlowResult) -> dict | None:
    """The rule-group switches the PDK's own flow configuration passes to its KLayout deck (libs.tech/openlane/config.tcl: `KLAYOUT_DRC_OPTIONS [dict create beol 1 feol 1 ...]`). Without them the sky130 deck runs with every
    rule group OFF and reports 0 violations on anything - so when they cannot be read, the check is NOT RUN rather than guessed."""
    root, pdk = result.resolved.get("PDK_ROOT"), result.resolved.get("PDK")
    if not (root and pdk):
        return None
    for cfg in sorted(Path(root).rglob(f"{pdk}/libs.tech/openlane/config.tcl")):
        m = re.search(r"KLAYOUT_DRC_OPTIONS\)\s*\[dict create ([^\]]+)\]", cfg.read_text())
        if m:
            toks = m.group(1).split()
            return dict(zip(toks[0::2], toks[1::2]))
    return None


def rerun_klayout_drc(result: FlowResult, gds: Path | None = None) -> Verdict:
    runset = result.resolved.get("KLAYOUT_DRC_RUNSET")
    fallback_defines = None
    if not runset and gds is not None:
        runset, fallback_defines = _pdk_runset(result), _pdk_drc_defines(result)
        if runset and not fallback_defines:
            return Verdict("drc.klayout", NOT_RUN, "rerun", "the PDK's KLayout rule-group switches could not be read (the deck would run with all rule groups off): NOT RUN")
    if not runset:
        return Verdict("drc.klayout", NOT_RUN, "rerun", "the PDK configuration names no KLayout DRC runset")
    gds = gds or result.artifacts["gds"]
    with tempfile.TemporaryDirectory(prefix="kldrc_") as td:
        report = Path(td) / "drc.lyrdb"
        defines = [x for k, v in (result.resolved.get("KLAYOUT_DRC_DEFINES") or fallback_defines or {}).items() for x in ("-rd", f"{k}={v}")]
        rc, text = _run([toolchain.which("klayout"), "-b", "-zz", "-r", runset, "-rd", f"input={gds}", "-rd", f"topcell={_top(result)}", "-rd", f"report={report}", *defines, "-rd", "threads=2"])
        if not report.is_file():
            return Verdict("drc.klayout", NOT_RUN, "rerun", f"KLayout produced no report (rc={rc}): {text.strip()[-200:]}")
        n = count_lyrdb_items(report)
    return Verdict("drc.klayout", _status(n == 0), "rerun", f"KLayout DRC with {Path(runset).name}: {n} violation(s)", float(n))


def rerun_magic_drc(result: FlowResult, gds: Path | None = None) -> Verdict:
    rc_file, pdk_root, pdk = result.resolved.get("MAGICRC"), result.resolved.get("PDK_ROOT"), result.resolved.get("PDK")
    if not (rc_file and pdk_root):
        return Verdict("drc.magic", NOT_RUN, "rerun", "no Magic setup in the resolved configuration")
    gds = gds or result.artifacts["gds"]
    with tempfile.TemporaryDirectory(prefix="mgdrc_") as td:
        script = Path(td) / "drc.tcl"
        script.write_text(f"gds read {gds}\nload {_top(result)}\nselect top cell\ndrc catchup\ndrc count total\nquit -noprompt\n")
        env = {**toolchain.env(), "PDK_ROOT": pdk_root, "PDK": pdk or ""}
        rc, text = _run([toolchain.which("magic"), "-dnull", "-noconsole", "-rcfile", rc_file, str(script)], env)
    m = re.search(r"Total DRC errors found:\s*(\d+)", text)
    if not m:
        return Verdict("drc.magic", NOT_RUN, "rerun", f"Magic printed no DRC total (rc={rc}): {text.strip()[-200:]}")
    n = int(m.group(1))
    return Verdict("drc.magic", _status(n == 0), "rerun", f"Magic DRC: {n} error(s)", float(n))


def rerun_lvs(result: FlowResult, layout_spice: Path | None = None, netlist: Path | None = None) -> Verdict:
    """Netgen LVS of the layout-extracted SPICE against the power-aware netlist, with the PDK's own Netgen setup and the cells' SPICE models."""
    setup = result.resolved.get("NETGEN_SETUP")
    spice = layout_spice or (result.artifacts.get("spice") and result.artifacts["spice"])
    if not setup or not spice:
        return Verdict("lvs.netgen", NOT_RUN, "rerun", "no Netgen setup or no extracted SPICE in the run")
    pnl = netlist or (result.final / "pnl" / f"{_top(result)}.pnl.v")
    cells = result.resolved.get("CELL_SPICE_MODELS") or []
    with tempfile.TemporaryDirectory(prefix="lvs_") as td:
        script = Path(td) / "lvs.tcl"
        lines = [f"set circuit1 [readnet spice {spice}]", "set circuit2 [readnet verilog /dev/null]"]
        lines += [f"readnet spice {c} $circuit2" for c in cells]
        lines += [f"readnet verilog {pnl} $circuit2", f'lvs "$circuit1 {_top(result)}" "$circuit2 {_top(result)}" {setup} {Path(td) / "lvs.rpt"} -blackbox']
        script.write_text("\n".join(lines) + "\n")
        rc, text = _run([toolchain.which("netgen"), "-batch", "source", str(script)])
    if "Circuits match uniquely" in text or re.search(r"Circuits match(?! uniquely)", text) and "do not match" not in text:
        return Verdict("lvs.netgen", PASS, "rerun", "Netgen: circuits match")
    if "do not match" in text or "mismatch" in text.lower() or "Mismatch" in text:
        return Verdict("lvs.netgen", FAIL, "rerun", "Netgen: circuits do NOT match")
    return Verdict("lvs.netgen", NOT_RUN, "rerun", f"Netgen gave no verdict (rc={rc}): {text.strip()[-200:]}")


def _macro_views(result: FlowResult, corner: str | None) -> tuple[list[str], list[tuple[str, str]]]:
    """Macro views for the STA re-run, the way the flow itself uses them: a macro with a netlist AND parasitics is analysed hierarchically (its netlist is read and its SPEF applied under each instance); a macro with only a
    Liberty view is a timing black box. Returns (extra files to read: Liberty or netlists, (instance, SPEF) pairs)."""
    import fnmatch
    files, spefs = [], []
    tag = (corner or "nom_").split("_")[0]
    for name, m in (result.resolved.get("MACROS") or {}).items():
        sp = next((v for pat, v in (m.get("spef") or {}).items() if pat.startswith(tag)), None)
        if m.get("nl") and sp:
            files += [("netlist", str(x)) for x in m["nl"][:1]]
            spefs += [(inst, str(sp[0])) for inst in (m.get("instances") or {})]
        else:
            lib = next((v for pat, v in (m.get("lib") or {}).items() if pat == "*" or (corner and fnmatch.fnmatch(corner, pat))), None)
            if lib:
                files.append(("lib", str(lib[0])))
    return files, spefs


def rerun_sta(result: FlowResult, corner: str | None = None, sdc: Path | None = None) -> list[Verdict]:
    """OpenSTA on the final netlist with the flow's SDC and extracted parasitics at one corner: worst setup and hold slack."""
    corner = corner or result.resolved.get("DEFAULT_CORNER")
    lib_map = result.resolved.get("LIB") or {}
    lib = next((p[0] for pat, p in lib_map.items() if corner and corner.endswith(pat.lstrip("*"))), None)
    top = _top(result)
    spef = result.final / "spef" / (corner.split("_")[0] if corner else "nom") / f"{top}.{corner.split('_')[0] if corner else 'nom'}.spef"
    sdc = sdc or result.artifacts.get("sdc")
    if not (lib and Path(lib).is_file() and spef.is_file() and sdc and "netlist" in result.artifacts):
        return [Verdict("timing.setup", NOT_RUN, "rerun", f"missing inputs for OpenSTA at corner {corner} (liberty/SPEF/SDC/netlist)"),
                Verdict("timing.hold", NOT_RUN, "rerun", "same")]
    with tempfile.TemporaryDirectory(prefix="sta_") as td:
        script = Path(td) / "sta.tcl"
        macro_files, macro_spefs = _macro_views(result, corner)
        script.write_text(f"read_liberty {lib}\n" + "".join(f"read_liberty {f}\n" for k, f in macro_files if k == "lib") + f"read_verilog {result.artifacts['netlist']}\n" + "".join(f"read_verilog {f}\n" for k, f in macro_files if k == "netlist")
                          + f"link_design {top}\nread_sdc {sdc}\nread_spef {spef}\n"
                          + "".join(f"read_spef -path {inst} {sp}\n" for inst, sp in macro_spefs)
                          + "report_worst_slack -max -digits 4\nreport_worst_slack -min -digits 4\nexit\n")
        rc, text = _run([toolchain.which("sta"), "-no_splash", "-exit", str(script)])
    out = []
    for kind, word in (("SETUP", "max"), ("HOLD", "min")):
        m = re.search(rf"worst slack {word}\s+(-?[\d.eE+-]+)", text)
        name = f"timing.{kind.lower()}"
        if not m:
            out.append(Verdict(name, NOT_RUN, "rerun", f"OpenSTA printed no {kind.lower()} slack (rc={rc}): {text.strip()[-160:]}"))
            continue
        v = float(m.group(1))
        out.append(Verdict(name, _status(v >= 0), "rerun", f"OpenSTA at {corner}: worst {kind.lower()} slack {v:.3f} ns", v))
    return out


# ---- everything together ---------------------------------------------------------------------------------------------------------------------
def compare(flow_v: list[Verdict], rerun_v: list[Verdict], slack_tol_ns: float = 0.01) -> list[Verdict]:
    """Where both the flow and our re-run have an answer they must agree (DRC/LVS verdicts equal; timing slacks within `slack_tol_ns`)."""
    by = {v.check: v for v in flow_v}
    out = []
    for r in rerun_v:
        f = by.get(r.check)
        if f is None or NOT_RUN in (f.status, r.status):
            out.append(Verdict(f"agreement.{r.check}", NOT_RUN, "comparison", "one side did not run"))
            continue
        if r.check.startswith("timing"):
            ok = abs((f.value or 0.0) - (r.value or 0.0)) <= slack_tol_ns
            out.append(Verdict(f"agreement.{r.check}", _status(ok), "comparison", f"flow {f.value:.3f} ns vs re-run {r.value:.3f} ns"))
        else:
            out.append(Verdict(f"agreement.{r.check}", _status(f.status == r.status), "comparison", f"flow {f.status}, re-run {r.status}"))
    return out


def signoff(result: FlowResult, rerun: bool = True) -> list[Verdict]:
    if not result.ok or result.final is None:
        return [Verdict("flow", NOT_RUN, "flow", "the flow did not complete")]
    flow_v = from_metrics(result)
    if not rerun:
        return flow_v
    rerun_v = [rerun_klayout_drc(result), rerun_magic_drc(result), rerun_lvs(result), *rerun_sta(result)]
    return flow_v + rerun_v + compare(flow_v, rerun_v)


def clean(verdicts: list[Verdict]) -> bool:
    """Signed off only when nothing failed AND nothing was left unchecked."""
    return all(v.status == PASS for v in verdicts if not v.check.startswith("agreement")) and all(v.status != FAIL for v in verdicts)
