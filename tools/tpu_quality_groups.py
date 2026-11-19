"""Does K-group quantisation (separate activation and weight scales per group of K columns; the array emits one int32 partial sum per group, the host combines them in float) improve W8A8+SmoothQuant quality?
usage: python tools/tpu_quality_groups.py <n_tokens> <group sizes (columns, multiples of 16) ...>"""
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import llm, quant

import os
D = os.environ.get("TPU_MODEL_DIR", "/home/user/data/smollm2")
n = int(sys.argv[1]); groups = [int(g) for g in sys.argv[2:]]
tk = llm.tokenizer(D)
test = np.array(tk.encode(open("/home/user/data/wikitext2/test.txt").read()[:max(60000, n * 30)]).ids)[:n]
wsm = quant.load_smooth(D, quant.default_alpha(288))


class GroupQ(quant.QuantLlama):
    group = 0

    def qweight(self, name):
        if name not in self._wq:
            w = self.w[name]; K = w.shape[1]; g = self.group or K
            qs, ss = [], []
            for k0 in range(0, K, g):
                q, s = quant.quant_rows(w[:, k0:k0 + g]); qs.append(q); ss.append(s)
            self._wq[name] = (qs, ss)
        return self._wq[name]

    def linear(self, x, name):
        qs, ss = self.qweight(name)
        K = x.shape[1]; g = self.group or K
        out = 0.0
        for gi, k0 in enumerate(range(0, K, g)):
            xq, sx = quant.quant_rows(x[:, k0:k0 + g])
            out = out + (self.igemm(xq, qs[gi]).astype(np.float32) * sx[:, None]) * ss[gi][None, :]
        return out


pf = llm.perplexity(llm.Llama(D), test, 256)
print(f"float32 ppl {pf:.3f} ({len(test)} tokens)", flush=True)
for g in groups:
    q = GroupQ(D); q.w = dict(wsm); q.group = g; q.group_attn = False
    p = llm.perplexity(q, test, 256)
    print(f"K-group {g or 'full':>5}: ppl {p:.3f} ({100 * (p / pf - 1):+.2f} %)  partial-sum streams per GEMM = K/{g or 'K'}", flush=True)
