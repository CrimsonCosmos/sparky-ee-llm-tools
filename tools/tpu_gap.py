"""System-level projection with the RTL-validated cycle model (src/tpu/perf.py): decode throughput of a Llama-class int8 model for scaled variants of this TPU, next to published specs of Google's TPUs.
Clock and memory figures of the variants are INPUTS (assumptions), not results; published numbers are cited in docs/TPU_GAP.md. usage: python tools/tpu_gap.py"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import perf

# model: Llama-3-8B-class shapes (int8 weights ~ 8 GB)
M8B = dict(layers=32, hidden=4096, heads=32, kv_heads=8, head_dim=128, ffn=14336, vocab=128256)
M135 = dict(layers=30, hidden=576, heads=9, kv_heads=3, head_dim=64, ffn=1536, vocab=49152)


def step_cycles(core, cores, model, batch, ctx):
    """cycles of one batched decode step; `cores` arrays split every GEMM's output columns (each has its own weight port)."""
    total = 0
    for m, k, n in perf.workload_gemms(m=batch, ctx=ctx, gqa_group=True, **model):
        total += perf.gemm_cycles(core, m, k, -(-n // cores))
    return total


def row(name, n, cores, f_hz, model, batch, ctx, mem_gbs_published=None):
    core = perf.Core(n=n, mmax=max(64, batch), bn=1, bd=1, lat=0)
    cyc = step_cycles(core, cores, model, batch, ctx)
    steps = f_hz / cyc
    macs = n * n * cores
    port = n * cores * f_hz / 1e9
    wbytes = sum(k * nn for _, k, nn in perf.workload_gemms(m=1, ctx=1, gqa_group=True, **model)) / 1e9
    print(f"{name:34s} N={n:3d} x{cores:<2d} {macs:6d} MAC {f_hz/1e6:7.0f} MHz  peak {2*macs*f_hz/1e12:8.3f} TOPS  weight port {port:8.1f} GB/s  "
          f"step {1e3/steps:9.2f} ms  {steps*batch:10.1f} tok/s (B={batch}, ctx {ctx})")


if __name__ == "__main__":
    f_ours = float(sys.argv[1]) if len(sys.argv) > 1 else 70e6      # sky130 clock achievable (input; see docs/TPU.md for the flow result)
    print("== SmolLM2-135M (the model actually run) ==")
    row("this TPU as built (v5, N=16)", 16, 1, f_ours, M135, 1, 128)
    row("this TPU, 16 sequences batched", 16, 1, f_ours, M135, 16, 128)
    print("== Llama-3-8B-class, int8 (weights ~8 GB), decode, ctx 1024 ==")
    for name, n, k, f in (("as built, sky130 clock", 16, 1, f_ours), ("N=128 array, sky130 clock", 128, 1, f_ours), ("N=128 x4 arrays, assumed 1.0 GHz", 128, 4, 1.0e9),
                          ("N=128 x16 arrays, assumed 1.0 GHz", 128, 16, 1.0e9)):
        row(name, n, k, f, M8B, 64, 1024)
    print("reference ceilings from public specs (not simulated): TPU v5e 393 TOPS int8, 819 GB/s HBM => 8 GB weights/step: <= %.0f steps/s memory bound" % (819 / 8))
