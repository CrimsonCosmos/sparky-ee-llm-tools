"""Perplexity of float32 vs W8A8 (plain and SmoothQuant-folded) SmolLM2-135M on WikiText-2 test text; calibration text is the validation split. usage: python tools/tpu_quality.py [n_test_tokens] [alphas...]"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import llm, quant

D = "/home/user/data/smollm2"
n = int(sys.argv[1]) if len(sys.argv) > 1 else 2049
alphas = [float(a) for a in sys.argv[2:]] or [0.5]
m, tk = llm.Llama(D), llm.tokenizer(D)
test = np.array(tk.encode(open("/home/user/data/wikitext2/test.txt").read()[:max(60000, n * 30)]).ids)[:n]
calib = np.array(tk.encode(open("/home/user/data/wikitext2/validation.txt").read()[:60000]).ids)[:1537]
pf = llm.perplexity(m, test, 256)
print(f"test tokens {len(test)}; float32 ppl {pf:.3f}")
q = quant.QuantLlama(D)
pq = llm.perplexity(q, test, 256)
print(f"W8A8 plain: ppl {pq:.3f} ({100 * (pq / pf - 1):+.2f} %)")
for a in alphas:
    q = quant.QuantLlama(D)
    q.w = quant.smooth_weights(m, calib, a)
    ps = llm.perplexity(q, test, 256)
    print(f"W8A8 + SmoothQuant alpha {a}: ppl {ps:.3f} ({100 * (ps / pf - 1):+.2f} %)", flush=True)
