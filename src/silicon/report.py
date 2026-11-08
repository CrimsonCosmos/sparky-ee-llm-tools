"""Package a finished silicon flow: artifact bundle, reproducibility manifest and REPORT.md (every verdict, NOT RUN stated, estimates labelled).

Shuttle/programme constraints (maximum die size, required checks) are INPUTS read from the programme by the user (`Programme`); none is built in.
`tapeout_ready` is true only when sign-off is clean (nothing failed, nothing unchecked), every cross-check passed and every programme constraint was checked and met."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import crosscheck, toolchain
from .flow import FlowResult
from .signoff import FAIL, NOT_RUN, PASS, Verdict, clean, signoff
from .spec import SiliconSpec

BUNDLE_KEYS = ("gds", "lef", "def", "netlist", "sdc", "sdf", "spef", "lib", "spice")


@dataclass
class Programme:
    """A shuttle's submission constraints, copied by the user from the programme's documentation (source stated in `source`)."""
    name: str
    source: str
    max_die_um: tuple[float, float] | None = None
    pdk: str | None = None
    notes: list[str] = field(default_factory=list)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def programme_checks(spec: SiliconSpec, result: FlowResult, prog: Programme) -> list[Verdict]:
    out: list[Verdict] = []
    if prog.pdk is not None:
        out.append(Verdict("programme.pdk", PASS if prog.pdk == spec.pdk else FAIL, "comparison", f"programme requires {prog.pdk}, design uses {spec.pdk}"))
    if prog.max_die_um is not None:
        try:
            w, h = crosscheck.die_size_um(result.artifacts["gds"])
            ok = w <= prog.max_die_um[0] and h <= prog.max_die_um[1]
            out.append(Verdict("programme.die", PASS if ok else FAIL, "comparison", f"die {w:g} x {h:g} um (read from the GDS) vs limit {prog.max_die_um[0]:g} x {prog.max_die_um[1]:g} um"))
        except Exception as e:
            out.append(Verdict("programme.die", NOT_RUN, "comparison", f"cannot read the die size: {e}"))
    return out


def package(spec: SiliconSpec, result: FlowResult, outdir: str | Path, verdicts: list[Verdict] | None = None, extra: list[Verdict] | None = None,
            programme: Programme | None = None, rerun: bool = True) -> dict:
    """Write `<outdir>/{bundle/, manifest.json, REPORT.md}`. `extra` = further verdicts (e.g. rtl.equivalence). Returns the manifest."""
    out = Path(outdir)
    bundle = out / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    verdicts = list(verdicts) if verdicts is not None else signoff(result, rerun=rerun)
    verdicts += list(extra or [])
    checks = crosscheck.run_checks(spec, result)
    if programme:
        verdicts += programme_checks(spec, result, programme)
    files = {}
    for k in BUNDLE_KEYS:
        p = result.artifacts.get(k)
        if p and Path(p).is_file():
            dst = bundle / Path(p).name
            shutil.copy2(p, dst)
            files[k] = {"file": f"bundle/{dst.name}", "sha256": _sha(dst), "bytes": dst.stat().st_size}
    missing = [k for k in BUNDLE_KEYS if k not in files]
    cross_ok = bool(checks) and all(c.ok for c in checks)
    ready = result.ok and clean(verdicts) and cross_ok and "gds" in files and "netlist" in files
    manifest = {
        "design": spec.design_name, "pdk": spec.pdk, "config": spec.to_config(), "flow_seconds": result.seconds, "run_dir": str(result.run_dir),
        "tools": toolchain.report(), "files": files, "missing_artifacts": missing, "tapeout_ready": ready,
        "verdicts": [asdict(v) for v in verdicts], "crosschecks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    (out / "REPORT.md").write_text(_markdown(spec, result, manifest, verdicts, checks, programme))
    return manifest


_ICON = {PASS: "PASS", FAIL: "**FAIL**", NOT_RUN: "NOT RUN"}


def _markdown(spec, result, m, verdicts, checks, programme) -> str:
    L = [f"# {spec.design_name} - silicon report ({spec.pdk})", ""]
    L.append(f"**Tapeout ready: {'YES' if m['tapeout_ready'] else 'NO'}** (needs: flow completed, no failed or unchecked sign-off verdict, all cross-checks passed, GDS + netlist present"
             + (f", programme '{programme.name}' constraints met)" if programme else "; no programme constraints given, so shuttle rules were NOT checked)"))
    L += ["", "## Sign-off verdicts", "", "| check | status | source | detail |", "|---|---|---|---|"]
    L += [f"| {v.check} | {_ICON[v.status]} | {v.source} | {v.detail} |" for v in verdicts]
    L += ["", "## Cross-checks (independent re-derivation from the artifacts)", "", "| check | status | detail |", "|---|---|---|"]
    L += [f"| {c.name} | {'NOT RUN' if c.ok is None else ('PASS' if c.ok else '**FAIL**')} | {c.detail} |" for c in checks]
    keys = ["design__instance__count", "design__die__area", "design__core__area", "power__total", "route__wirelength"]
    rows = [(k, result.metric(k)) for k in keys]
    L += ["", "## Flow metrics (reported by the flow, not independently verified)", "", "| metric | value |", "|---|---|"]
    L += [f"| {k} | {'not reported' if v is None else v} |" for k, v in rows]
    L += ["", "## Bundle", ""] + [f"- {k}: `{f['file']}` sha256 `{f['sha256'][:16]}...`" for k, f in m["files"].items()]
    if m["missing_artifacts"]:
        L.append(f"- NOT PRODUCED: {', '.join(m['missing_artifacts'])}")
    L += ["", "## Reproducibility", "", f"Flow time {result.seconds:.0f} s. Tool versions:", ""] + [f"- {k}: {v or 'NOT FOUND'}" for k, v in m["tools"].items()]
    L += ["", "## Not verified by this pipeline", "",
          "- Functional correctness beyond the simulated stimulus (random-vector RTL vs gate-level equivalence is not formal equivalence).",
          "- Analog behaviour, packaging, board-level integration and the shuttle's own acceptance checks."]
    if programme:
        L += ["", f"Programme '{programme.name}' constraints source: {programme.source}"] + [f"- {n}" for n in programme.notes]
    return "\n".join(L) + "\n"
