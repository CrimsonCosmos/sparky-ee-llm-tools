"""Scale-out projections (tensor x pipeline x data parallel) from the RTL-validated cycle and ring models (src/tpu/scaleout.py).
CHIP / LINK / MEMORY numbers are INPUTS (assumptions), printed with every table; cycle counts come from the validated models.
usage: python tools/tpu_scaleout.py [--n 128 --cores 4 --clock-mhz 1000 --mem-gb 32 --link 64 --batch 64 --ctx 1024]"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import scaleout

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=128); ap.add_argument("--cores", type=int, default=4); ap.add_argument("--clock-mhz", type=float, default=1000.0)
ap.add_argument("--mem-gb", type=float, default=32.0); ap.add_argument("--link", type=int, default=64, help="link bytes per core cycle"); ap.add_argument("--batch", type=int, default=64)
ap.add_argument("--ctx", type=int, default=1024)
a = ap.parse_args()
chip = scaleout.Chip(n=a.n, cores=a.cores, clock_hz=a.clock_mhz * 1e6, mem_bytes=a.mem_gb * 1e9)
link = scaleout.Link(bytes_per_cycle=a.link)
print(f"ASSUMED inputs: chip = {a.cores} arrays of {a.n}x{a.n} at {a.clock_mhz:g} MHz ({2 * a.n * a.n * a.cores * chip.clock_hz / 1e12:.1f} TOPS peak, weight port {a.n * a.cores * chip.clock_hz / 1e9:.0f} GB/s), "
      f"{a.mem_gb:g} GB per chip, link {a.link} B/cycle ({a.link * chip.clock_hz / 1e9:.0f} GB/s per direction); decode batch {a.batch}, context {a.ctx}; int8 weights and KV.\n")


def best(model, tp, pp, batch):
    cands = [scaleout.decode_step(chip, link, scaleout.MODELS[model], tp, pp, batch, a.ctx, m) for m in (1, 2, 4, 8, 16) if m <= batch]
    return max(cands, key=lambda r: r["tokens_per_s"])


def show(title, rows, base=None):
    print(title)
    print("  chips  TP x PP x DP  micro-b   step ms   tokens/s   comm %   GB/chip  fits   scaling eff vs 1 replica baseline")
    for r, dp in rows:
        tot = r["tokens_per_s"] * dp
        eff = f"{100 * tot / (base['tokens_per_s'] * r['chips'] * dp / base['chips']):6.1f} %" if base else "   -"
        print(f"  {r['chips'] * dp:5d}  {r['tp']:2d} x {r['pp']:2d} x {dp:2d}   {r['microbatches']:5d}   {r['step_s'] * 1e3:8.2f}  {tot:9.1f}  {100 * r['comm_fraction']:6.1f}  {r['bytes_per_chip'] / 1e9:8.2f}  {'yes' if r['fits'] else 'NO '}   {eff}")
    print()


# 1. strong scaling of an 8B-class model over tensor-parallel chips (TP <= number of KV heads)
base = best("llama-3-8b", 1, 1, a.batch)
show("Llama-3-8B-class, strong scaling with tensor parallelism (same batch, more chips)", [(best("llama-3-8b", tp, 1, a.batch), 1) for tp in (1, 2, 4, 8)], base)
# 2. beyond the KV-head limit: add pipeline stages and data-parallel replicas (replicas serve independent batches)
rows = []
for tp, pp, dp in ((8, 1, 1), (8, 1, 2), (8, 1, 4), (8, 1, 8), (8, 2, 8), (8, 4, 8)):
    rows.append((best("llama-3-8b", tp, pp, a.batch), dp))
show("Llama-3-8B-class, growing the cluster (TP=8 groups, pipeline stages, data-parallel replicas; every replica serves its own batch)", rows, base=None)
# 3. a 70B-class model needs several chips just to hold the weights
rows = []
for tp, pp in ((4, 1), (8, 1), (8, 2), (8, 4), (8, 8)):
    rows.append((best("llama-3-70b", tp, pp, a.batch), 1))
show("Llama-3-70B-class (weights alone ~70 GB): tensor x pipeline parallel", rows, base=None)
