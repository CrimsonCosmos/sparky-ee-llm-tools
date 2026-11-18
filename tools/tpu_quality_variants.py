"""Quantisation variants on top of W8A8+SmoothQuant (alpha 0.5): activation-scale quantile clipping and MSE-optimal weight scales. All stay inside the int8 array's contract (only scales change; the integer GEMM is untouched).
usage: python tools/tpu_quality_variants.py <n_tokens>"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import llm, quant

D = "/home/user/data/smollm2"
n = int(sys.argv[1])
tk = llm.tokenizer(D)
test = np.array(tk.encode(open("/home/user/data/wikitext2/test.txt").read()[:max(60000, n * 30)]).ids)[:n]
wsm = quant.load_smooth(D, 0.5)
pf = llm.perplexity(llm.Llama(D), test, 256)
print(f"float32 ppl {pf:.3f} ({len(test)} tokens)", flush=True)
for name, clip, mse in (("baseline", None, False), ("weights MSE scale", None, True), ("act clip q=0.9999", 0.9999, False), ("act clip q=0.999", 0.999, False), ("both: w MSE + act q=0.9999", 0.9999, True)):
    q = quant.QuantLlama(D); q.w = dict(wsm); q.act_clip = clip; q.w_mse = mse
    p = llm.perplexity(q, test, 256)
    print(f"{name:28s} ppl {p:.3f} ({100 * (p / pf - 1):+.2f} %)", flush=True)
