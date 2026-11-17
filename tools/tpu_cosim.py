"""Run SmolLM2-135M (W8A8 + SmoothQuant) with EVERY matrix product executed on the TPU RTL (Verilator) and checked bit-exactly against the
integer numpy reference. usage: python tools/tpu_cosim.py <version> [--n 16] [--prefill 32] [--decode 8] [--bw 1/1] [--lat 0] [--out results.json]"""
import argparse, json, sys, time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import llm, quant, rtl

D = "/home/user/data/smollm2"
ap = argparse.ArgumentParser()
ap.add_argument("version"); ap.add_argument("--n", type=int, default=16); ap.add_argument("--mmax", type=int, default=64); ap.add_argument("--ktmax", type=int, default=96)
ap.add_argument("--prefill", type=int, default=32); ap.add_argument("--decode", type=int, default=8)
ap.add_argument("--bw", default="1/1"); ap.add_argument("--lat", type=int, default=0); ap.add_argument("--out"); ap.add_argument("--alpha", type=float, default=0.5)
a = ap.parse_args()
bwn, bwd = (int(v) for v in a.bw.split("/"))

tk = llm.tokenizer(D)
text = open("/home/user/data/wikitext2/test.txt").read()[:20000]
ids = np.array(tk.encode(text).ids)[:a.prefill]
sm = Path(D) / f"smooth_a{a.alpha}.npz"
if sm.exists():
    wsm = dict(np.load(sm))
else:
    calib = np.array(tk.encode(open("/home/user/data/wikitext2/validation.txt").read()[:60000]).ids)[:1537]
    wsm = quant.smooth_weights(llm.Llama(D), calib, a.alpha)
    np.savez(sm, **wsm)

wd = Path(f"/home/user/data/tpu_build/{a.version}_n{a.n}")
exe = rtl.build_harness(a.version, a.n, a.mmax, a.ktmax, wd)
sim = rtl.Sim(exe, a.mmax, (bwn, bwd), a.lat)
checked = {"gemms": 0, "mismatch": 0}


def tpu_igemm(xq, wq):                            # xq [M,K] int8, wq [N,K] int8
    c = sim.gemm(xq, np.ascontiguousarray(wq.T))
    ref = quant.igemm_reference(xq, wq)
    checked["gemms"] += 1
    if not np.array_equal(c, ref):
        checked["mismatch"] += 1
    return c


def run(model, tokens, ndecode):
    cache = model.new_cache()
    logits = model.forward(tokens, cache, 0)
    out = [int(np.argmax(logits[-1]))]
    for i in range(ndecode - 1):
        logits = model.forward(np.array([out[-1]]), cache, len(tokens) + i)
        out.append(int(np.argmax(logits[-1])))
    return out


qm = quant.QuantLlama(D, igemm=tpu_igemm); qm.w = wsm
t0 = time.time()
cache = qm.new_cache()
logits = qm.forward(ids, cache, 0)
pre_calls = len(sim.calls); pre_cycles = sim.cycles; pre_macs = sim.macs
gen = [int(np.argmax(logits[-1]))]
for i in range(a.decode - 1):
    logits = qm.forward(np.array([gen[-1]]), cache, len(ids) + i)
    gen.append(int(np.argmax(logits[-1])))
sim.close()
wall = time.time() - t0
dec_cycles = sim.cycles - pre_cycles; dec_macs = sim.macs - pre_macs
ref_model = quant.QuantLlama(D); ref_model.w = wsm
gen_ref = run(ref_model, ids, a.decode)
fp = llm.Llama(D)
gen_fp = run(fp, ids, a.decode)
macs_per_cycle = a.n * a.n
res = {
    "version": a.version, "N": a.n, "mmax": a.mmax, "bw_beats_per_cycle": bwn / bwd, "mem_latency": a.lat, "prefill_tokens": len(ids), "decode_tokens": a.decode,
    "gemms_checked": checked["gemms"], "gemms_mismatching": checked["mismatch"], "tpu_tokens_equal_numpy_int_reference": gen == gen_ref,
    "greedy_agreement_with_float32": float(np.mean([x == y for x, y in zip(gen, gen_fp)])),
    "prefill": {"cycles": pre_cycles, "macs": pre_macs, "utilization": pre_macs / (pre_cycles * macs_per_cycle)},
    "decode": {"cycles": dec_cycles, "macs": dec_macs, "utilization": dec_macs / (dec_cycles * macs_per_cycle), "cycles_per_token": dec_cycles / max(1, a.decode - 1)},
    "wall_s": wall,
}
print(json.dumps(res, indent=1))
if a.out:
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(res, indent=1))
