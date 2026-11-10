"""Deterministic source mutations of a Verilog design (operator swaps, constant changes, bit-index changes). Used for (a) a search baseline that needs no LLM and (b) mutation-testing the scorer itself:
every mutant's formal verdict must agree with exhaustive simulation where that is possible."""
from __future__ import annotations

import random
import re

_OPS = [("*", "+"), ("+", "-"), ("-", "+"), ("&", "|"), ("|", "&"), ("^", "&"), ("<", ">"), (">", "<"), ("==", "!="), ("!=", "==")]
_TOKEN = re.compile(r"(?<![<>=!&|^+\-])(==|!=|[*+\-&|^<>])(?![<>=&|])")
_NUM = re.compile(r"(?:(?<![\w'])|(?<='d))(\d+)(?![\w'])")
_IDX = re.compile(r"\[(\d+)\]")


def candidates(text: str) -> list[tuple[int, int, str]]:
    """All single mutations as (start, end, replacement) over the non-comment, non-header part of the text."""
    body_start = text.find(");") + 2 if ");" in text else 0
    out = []
    for m in _TOKEN.finditer(text, body_start):
        for a, b in _OPS:
            if m.group(1) == a:
                out.append((m.start(1), m.end(1), b))
    for m in _NUM.finditer(text, body_start):
        v = int(m.group(1))
        for nv in {v + 1, max(0, v - 1)} - {v}:
            out.append((m.start(1), m.end(1), str(nv)))
    for m in _IDX.finditer(text, body_start):
        v = int(m.group(1))
        for nv in {v + 1, max(0, v - 1)} - {v}:
            out.append((m.start(1), m.end(1), str(nv)))
    return out


def mutants(text: str, n: int, seed: int = 0) -> list[str]:
    """Up to n distinct single-edit mutants, reproducible for a seed."""
    cands = candidates(text)
    random.Random(seed).shuffle(cands)
    seen, out = {text}, []
    for s, e, r in cands:
        m = text[:s] + r + text[e:]
        if m not in seen:
            seen.add(m)
            out.append(m)
        if len(out) >= n:
            break
    return out


def mutation_proposer(seed_text: str, base_seed: int = 0):
    """A ScriptedProposer-compatible function: each call returns mutants of the given seed (an LLM-free baseline for the loop)."""
    state = {"k": 0}

    def propose(prompt: str, n: int) -> list[str]:
        state["k"] += 1
        return mutants(seed_text, n, base_seed + state["k"])
    return propose
