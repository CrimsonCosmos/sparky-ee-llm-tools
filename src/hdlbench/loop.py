"""The search loop: ask a proposer for candidates, score them with the deterministic pipeline, keep everything in the archive, feed structured feedback (failing visible vectors, formal counterexample, cost vs the
reference) into the next prompt. Strategies: 'refine' (improve the best design) and 'population' (show the top-k, ask for mutations/combinations). Budgets cap iterations, candidates, time and tokens."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import metrics
from .archive import Archive, Entry
from .problem import Problem
from .proposers import Proposer
from .score import RewardConfig, Score, code_hash, evaluate


@dataclass
class Budget:
    max_iterations: int = 10
    max_candidates: int = 100
    max_seconds: float | None = None
    max_tokens: int | None = None               # input + output, only counted if the proposer reports usage


@dataclass
class LoopConfig:
    strategy: str = "refine"                    # refine | population
    candidates_per_iteration: int = 4
    top_k: int = 3                              # parents shown by 'population'
    optimize: bool = True                       # keep improving cost after the first proof; False = stop at the first solved design
    show_reference_cost: bool = True            # tell the proposer what the human reference costs (the target to beat)
    workers: int = 2                            # parallel evaluations (each runs several tool processes)
    reward: RewardConfig = field(default_factory=RewardConfig)


@dataclass
class RunResult:
    problem: str
    best: Entry | None
    baseline: Score
    archive: Archive
    iterations: int
    candidates: int
    stopped: str
    seconds: float
    usage: dict

    @property
    def solved(self) -> bool:
        return bool(self.best and self.best.score.solved)

    def summary(self) -> str:
        b = self.best
        s = f"{self.problem}: {'SOLVED' if self.solved else 'not solved'} after {self.candidates} candidates / {self.iterations} iterations ({self.stopped}, {self.seconds:.0f} s)"
        if b:
            s += f"; best reward {b.score.reward:.3f} (reference {self.baseline.reward:.3f})"
            if b.score.metrics:
                m = b.score.metrics
                s += f"; {m.luts} LUT4 / depth {m.depth}" + (f" vs reference {self.baseline.metrics.luts} / {self.baseline.metrics.depth}" if self.baseline.metrics else "")
        return s


PREAMBLE = """You are designing a digital circuit in Verilog (IEEE 1364/1800 synthesizable subset). Reply with ONE complete Verilog module in a ```verilog code block and nothing else needed.
Rules: the module name and ports must match the specification exactly; no file or system tasks ($readmem*, $fopen, $system, ...), no `include, no latches in combinational logic, no testbench constructs.
Your design is checked by simulation on hidden vectors and then by FORMAL equivalence to a golden design, so it must be exactly correct for every input (and from reset for sequential designs).
Among correct designs, fewer LUT4s and fewer logic levels score higher.
"""


def build_prompt(p: Problem, parents: list[Entry], cfg: LoopConfig, reference: Score | None, tried: int) -> str:
    out = [PREAMBLE, "## Specification", p.spec_text.strip()]
    if cfg.show_reference_cost and reference and reference.metrics:
        m = reference.metrics
        out.append(f"\nThe golden reference design costs {m.luts} LUT4, {m.flops} flip-flops, {m.depth} LUT levels (on iCE40). Beating it with an equivalent design earns a bonus.")
    if not parents:
        out.append("\nWrite your first design.")
        return "\n".join(out)
    if cfg.strategy == "refine" or len(parents) == 1:
        e = parents[0]
        out += [f"\n## Your best design so far (attempt #{tried}, reward {e.score.reward:.3f})", "```verilog", e.code.strip(), "```", "## Tool feedback on it", e.score.feedback,
                "\nFix every reported problem. If it is already proved correct, reduce its cost (fewer LUTs / levels) without changing its behaviour."]
    else:
        out.append("\n## Best designs so far (highest reward first) with tool feedback")
        for i, e in enumerate(parents, 1):
            out += [f"\n### Design {i} (reward {e.score.reward:.3f}, {'proved correct' if e.score.solved else 'NOT proved correct'})", "```verilog", e.code.strip(), "```", e.score.feedback]
        out.append("\nWrite a NEW design that fixes the failures and/or combines the best ideas of these; do not repeat a design that already failed.")
    return "\n".join(out)


def run(p: Problem, proposer: Proposer, budget: Budget | None = None, cfg: LoopConfig | None = None, archive_path: str | Path | None = None) -> RunResult:
    budget, cfg = budget or Budget(), cfg or LoopConfig()
    t0 = time.time()
    ref_text = "\n".join(Path(f).read_text() for f in p.reference_files)
    baseline = evaluate(p, ref_text, cfg.reward)                                  # the human design, scored by the same pipeline
    arch = Archive(archive_path)
    n_cand, it, stopped = 0, 0, "iteration budget"
    while it < budget.max_iterations:
        if budget.max_seconds is not None and time.time() - t0 > budget.max_seconds:
            stopped = "time budget"; break
        used = proposer.usage.get("input_tokens", 0) + proposer.usage.get("output_tokens", 0)
        if budget.max_tokens is not None and used >= budget.max_tokens:
            stopped = "token budget"; break
        if n_cand >= budget.max_candidates:
            stopped = "candidate budget"; break
        it += 1
        parents = arch.top(cfg.top_k if cfg.strategy == "population" else 1)
        prompt = build_prompt(p, parents, cfg, baseline, len(arch.order))
        n = min(cfg.candidates_per_iteration, budget.max_candidates - n_cand)
        try:
            texts = proposer.propose(prompt, n)
        except RuntimeError as e:
            stopped = f"proposer unavailable: {e}"; break
        n_cand += len(texts)
        fresh = []
        for t in texts:
            h = code_hash(t)
            if h not in arch and t.strip() and all(code_hash(x) != h for x in fresh):
                fresh.append(t)
        parent = parents[0].score.code_hash if parents else None
        with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as ex:
            scores = list(ex.map(lambda t: evaluate(p, t, cfg.reward, ref_metrics=baseline.metrics), fresh))
        for t, s in zip(fresh, scores):
            arch.add(it, parent, t, s)
        best = arch.best()
        if best and best.score.solved and not cfg.optimize:
            stopped = "solved"; break
    return RunResult(p.name, arch.best(), baseline, arch, it, n_cand, stopped, time.time() - t0, dict(proposer.usage))
