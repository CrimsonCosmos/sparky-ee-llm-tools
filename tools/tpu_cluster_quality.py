"""Perplexity of the tensor-parallel model vs the unsharded model (same K-groups) vs float32, numpy integer backends (identical to the RTL, which is bit-exact).
usage: [TPU_MODEL_DIR=...] python tools/tpu_cluster_quality.py <n_tokens> <tp>"""
import os, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import cluster, llm, quant

D = os.environ.get("TPU_MODEL_DIR", "/home/user/data/smollm2")
n, tp = int(sys.argv[1]), int(sys.argv[2])
tk = llm.tokenizer(D)
test = np.array(tk.encode(open("/home/user/data/wikitext2/test.txt").read()[:max(60000, n * 30)]).ids)[:n]
wsm = quant.load_smooth(D, quant.default_alpha(160))
pf = llm.perplexity(llm.Llama(D), test, 256)
print(f"float32 ppl {pf:.4f} ({len(test)} tokens)", flush=True)
un = cluster.ClusterModel(D, 1, [cluster.RefBackend()], wsm, group_tp=tp)
pu = llm.perplexity(un, test, 256)
print(f"unsharded (groups of the tp={tp} layout) ppl {pu:.4f} ({100 * (pu / pf - 1):+.2f} %)", flush=True)
sh = cluster.ClusterModel(D, tp, [cluster.RefBackend() for _ in range(tp)], wsm)
ps = llm.perplexity(sh, test, 256)
print(f"tensor-parallel tp={tp} ppl {ps:.4f} ({100 * (ps / pf - 1):+.2f} %); sharded vs unsharded {100 * (ps / pu - 1):+.3f} %", flush=True)
