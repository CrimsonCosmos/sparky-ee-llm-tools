"""The reward: staged, partial-credit scoring of one candidate against one problem. Stages gate each other (a design that does not compile earns nothing later); a design is SOLVED only when the formal
stage proves equivalence and every test passes. Weights are reward-shaping choices of the benchmark (RewardConfig, user-tunable), not physical values.

Held-out vectors are never shown: feedback to the proposer lists failing VISIBLE vectors and formal counterexamples, and only the pass fraction for the held-out set."""
from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import formal, metrics, sim, tools
from .problem import Problem, ports_of

STAGES = ("interface", "compiles", "synthesizable", "visible", "heldout", "formal", "quality")
RESERVED_MODULES = {"tb", "gold", "gate", "miter", "formal_top"}                     # names the harness itself uses
FORBIDDEN = re.compile(r"\$(?:fopen|fclose|fdisplay|fwrite|fscanf|fgets|fread|readmemh|readmemb|system|test\$plusargs|value\$plusargs|dumpfile|dumpvars|stop|finish)\b|`include\b|`define\s+\w+\s*\$|\bforce\b|\binitial\b|\b(?:reg|logic|integer|bit)\b[^;()]*?\w\s*=[^=]")


@dataclass
class RewardConfig:
    w_compiles: float = 0.05
    w_synth: float = 0.05
    w_visible: float = 0.15
    w_heldout: float = 0.25
    w_formal: float = 0.40                      # full weight only for a PROOF; a bounded check earns bounded_fraction of it
    w_quality: float = 0.10                     # only for formally proved designs
    bounded_fraction: float = 0.25
    quality_cap: float = 2.0                    # a metric 2x better than the reference gets the full quality credit for it; equal to the reference gets half
    max_chars: int = 200_000
    lut_warn_case_items: int = 64               # heuristic WARNING threshold for table-style designs (formal remains the judge)
    formal_timeout: int = 120
    sim_timeout: int = 30                       # per simulation run; a design that loops forever fails 'visible' instead of stalling the scorer
    tool_timeout: int = 60                      # Yosys elaboration / Icarus compile


@dataclass
class StageResult:
    name: str
    status: str                                 # pass | fail | not_run | partial
    detail: str
    fraction: float = 0.0                       # 0..1 credit within the stage's weight


@dataclass
class Score:
    problem: str
    code_hash: str
    stages: dict[str, StageResult] = field(default_factory=dict)
    reward: float = 0.0
    solved: bool = False
    metrics: metrics.Metrics | None = None
    ratios: dict[str, float | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    feedback: str = ""
    mismatches: list[sim.Mismatch] = field(default_factory=list)
    counterexample: dict[str, str] | None = None
    solved_by: str = ""                         # 'formal proof' | 'tests only (NOT proved)' | ''

    def stage(self, name: str) -> StageResult | None:
        return self.stages.get(name)


def code_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def scan(p: Problem, text: str, cfg: RewardConfig) -> list[str]:
    """Reasons the candidate text is rejected before any tool runs ([] = acceptable)."""
    bad = []
    if len(text) > cfg.max_chars:
        bad.append(f"candidate is {len(text)} characters (limit {cfg.max_chars})")
    m = FORBIDDEN.search(text)
    if m:
        bad.append(f"forbidden construct '{m.group(0).strip()}' (file/system tasks, includes, force and initial blocks / declaration initialisers are not allowed in a design: state must come from reset)")
    for name in re.findall(r"^\s*module\s+(\w+)", text, re.M):
        if name in RESERVED_MODULES:
            bad.append(f"module name '{name}' is reserved by the harness")
    if not re.search(rf"^\s*module\s+{re.escape(p.top)}\b", text, re.M):
        bad.append(f"no module named '{p.top}'")
    return bad


def _yosys_check(p: Problem, f: Path, td: Path, timeout: int = 60) -> tuple[bool, str]:
    y = tools.which("yosys")
    rc, text = tools.run([y, "-p", f"read_verilog -sv {f}; hierarchy -top {p.top}; flatten; proc; tee -o st.txt stat; check -assert", "-l", "y.log"], td, timeout)
    log = (td / "y.log").read_text() if (td / "y.log").is_file() else text
    if rc != 0:
        why = re.findall(r"(?:ERROR|Warning): (.*)", log)
        return False, (why[-1] if why else log.strip()[-200:])
    st = (td / "st.txt").read_text() if (td / "st.txt").is_file() else ""
    latches = sum(int(n) for n, c in re.findall(r"^\s*(\d+)\s+\$(\w*latch\w*)", st, re.M))
    if latches and p.kind == "comb":
        return False, f"{latches} latch(es) inferred in a combinational design (incomplete assignment)"
    return True, "elaborates, no combinational loops" + (f"; {latches} latch(es) inferred" if latches else "")


def evaluate(p: Problem, text: str, cfg: RewardConfig | None = None, ref_metrics: metrics.Metrics | None = None, do_formal: bool = True, do_metrics: bool = True) -> Score:
    cfg = cfg or RewardConfig()
    s = Score(problem=p.name, code_hash=code_hash(text))
    st = s.stages

    def done(feedback: str = "") -> Score:
        for n in STAGES:
            st.setdefault(n, StageResult(n, "not_run", "an earlier stage failed"))
        w = {"compiles": cfg.w_compiles, "synthesizable": cfg.w_synth, "visible": cfg.w_visible, "heldout": cfg.w_heldout, "formal": cfg.w_formal, "quality": cfg.w_quality}
        s.reward = round(sum(w[n] * st[n].fraction for n in w), 6) if st["interface"].status == "pass" else 0.0          # no credit for a design with the wrong interface
        tests_ok = st["visible"].fraction == 1.0 and st["heldout"].fraction == 1.0
        s.solved = st["formal"].status == "pass" and tests_ok
        s.solved_by = ("formal proof" if "BOUNDED check" not in " ".join(s.warnings) else f"bounded check ({p.formal_depth} cycles) + tests, NOT an unbounded proof") if s.solved else ""
        if p.formal_mode == "off" and tests_ok and st["interface"].status == "pass":
            s.solved, s.solved_by = True, "tests only (NOT proved)"
        s.feedback = feedback or _feedback(p, s)
        return s

    bad = scan(p, text, cfg)
    if bad:
        st["interface"] = StageResult("interface", "fail", "; ".join(bad))
        return done()
    with tempfile.TemporaryDirectory(prefix="hb_eval_") as td:
        td = Path(td)
        f = td / f"{p.top}.v"
        f.write_text(text)
        # 1. compiles
        try:
            iv = tools.which("iverilog")
            rc, out = tools.run([iv, "-g2012", "-o", str(td / "c.vvp"), str(f)], td, cfg.tool_timeout)
            if rc != 0:
                raise RuntimeError(out.strip()[-400:])
        except RuntimeError as e:
            st["compiles"] = StageResult("compiles", "fail", str(e))
            return done()
        st["compiles"] = StageResult("compiles", "pass", "Icarus Verilog -g2012 compiles it", 1.0)
        # 0. interface
        try:
            got = ports_of([f], p.top)
        except RuntimeError as e:
            st["interface"] = StageResult("interface", "fail", f"could not elaborate: {e}")
            return done()
        want = p.ports
        if got != want:
            diff = [f"{n}: expected {want.get(n)}, got {got.get(n)}" for n in sorted(set(want) | set(got)) if want.get(n) != got.get(n)]
            st["interface"] = StageResult("interface", "fail", "ports differ from the specification: " + "; ".join(diff))
            return done()
        st["interface"] = StageResult("interface", "pass", "ports match the reference exactly", 1.0)
        # 2. synthesizable
        ok, why = _yosys_check(p, f, td, cfg.tool_timeout)
        if not ok:
            st["synthesizable"] = StageResult("synthesizable", "fail", why)
            return done()
        st["synthesizable"] = StageResult("synthesizable", "pass", why, 1.0)
        if p.kind == "comb" and len(re.findall(r"'[hHbBdD][0-9a-fA-F_xXzZ]+\s*:", text)) > cfg.lut_warn_case_items:
            s.warnings.append("many constant case items: looks like a lookup table (only the held-out tests and the formal proof can judge it)")
        # 3/4. tests
        try:
            for which in ("visible", "heldout"):
                vec = sim.vectors(p, which)
                ref_rows = sim.simulate(p, p.reference_files, vec, cfg.sim_timeout)
                rows = sim.simulate(p, [f], vec, cfg.sim_timeout)
                ok_n, tot, mism = sim.compare(p, vec, ref_rows, rows)
                frac = ok_n / tot if tot else 0.0
                st[which] = StageResult(which, "pass" if ok_n == tot else ("partial" if ok_n else "fail"), f"{ok_n}/{tot} {which} vectors match the reference", frac)
                if which == "visible":
                    s.mismatches = mism
        except RuntimeError as e:
            st["visible"] = StageResult("visible", "fail", f"simulation error: {e}")
            return done()
        # 5. formal
        if p.formal_mode == "off":
            st["formal"] = StageResult("formal", "pass" if st["visible"].fraction == 1.0 and st["heldout"].fraction == 1.0 else "not_run",
                                       "formal checking is OFF for this problem (see problem.json): the tests are the only judge, so this is NOT a proof", 1.0 if st["visible"].fraction == 1.0 and st["heldout"].fraction == 1.0 else 0.0)
        elif not do_formal:
            st["formal"] = StageResult("formal", "not_run", "disabled")
        elif st["visible"].fraction < 1.0 or st["heldout"].fraction < 1.0:
            st["formal"] = StageResult("formal", "not_run", "skipped: simulation already shows a difference")
        else:
            r = formal.check(p, [f], cfg.formal_timeout)
            bounded_ok = p.formal_mode == "bounded" and r.status == formal.BOUNDED
            frac = 1.0 if (r.proved or bounded_ok) else (cfg.bounded_fraction if r.status == formal.BOUNDED else 0.0)
            st["formal"] = StageResult("formal", "pass" if (r.proved or bounded_ok) else ("partial" if r.status == formal.BOUNDED else ("fail" if r.status == formal.DIFFERENT else "not_run")), f"{r.method}: {r.detail}", frac)
            if bounded_ok:
                s.warnings.append(f"formal result is a BOUNDED check ({p.formal_depth} cycles after reset), not an unbounded proof; judged together with held-out simulation incl. mid-run resets")
            s.counterexample = r.counterexample
        # 6. quality (only for proved designs)
        if st["formal"].status == "pass" and do_metrics and (p.formal_mode == "off" or st["formal"].detail):
            m = metrics.measure(p, [f])
            s.metrics = m
            if ref_metrics is None:
                ref_metrics = metrics.measure(p, p.reference_files)
            s.ratios = metrics.ratios(m, ref_metrics)
            if m.fmax_mhz and ref_metrics.fmax_mhz:
                s.ratios["fmax_mhz_inverse"] = ref_metrics.fmax_mhz / m.fmax_mhz            # <1 = faster than the reference
            qs = []
            if m.other:                                                      # block RAM / DSP would hide cost from the LUT count: no quality credit, say why
                s.warnings.append(f"design uses non-LUT primitives {m.other}: quality credit withheld (cost not comparable)")
                s.ratios = {}
            for k, r in s.ratios.items():
                if r is None:
                    continue
                qs.append(min(cfg.quality_cap, (1.0 / r) if r > 0 else cfg.quality_cap) / cfg.quality_cap)
            q = sum(qs) / len(qs) if qs else 0.0
            st["quality"] = StageResult("quality", "pass" if qs else "not_run", "relative to the reference design (" + ", ".join(f"{k} x{r:.2f}" for k, r in s.ratios.items() if r is not None) + "); nextpnr values are tool estimates", q)
    return done()


def _feedback(p: Problem, s: Score) -> str:
    lines = [f"reward {s.reward:.3f} ({'SOLVED by ' + s.solved_by if s.solved else 'not solved'})"]
    for n in STAGES:
        r = s.stages[n]
        lines.append(f"- {n}: {r.status} - {r.detail}")
    if s.mismatches:
        lines.append("failing visible vectors (inputs -> expected vs got):")
        lines += [f"  {m.text()}" for m in s.mismatches]
    if s.counterexample:
        lines.append("formal counterexample: " + ", ".join(f"{k}={v}" for k, v in s.counterexample.items()))
    if s.metrics:
        m = s.metrics
        lines.append(f"cost: {m.luts} LUT4, {m.flops} FF, depth {m.depth} LUT levels" + (f", Fmax {m.fmax_mhz} MHz (estimate)" if m.fmax_mhz else ""))
    lines += [f"warning: {w}" for w in s.warnings]
    return "\n".join(lines)
