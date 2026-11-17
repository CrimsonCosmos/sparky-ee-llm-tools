"""C2: perplexity of SmolLM2-135M (W8A8 + SmoothQuant) with every matrix product executed on the TPU RTL (bit-exact checked), vs the float32 reference on the same tokens.
usage: python tools/tpu_ppl.py <version> <n_tokens> [--out file.json]"""
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import llm, quant, rtl

D = "/home/user/data/smollm2"
version, ntok = sys.argv[1], int(sys.argv[2])
out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
tk = llm.tokenizer(D)
test = np.array(tk.encode(open("/home/user/data/wikitext2/test.txt").read()[:max(60000, ntok * 30)]).ids)[:ntok]
wsm = dict(np.load(Path(D) / "smooth_a0.5.npz"))
exe = rtl.build_harness(version, 16, 64, 96, Path(f"/home/user/data/tpu_build/{version}_n16"))
sim = rtl.Sim(exe, 64, n=16)
bad = [0, 0]


def tpu_igemm(xq, wq):
    c = sim.gemm(xq, np.ascontiguousarray(wq.T)); bad[0] += 1
    bad[1] += int(not np.array_equal(c, quant.igemm_reference(xq, wq)))
    return c


q = quant.QuantLlama(D, igemm=tpu_igemm); q.w = wsm
p_tpu = llm.perplexity(q, test, 256)
sim.close()
p_fp = llm.perplexity(llm.Llama(D), test, 256)
res = {"version": version, "tokens": int(len(test)), "ppl_float32": p_fp, "ppl_tpu_rtl_w8a8_smoothquant": p_tpu, "degradation_pct": 100 * (p_tpu / p_fp - 1), "gemms_checked": bad[0], "gemms_mismatching": bad[1],
       "cycles": sim.cycles, "macs": sim.macs, "utilization": sim.macs / (sim.cycles * 256)}
print(json.dumps(res, indent=1))
if out:
    Path(out).write_text(json.dumps(res, indent=1))
