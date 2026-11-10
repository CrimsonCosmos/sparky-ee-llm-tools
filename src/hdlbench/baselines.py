"""Scores of the golden reference designs through the same pipeline (so 'beating the human design' has a number), and an LLM-free search baseline (random source mutations) for the loop."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import loop, metrics, mutate, problem, proposers
from .score import RewardConfig, evaluate


@dataclass
class Row:
    name: str
    kind: str
    reward: float
    solved: bool
    luts: int | None
    flops: int | None
    depth: int | None
    gates: int | None
    fmax_mhz: float | None
    note: str


def reference_rows(names: list[str] | None = None, root: Path | None = None, cfg: RewardConfig | None = None) -> list[Row]:
    rows = []
    for n in names or problem.list_problems(root):
        p = problem.get(n, root)
        s = evaluate(p, "\n".join(Path(f).read_text() for f in p.reference_files), cfg)
        m = s.metrics or metrics.measure(p, p.reference_files)
        rows.append(Row(n, p.kind, s.reward, s.solved, m.luts, m.flops, m.depth, m.gates, m.fmax_mhz, "; ".join(x for x in m.notes if "failed" in x or "NOT RUN" in x)[:100]))
    return rows


def markdown(rows: list[Row]) -> str:
    out = ["| problem | kind | reward | solved | LUT4 | FF | depth | 2-in gates | Fmax MHz (estimate) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r.name} | {r.kind} | {r.reward:.3f} | {'yes' if r.solved else 'NO'} | {r.luts} | {r.flops} | {r.depth} | {r.gates} | {r.fmax_mhz if r.fmax_mhz else '-'} |")
    return "\n".join(out)


def mutation_search(p: problem.Problem, start_text: str, iterations: int = 5, per_iter: int = 8, seed: int = 0):
    """LLM-free baseline: the loop with a proposer that only applies random single-edit mutations to `start_text`."""
    prop = proposers.ScriptedProposer(mutate.mutation_proposer(start_text, seed))
    return loop.run(p, prop, loop.Budget(max_iterations=iterations, max_candidates=iterations * per_iter), loop.LoopConfig(candidates_per_iteration=per_iter, optimize=False))
