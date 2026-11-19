"""Run a model tensor-parallel on C TPU chips (each chip = its own RTL simulation), check it against an unsharded reference and report scale-out cycles.
usage: [TPU_MODEL_DIR=...] python tools/tpu_cluster.py --tp 3 [--cores 1] [--prefill 16] [--decode 4] [--link 16] [--out results.json]
Checks: (1) every GEMM on every chip is bit-exact against numpy; (2) the RTL-sharded logits equal the numpy-sharded logits EXACTLY; (3) sharded vs unsharded (same groups) logits differ only by float summation order
(reported as max |delta|) and the generated tokens agree; (4) cycles: single chip (all GEMMs on one chip with the same number of arrays) vs C chips + ring collectives."""
import argparse, json, os, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tpu import cluster, llm, quant, rtl

D = os.environ.get("TPU_MODEL_DIR", "/home/user/data/smollm2")
ap = argparse.ArgumentParser()
ap.add_argument("--tp", type=int, required=True); ap.add_argument("--cores", type=int, default=1); ap.add_argument("--prefill", type=int, default=16); ap.add_argument("--decode", type=int, default=4)
ap.add_argument("--link", type=int, default=16, help="inter-chip link width in bytes per core cycle"); ap.add_argument("--version", default="v10"); ap.add_argument("--reduce", choices=["gather", "allreduce"], default="gather")
ap.add_argument("--rtl-ring", action="store_true", help="with --reduce allreduce: run every all-reduce on the RTL ring (float32 adders) instead of the numpy emulation of its order"); ap.add_argument("--out")
a = ap.parse_args()

tk = llm.tokenizer(D)
prompt = np.array(tk.encode(open("/home/user/data/wikitext2/test.txt").read()[:20000]).ids)[:a.prefill]
wsm = quant.load_smooth(D, quant.default_alpha(160))
cfg = llm.Config.load(Path(D) / "config.json")
ktmax = -(-max(cfg.hidden, cfg.inter) // 16) + 8
wd = Path(f"/home/user/data/tpu_build/{a.version}_n16")
exe = rtl.build_harness(a.version, 16, 64, ktmax, wd) if a.cores == 1 else rtl.build_multi(a.version, 16, 64, ktmax, a.cores, Path(f"{wd}_k{a.cores}"))


def rtl_backends(n):
    return [cluster.RtlBackend(rtl.Sim(exe, 64, n=16)) for _ in range(n)]


ring_fn = None
if a.rtl_ring:
    assert a.reduce == "allreduce"
    ring_exe = rtl.build_ring_ar(a.tp, 16, 4096, Path(f"/home/user/data/tpu_build/ring_ar_c{a.tp}"))
    ring_fn = lambda vecs: rtl.ring_allreduce(ring_exe, vecs, 16)[0]

t0 = time.time()
res = {"reduce": a.reduce, "ring_on_rtl": a.rtl_ring, "model": Path(D).name, "tp": a.tp, "cores_per_chip": a.cores, "link_bytes_per_cycle": a.link, "prefill": len(prompt), "decode_tokens": a.decode, "version": a.version}
# reference: unsharded (tp=1) with the groups of the tp-sharded layout, numpy integer backend
ref = cluster.ClusterModel(D, 1, [cluster.RefBackend()], wsm, group_tp=a.tp, link_bytes_per_cycle=a.link)
tok_ref, lg_ref, _, _ = ref.generate(prompt, a.decode)
# numpy-sharded (exact reference of the sharded computation)
nsh = cluster.ClusterModel(D, a.tp, [cluster.RefBackend() for _ in range(a.tp)], wsm, link_bytes_per_cycle=a.link, reduce=a.reduce)
tok_ns, lg_ns, _, _ = nsh.generate(prompt, a.decode, forced=tok_ref)
# RTL single chip (baseline cycles)
b1 = rtl_backends(1)
one = cluster.ClusterModel(D, 1, b1, wsm, group_tp=a.tp, link_bytes_per_cycle=a.link)
tok1, lg1, pre1, dec1 = one.generate(prompt, a.decode, forced=tok_ref)
# RTL cluster
bs = rtl_backends(a.tp)
cl = cluster.ClusterModel(D, a.tp, bs, wsm, link_bytes_per_cycle=a.link, reduce=a.reduce, ring_fn=ring_fn)
tokc, lgc, prec, decc = cl.generate(prompt, a.decode, forced=tok_ref)
for b in b1 + bs:
    b.sim.close()
res.update({
    "groups": {"hidden": cl.g_h, "o_proj_shard": cl.g_o, "down_proj_shard": cl.g_d},
    "gemms_checked_cluster": sum(b.calls for b in bs), "gemms_mismatching_cluster": sum(b.mismatches for b in bs),
    "gemms_checked_single": b1[0].calls, "gemms_mismatching_single": b1[0].mismatches,
    "rtl_cluster_logits_equal_numpy_sharded_exactly": bool(np.array_equal(lgc, lg_ns)), "rtl_cluster_tokens_equal_numpy_sharded": tokc == tok_ns,
    "sharded_vs_unsharded_max_abs_logit_diff": float(np.abs(lgc - lg_ref).max()), "sharded_vs_unsharded_logit_scale": float(np.abs(lg_ref).max()),
    "comparison": "teacher forced: every model is fed the reference tokens, so logits are compared on identical contexts", "reference_tokens": tok_ref,
    "sharded_argmax_equals_unsharded_argmax_per_step": [int(x == y) for x, y in zip(tokc, tok_ref)], "reference_top1_top2_logit_margin_per_step": [float(np.sort(l)[-1] - np.sort(l)[-2]) for l in lg_ref],
    "sharded_tokens_equal_unsharded": tokc == tok_ref, "tokens": tokc,
    "single_chip": {"prefill_cycles": pre1["cycles"], "decode_cycles": dec1["cycles"], "decode_cycles_per_token": dec1["cycles"] / max(1, a.decode - 1)},
    "cluster": {"prefill_cycles": prec["cycles"], "prefill_collective_cycles": prec["collective"], "decode_cycles": decc["cycles"], "decode_collective_cycles": decc["collective"],
                "decode_cycles_per_token": decc["cycles"] / max(1, a.decode - 1)},
    "decode_speedup": dec1["cycles"] / decc["cycles"], "decode_scaling_efficiency": dec1["cycles"] / decc["cycles"] / a.tp,
    "prefill_speedup": pre1["cycles"] / prec["cycles"], "prefill_scaling_efficiency": pre1["cycles"] / prec["cycles"] / a.tp,
    "collectives_per_token": cl.collectives // max(1, a.decode), "wall_s": time.time() - t0})
print(json.dumps(res, indent=1))
if a.out:
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(res, indent=1))
