"""Choose the SmoothQuant alpha on HELD-OUT validation text (disjoint from the 1537 calibration tokens), then report the chosen value's test-set perplexity.
usage: python tools/tpu_alpha_select.py <n_tokens> <alphas...>"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import llm, quant

D = "/home/user/data/smollm2"
n = int(sys.argv[1]); alphas = [float(a) for a in sys.argv[2:]]
tk = llm.tokenizer(D)
val_text = open("/home/user/data/wikitext2/validation.txt").read()
calib = np.array(tk.encode(val_text[:60000]).ids)[:1537]
held = np.array(tk.encode(val_text[200000:200000 + max(60000, n * 30)]).ids)[:n]          # far from the calibration text
test = np.array(tk.encode(open("/home/user/data/wikitext2/test.txt").read()[:max(60000, n * 30)]).ids)[:n]
base = llm.Llama(D)
pf_h, pf_t = llm.perplexity(base, held, 256), llm.perplexity(base, test, 256)
print(f"float32: held-out val {pf_h:.3f}, test {pf_t:.3f} ({len(held)}/{len(test)} tokens)", flush=True)
for a in alphas:
    q = quant.QuantLlama(D); q.w = quant.smooth_weights(base, calib, a)
    ph, pt = llm.perplexity(q, held, 256), llm.perplexity(q, test, 256)
    print(f"alpha {a}: held-out val {ph:.3f} ({100 * (ph / pf_h - 1):+.2f} %)   test {pt:.3f} ({100 * (pt / pf_t - 1):+.2f} %)", flush=True)
