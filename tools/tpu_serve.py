"""End-to-end serving demo: SmolLM2-135M (W8A8 + SmoothQuant) generating text for several prompts at once, every matrix product executed on the TPU RTL (Verilator, K arrays), bit-exact checked
against the integer reference, batched greedy decoding with a per-sequence KV cache. Prints the generated text, cycles per generated token and the throughput at an ASSUMED clock.
usage: python tools/tpu_serve.py <version> [--n 16] [--cores 4] [--new 24] [--clock-mhz 70] [--out results/tpu/serve_demo.json] [prompt ...]"""
import argparse, json, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import llm, quant, rtl

import os
D = os.environ.get("TPU_MODEL_DIR", "/home/user/data/smollm2")     # any Llama-architecture model directory (config.json + safetensors + tokenizer.json)
ap = argparse.ArgumentParser()
ap.add_argument("version"); ap.add_argument("--n", type=int, default=16); ap.add_argument("--cores", type=int, default=4); ap.add_argument("--new", type=int, default=24)
ap.add_argument("--clock-mhz", type=float, default=70.0); ap.add_argument("--out"); ap.add_argument("--group-cols", type=int, default=0); ap.add_argument("prompts", nargs="*")
a = ap.parse_args()
prompts = a.prompts or ["The capital of France is", "Photosynthesis is the process by which", "In 1969, the Apollo 11 mission", "A systolic array is"]
tk = llm.tokenizer(D)
wsm = quant.load_smooth(D, quant.default_alpha(a.group_cols))
wd = Path(f"/home/user/data/tpu_build/{a.version}_n{a.n}")
exe = rtl.build_harness(a.version, a.n, 64, 96, wd) if a.cores == 1 else rtl.build_multi(a.version, a.n, 64, 96, a.cores, Path(f"{wd}_k{a.cores}"))
sim = rtl.Sim(exe, 64, n=a.n)
chk = [0, 0]


def igemm_groups(xq, wq, g):
    c = sim.gemm(xq, np.ascontiguousarray(wq.T), group_tiles=g // a.n); chk[0] += 1
    chk[1] += int(not np.array_equal(c, quant.igemm_groups_reference(xq, wq, g)))
    return c


def igemm(xq, wq):
    c = sim.gemm(xq, np.ascontiguousarray(wq.T)); chk[0] += 1
    chk[1] += int(not np.array_equal(c, quant.igemm_reference(xq, wq)))
    return c


m = quant.QuantLlama(D, igemm=igemm, igemm_groups=igemm_groups); m.w = wsm; m.group_attn = True; m.group_cols = a.group_cols
B = len(prompts)
ids = [np.array(tk.encode(p).ids) for p in prompts]
caches = [m.new_cache() for _ in range(B)]
t0 = time.time()
nxt = []
for b in range(B):
    nxt.append(int(np.argmax(m.forward(ids[b], caches[b], 0)[-1])))
pre_cycles = sim.cycles; pre_abuf = sim.abuf_cycles
gen = [[t] for t in nxt]
for step in range(a.new - 1):
    lg = m.forward_batch(np.array([g[-1] for g in gen]), caches, [len(ids[b]) + step for b in range(B)])
    for b in range(B):
        gen[b].append(int(np.argmax(lg[b])))
dec_cycles = sim.cycles - pre_cycles; dec_abuf = sim.abuf_cycles - pre_abuf
sim.close()
f = a.clock_mhz * 1e6
res = {"version": a.version, "group_cols": a.group_cols, "N": a.n, "cores": a.cores, "sequences": B, "new_tokens_each": a.new, "gemms_checked": chk[0], "gemms_mismatching": chk[1],
       "prefill_cycles": pre_cycles, "decode_cycles": dec_cycles, "decode_abuf_fill_cycles_not_overlapped": dec_abuf, "decode_cycles_per_generated_token_incl_abuf_fill": (dec_cycles + dec_abuf) / (B * (a.new - 1)), "decode_cycles_per_generated_token": dec_cycles / (B * (a.new - 1)),
       "assumed_clock_mhz": a.clock_mhz, "decode_tokens_per_s_at_assumed_clock": B * (a.new - 1) / (dec_cycles / f), "wall_s": time.time() - t0,
       "outputs": [{"prompt": p, "completion": tk.decode(g)} for p, g in zip(prompts, gen)]}
for o in res["outputs"]:
    print(repr(o["prompt"]), "->", repr(o["completion"]))
print({k: v for k, v in res.items() if k != "outputs"})
if a.out:
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(res, indent=1))
