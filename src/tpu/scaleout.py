"""Scale-out projection: decode/prefill step time of a Llama-architecture model on a cluster of TPU chips (tensor parallel x pipeline parallel), built ONLY from components validated against the RTL:
the per-chip GEMM schedule (perf.gemm_cycles, exact vs RTL for the integer-beat configurations) and the ring all-gather (perf.ring_allgather_cycles, exact vs RTL). tests/test_tpu_scaleout.py anchors the whole
composition to the measured multi-chip co-simulation (results/tpu/cluster_*.json). Everything that is not a cycle count (clock, link bandwidth, chip memory) is an INPUT, never a result."""
from __future__ import annotations
import math
from dataclasses import dataclass
from . import perf

MODELS = {
    **perf.MODELS,
    "smollm2-360m": dict(layers=32, hidden=960, heads=15, kv_heads=5, head_dim=64, ffn=2560, vocab=49152),
    "llama-3-70b": dict(layers=80, hidden=8192, heads=64, kv_heads=8, head_dim=128, ffn=28672, vocab=128256),
}


@dataclass(frozen=True)
class Chip:
    n: int = 16                 # array dimension
    cores: int = 1              # arrays per chip (tpu_multi), each with its own weight port
    clock_hz: float = 70e6
    mem_bytes: float = 16e9     # weight + KV capacity per chip (input)
    lat: int = 0                # memory latency in cycles


@dataclass(frozen=True)
class Link:
    bytes_per_cycle: int = 16   # inter-chip link width per core cycle (input)
    hop_cycles: int = 0         # extra per-collective fixed latency (input; the RTL ring has 1 cycle per hop built into ring_allgather_cycles)
    reduce: str = "allreduce"   # "allreduce" (ring reduce-scatter + all-gather with fp32 adders, hardware/tpu/v12_ring_allreduce.v) or "gather" (ring all-gather of the partials, no adders)


def _core(chip: Chip, rows: int) -> perf.Core:
    return perf.Core(n=chip.n, mmax=max(64, rows), bn=1, bd=1, lat=chip.lat, bubble=0)


def layer_cycles(chip: Chip, model: dict, tp: int, rows: int, ctx: int, seqs: int) -> int:
    """Cycles of ONE layer on ONE chip of a tp-way tensor-parallel group: `rows` activation rows (decode: sequences in the micro-batch; prefill: tokens), `seqs` sequences with `ctx` cached positions."""
    c = _core(chip, rows)
    hd, hl, kvl = model["head_dim"], model["heads"] // tp, model["kv_heads"] // tp
    rep = model["heads"] // model["kv_heads"]
    il = model["ffn"] // tp
    h = model["hidden"]
    g = lambda m, k, n: perf.gemm_cycles(c, m, k, n, chip.cores)
    cyc = g(rows, h, (hl + 2 * kvl) * hd)                                   # fused qkv (column parallel)
    for _ in range(seqs * kvl):                                              # attention of the local KV heads, GQA grouped
        r = rep * (rows // seqs if seqs else 1)
        cyc += g(r, hd, ctx) + g(r, ctx, hd)
    cyc += g(rows, hl * hd, h)                                               # o_proj (row parallel)
    cyc += g(rows, h, 2 * il)                                                # fused gate/up
    cyc += g(rows, il, h)                                                    # down_proj (row parallel)
    return cyc


def lm_head_cycles(chip: Chip, model: dict, tp: int, rows: int) -> int:
    return perf.gemm_cycles(_core(chip, rows), rows, model["hidden"], -(-model["vocab"] // tp), chip.cores)


def collective_cycles(link: Link, tp: int, rows: int, hidden: int) -> int:
    f = perf.ring_allreduce_cycles if link.reduce == "allreduce" else perf.ring_allgather_cycles
    return f(tp, rows * hidden * 4, link.bytes_per_cycle) + (link.hop_cycles if tp > 1 else 0)


def decode_step(chip: Chip, link: Link, model: dict, tp: int, pp: int, batch: int, ctx: int, microbatches: int = 1) -> dict:
    """One decode step for `batch` sequences (each with `ctx` cached tokens) on tp x pp chips. Pipeline: `microbatches` equal micro-batches; step latency = (m + pp - 1) stage times."""
    m = max(1, min(microbatches, batch))
    rows = -(-batch // m)
    layers_per_stage = -(-model["layers"] // pp)
    per_layer = layer_cycles(chip, model, tp, rows, ctx, rows)
    coll = 2 * collective_cycles(link, tp, rows, model["hidden"])
    stage = layers_per_stage * (per_layer + coll)
    last = lm_head_cycles(chip, model, tp, rows) + collective_cycles(link, tp, rows, 2)
    hop = -(-rows * model["hidden"] * 4 // link.bytes_per_cycle) if pp > 1 else 0          # activation hand-over between pipeline stages
    stage_time = stage + hop
    step = (m + pp - 1) * stage_time + last
    comm = (m + pp - 1) * layers_per_stage * coll
    wbytes = (model["layers"] * (model["hidden"] * (model["heads"] + 2 * model["kv_heads"]) * model["head_dim"] + model["heads"] * model["head_dim"] * model["hidden"] + 3 * model["hidden"] * model["ffn"])
              + model["vocab"] * model["hidden"])
    kv = batch * ctx * 2 * model["kv_heads"] * model["head_dim"] * model["layers"]
    per_chip_bytes = (wbytes + kv) / (tp * pp)
    return {"tp": tp, "pp": pp, "chips": tp * pp, "batch": batch, "microbatches": m, "step_cycles": step, "step_s": step / chip.clock_hz, "tokens_per_s": batch * chip.clock_hz / step,
            "comm_fraction": comm / step, "bytes_per_chip": per_chip_bytes, "fits": per_chip_bytes <= chip.mem_bytes}
