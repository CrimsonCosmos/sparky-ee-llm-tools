"""Quality test for 4-bit weight compression (RTN, symmetric, per-group scales along K) on top of W8A8+SmoothQuant. Weights are fake-quantised (quantise->dequantise) then run through the normal W8A8 path.
usage: python tools/tpu_w4.py <n_tokens> <group sizes...>   (reports perplexity vs float32 and weight bytes/param incl. 16-bit scales)"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import llm, quant

D = "/home/user/data/smollm2"
n = int(sys.argv[1]); groups = [int(g) for g in sys.argv[2:]]
tk = llm.tokenizer(D)
test = np.array(tk.encode(open("/home/user/data/wikitext2/test.txt").read()[:max(60000, n * 30)]).ids)[:n]
wsm = dict(np.load(Path(D) / "smooth_a0.5.npz"))
pf = llm.perplexity(llm.Llama(D), test, 256)
print(f"float32 ppl {pf:.3f} ({len(test)} tokens)")
LIN = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


def fq4(w, g):
    """w [N,K] -> symmetric int4 (-7..7) per (row, K-group), dequantised."""
    N, K = w.shape
    pad = (-K) % g
    wp = np.pad(w, ((0, 0), (0, pad))).reshape(N, -1, g)
    s = np.abs(wp).max(-1, keepdims=True) / 7.0
    s[s == 0] = 1.0
    return (np.clip(np.round(wp / s), -7, 7) * s).reshape(N, -1)[:, :K]


for g in groups:
    q = quant.QuantLlama(D)
    q.w = dict(wsm)
    for k in list(q.w):
        if any(t in k for t in LIN) and k.endswith("weight"):
            q.w[k] = fq4(np.asarray(q.w[k], np.float32), g)
    # tied embedding / lm_head
    q.w["model.embed_tokens.weight"] = q.w["model.embed_tokens.weight"]
    p = llm.perplexity(q, test, 256)
    print(f"W4 g{g} + A8: ppl {p:.3f} ({100 * (p / pf - 1):+.2f} %), weight bytes/param {0.5 + 2.0 / g:.3f} (int8: 1.0)", flush=True)
