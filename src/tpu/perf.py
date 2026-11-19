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


def gemm_cycles(c: Core, m: int, k: int, n_out: int, cores: int = 1) -> int:
    """`cores` arrays (tpu_multi) split the output column tiles; the slowest core (largest share, ceil(nt/cores) tiles) sets the time."""
    if cores > 1:
        n_out = -(-(-(-n_out // c.n)) // cores) * c.n
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


MODELS = {   # published architecture shapes (config.json of the open-weight models)
    "smollm2-135m": dict(layers=30, hidden=576, heads=9, kv_heads=3, head_dim=64, ffn=1536, vocab=49152),
    "llama-3-8b": dict(layers=32, hidden=4096, heads=32, kv_heads=8, head_dim=128, ffn=14336, vocab=128256),
}


def decode_step_cycles(core: Core, cores: int, model: dict, batch: int, ctx: int, gqa_group: bool = True) -> int:
    """Cycles of one batched decode step: batch sequences, each with ctx cached positions, `cores` arrays splitting the output columns of every GEMM."""
    return sum(gemm_cycles(core, m, k, n, cores) for m, k, n in workload_gemms(m=batch, ctx=ctx, gqa_group=gqa_group, **model))


def project(model: str, n: int, cores: int, clock_hz: float, batch: int, ctx: int, bubble: int = 0, mem_beats: tuple[int, int] = (1, 1), lat: int = 0) -> dict:
    """Throughput projection with the RTL-validated schedule; clock and memory rate are inputs (assumptions), not results."""
    core = Core(n=n, mmax=max(64, batch), bn=mem_beats[0], bd=mem_beats[1], lat=lat, bubble=bubble)
    cyc = decode_step_cycles(core, cores, MODELS[model], batch, ctx)
    return {"model": model, "n": n, "cores": cores, "clock_hz": clock_hz, "batch": batch, "ctx": ctx, "cycles_per_step": cyc, "steps_per_s": clock_hz / cyc, "tokens_per_s": batch * clock_hz / cyc,
            "weight_port_gb_per_s": n * cores * mem_beats[0] / mem_beats[1] * clock_hz / 1e9, "peak_tops": 2 * n * n * cores * clock_hz / 1e12}


def ring_allgather_cycles(chips: int, nbytes: int, link_bytes_per_cycle: int) -> int:
    """Cycles of the RTL ring all-gather (hardware/tpu/v11/tpu_ring.v): every chip contributes `nbytes`; beats = ceil(nbytes / link width); the link of every node carries (chips-1)*beats beats
    back to back (own vector first, then cut-through forwarding), plus one cycle of delivery; with very short vectors the C-1 hop latency (twice: out and back around) dominates.
    Exact against the RTL (tests/test_tpu_ring.py)."""
    if chips <= 1:
        return 0
    nb = -(-nbytes // link_bytes_per_cycle)
    return max((chips - 1) * nb + 1, 2 * (chips - 1))


def ring_allreduce_cycles(chips: int, nbytes: int, link_bytes_per_cycle: int) -> int:
    """Cycles of the RTL ring all-reduce (hardware/tpu/v12_ring_allreduce.v): the vector (nbytes of float32) is padded to C chunks of nbc beats (beat = link width); 2 (C-1) steps (reduce-scatter then all-gather)
    of nbc + 1 cycles, plus one cycle for the start pulse. Exact against the RTL (tests/test_tpu_ring.py)."""
    if chips <= 1:
        return 0
    beats = -(-nbytes // link_bytes_per_cycle)
    nbc = -(-beats // chips)
    return 2 * (chips - 1) * (nbc + 1) + 1
