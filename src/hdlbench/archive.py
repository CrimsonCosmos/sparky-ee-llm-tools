"""Memory of every attempt (JSONL, append-only): code, per-stage results, reward, cost. Gives the loop dedup, the best-so-far and the top-k parents, and gives you the full experiment log."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .score import Score


@dataclass
class Entry:
    iteration: int
    parent: str | None
    code: str
    score: Score


class Archive:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.entries: dict[str, Entry] = {}
        self.order: list[str] = []
        if self.path and self.path.parent:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def __contains__(self, h: str) -> bool:
        return h in self.entries

    def get(self, h: str) -> Entry:
        return self.entries[h]

    def add(self, iteration: int, parent: str | None, code: str, score: Score) -> Entry:
        e = Entry(iteration, parent, code, score)
        self.entries[score.code_hash] = e
        self.order.append(score.code_hash)
        if self.path:
            m = score.metrics
            rec = {"iteration": iteration, "parent": parent, "hash": score.code_hash, "reward": score.reward, "solved": score.solved,
                   "stages": {k: [v.status, v.fraction, v.detail[:300]] for k, v in score.stages.items()},
                   "ratios": score.ratios, "metrics": ({"luts": m.luts, "flops": m.flops, "depth": m.depth, "gates": m.gates, "fmax_mhz": m.fmax_mhz} if m else None),
                   "warnings": score.warnings, "code": code}
            with self.path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        return e

    def key(self, e: Entry) -> tuple:
        """Ranking: solved first, then reward (which already includes the quality bonus), newest last as tie-break."""
        return (e.score.solved, e.score.reward)

    def best(self) -> Entry | None:
        return max((self.entries[h] for h in self.order), key=self.key, default=None)

    def top(self, k: int) -> list[Entry]:
        return sorted((self.entries[h] for h in self.order), key=self.key, reverse=True)[:k]

    def rewards(self) -> list[float]:
        return [self.entries[h].score.reward for h in self.order]
