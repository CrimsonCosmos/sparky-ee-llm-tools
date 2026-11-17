"""What-if study on top of the RTL-validated cycle model. Levers NOT implemented in RTL are PROJECTIONS (the formula change is the lever's assumed behaviour); the baseline row is the validated model.
usage: python tools/tpu_levers.py"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import perf
from tpu_gap import M8B

def cyc(n, cores, batch, ctx, f, pipeline_cmds=False, wr_rows=1, model=M8B):
    total = 0
    gem = perf.workload_gemms(m=batch, ctx=ctx, gqa_group=True, **model)
    for m, k, nn in gem:
        nn = -(-nn // cores)
        for m0 in range(0, m, max(64, batch)):
            mm = min(max(64, batch), m - m0)
            t = -(-k // n) * -(-nn // n)
            load = -(-n // wr_rows)
            p = max(mm, load + 1)
            fixed = (2 * n) + 4 + 1
            total += (t - 1) * p + max(mm, load) + (0 if pipeline_cmds else fixed)
    return total + (2 * n + 5 if pipeline_cmds else 0)

if __name__ == "__main__":
    rows = (("baseline (validated model)", dict()), ("+ pipelined commands (hide fill/drain per GEMM)", dict(pipeline_cmds=True)),
            ("+ 2-row weight write per cycle (2x port)", dict(wr_rows=2)), ("+ both", dict(pipeline_cmds=True, wr_rows=2)),
            ("+ both, 4-row write", dict(pipeline_cmds=True, wr_rows=4)))
    for n, cores, batch in ((16, 1, 16), (128, 4, 64), (128, 4, 256)):
        print(f"--- N={n} x{cores} arrays, Llama-3-8B-class decode, batch {batch}, ctx 1024, 1 GHz assumed ---")
        base = None
        for name, kw in rows:
            c = cyc(n, cores, batch, 1024, 1e9, **kw)
            base = base or c
            macs = sum(m * k * nn for m, k, nn in perf.workload_gemms(m=batch, ctx=1024, gqa_group=True, **M8B))
            print(f"  {name:52s} {c/1e6:9.2f} Mcycles/step  speedup {base/c:5.2f}x  array utilization {macs/(c*n*n*cores)*100:5.1f} %")
