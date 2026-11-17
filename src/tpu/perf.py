"""Closed-form cycle model of the TPU core (versions v3-v5 share the schedule; they differ only in array latency), fitted to and TESTED against the RTL (tests/test_tpu_perf.py checks exact equality
with the Verilator model over random shapes, bandwidths and latencies; the RTL is the reference, nothing here is used to produce the expected value).
GEMM of M rows over K x N weights on an NA x NA array:
    tiles T = ceil(K/NA) * ceil(N/NA);  L = cycles to stream one tile = ceil(NA * bd / bn). Exact when NA*bd/bn is an integer; for fractional rates (credit carries across tiles) the model is within max(2 cycles, 1 %) (tested) for a port delivering bn/bd beats (NA bytes each) per cycle;
    period P = max(M, L, NA + bubble): the shadow-weight write port takes one row per cycle, plus (v3-v5) one bubble at each tile hand-over;
    cycles = (T-1) * P + M + L + lat + (LAT + 3 + bubble) + (1 if lat == 0 and bn >= bd else 0),  LAT = array latency (2*NA - 1 for v3, 2*NA for v4/v5).
M larger than the activation-buffer depth `mmax` is chunked (weights re-streamed per chunk)."""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Core:
    n: int = 16                 # array dimension
    mmax: int = 64              # activation rows per command
    bn: int = 1                 # beats per cycle numerator (one beat = n bytes)
    bd: int = 1
    lat: int = 0                # memory latency (cycles)
    array_lat_extra: int = 0    # 0 for v4/v5/v6 (LAT = 2n); -1 for v3 (LAT = 2n-1)
    bubble: int = 1             # idle cycle at each tile hand-over: 1 for v3-v5, 0 for v6 (swap decided in the cycle of the last weight beat)


def gemm_cycles(c: Core, m: int, k: int, n_out: int) -> int:
    total = 0
    for m0 in range(0, m, c.mmax):
        mm = min(c.mmax, m - m0)
        t = -(-k // c.n) * -(-n_out // c.n)
        beats = -(-c.n * c.bd // c.bn)
        p = max(mm, beats, c.n + c.bubble)
        edge = 1 if (c.lat == 0 and c.bn >= c.bd) else 0
        total += (t - 1) * p + mm + beats + c.lat + (2 * c.n + c.array_lat_extra) + 3 + c.bubble + edge
    return total


def workload_gemms(layers: int, hidden: int, heads: int, kv_heads: int, head_dim: int, ffn: int, vocab: int, m: int, ctx: int, gqa_group: bool = True) -> list[tuple[int, int, int]]:
    """GEMM list (M, K, N) of one forward step of a Llama-style model for `m` rows (tokens in prefill, sequences in batched decode) with `ctx` cached positions per sequence.
    Attention GEMMs are per sequence (rows = query heads of one KV group when gqa_group); counted once per sequence for the m rows (m sequences => m times)."""
    rep = heads // kv_heads
    g = []
    for _ in range(layers):
        g += [(m, hidden, heads * head_dim), (m, hidden, kv_heads * head_dim), (m, hidden, kv_heads * head_dim), (m, heads * head_dim, hidden),
              (m, hidden, ffn), (m, hidden, ffn), (m, ffn, hidden)]
        for _seq in range(m):                       # decode: one query row per sequence; prefill callers pass m=1 per-sequence tokens separately
            for _kv in range(kv_heads):
                rows = rep if gqa_group else 1
                for _r in range(1 if gqa_group else rep):
                    g += [(rows, head_dim, ctx), (rows, ctx, head_dim)]
    g.append((m, hidden, vocab))
    return g


def workload_cycles(c: Core, gemms: list[tuple[int, int, int]]) -> int:
    return sum(gemm_cycles(c, *x) for x in gemms)
