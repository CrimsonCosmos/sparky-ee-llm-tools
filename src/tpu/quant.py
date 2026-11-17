"""W8A8 quantisation of the Llama forward pass. Weights: symmetric int8, one scale per output channel. Activations: symmetric int8, one dynamic scale per row (token). Every matrix product becomes
`igemm(Xq [M,K] int8, Wq [N,K] int8) -> int32 [M,N]` followed by a float rescale on the host; `igemm` is the single point the TPU replaces. Attention products use the same scheme: keys/values are quantised
per cache row/column, scores and probabilities per row."""
from __future__ import annotations

import numpy as np

from .llm import Llama

QMAX = 127


def quant_rows(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-row symmetric int8: returns (int8 [M,K], float32 scale [M])."""
    s = np.abs(x).max(axis=-1) / QMAX
    s = np.where(s == 0, 1.0, s).astype(np.float32)
    q = np.clip(np.rint(x / s[..., None]), -QMAX, QMAX).astype(np.int8)
    return q, s


def igemm_reference(xq: np.ndarray, wq: np.ndarray) -> np.ndarray:
    """int8 x int8 -> int32 with exact integer arithmetic (the specification every backend must match bit for bit)."""
    return (xq.astype(np.int32) @ wq.astype(np.int32).T)


class QuantLlama(Llama):
    def __init__(self, model_dir, igemm=igemm_reference):
        super().__init__(model_dir)
        self.igemm = igemm
        self._wq: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.log: list[tuple[str, int, int, int]] = []          # (name, M, K, N) of every GEMM issued

    def qweight(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        if name not in self._wq:
            self._wq[name] = quant_rows(self.w[name])         # per output channel = per row of [out, in]
        return self._wq[name]

    def linear(self, x, name):
        wq, sw = self.qweight(name)
        xq, sx = quant_rows(x)
        self.log.append((name, x.shape[0], x.shape[1], wq.shape[0]))
        return (self.igemm(xq, wq).astype(np.float32) * sx[:, None]) * sw[None, :]

    def attn_qk(self, q, k):                                   # [T,hd] x [S,hd]^T: K rows are the "weights" (N = S)
        qq, sq = quant_rows(q)
        kq, sk = quant_rows(k)
        self.log.append(("qk", q.shape[0], q.shape[1], k.shape[0]))
        return (self.igemm(qq, kq).astype(np.float32) * sq[:, None]) * sk[None, :]

    def attn_pv(self, p, v):                                   # [T,S] x [S,hd]: contraction over S, so V^T [hd,S] is the weight; scale per hd column
        pq, sp = quant_rows(p)
        sv = np.abs(v).max(axis=0) / QMAX
        sv = np.where(sv == 0, 1.0, sv).astype(np.float32)
        vq = np.clip(np.rint(v / sv[None, :]), -QMAX, QMAX).astype(np.int8)
        self.log.append(("pv", p.shape[0], p.shape[1], v.shape[1]))
        return (self.igemm(pq, np.ascontiguousarray(vq.T)).astype(np.float32) * sp[:, None]) * sv[None, :]


def smooth_weights(model: Llama, calib_ids: np.ndarray, alpha: float = 0.5, window: int = 256) -> dict[str, np.ndarray]:
    """SmoothQuant (Xiao et al.): move activation outlier magnitude into the weights with per-input-channel factors s_j = max|x_j|^alpha / max|W_:j|^(1-alpha), folded OFFLINE into existing parameters so the
    network function is unchanged in exact arithmetic and the hardware needs nothing new: the factor of q/k/v is folded into input_layernorm, of gate/up into post_attention_layernorm, of down_proj into up_proj's
    output rows. Not applied to o_proj (its input channels are tied across grouped heads, so no exact fold) or to the tied lm_head. Activation statistics come from float32 runs on `calib_ids` (a text DISJOINT
    from the evaluation text). Returns a modified copy of the weight dict."""
    c = model.cfg
    stats: dict[str, np.ndarray] = {}

    class Rec(Llama):
        pass
    rec = Llama.__new__(Llama)
    rec.__dict__.update(model.__dict__)
    orig = model.linear

    def hook(x, name):
        key = name
        stats[key] = np.maximum(stats.get(key, 0.0), np.abs(x).max(axis=0))
        return orig(x, name)
    rec.linear = hook
    for s in range(0, len(calib_ids) - 1, window):
        rec.forward(calib_ids[s:s + window])
    w = {k: v.copy() for k, v in model.w.items()}
    for L in range(c.layers):
        p = f"model.layers.{L}."

        def factor(act_max: np.ndarray, mats: list[np.ndarray]) -> np.ndarray:
            wmax = np.maximum.reduce([np.abs(m_).max(axis=0) for m_ in mats])
            s = (np.maximum(act_max, 1e-5) ** alpha) / (np.maximum(wmax, 1e-5) ** (1 - alpha))
            return np.maximum(s, 1e-5).astype(np.float32)
        qkv = [w[p + f"self_attn.{n}_proj.weight"] for n in ("q", "k", "v")]
        s = factor(stats[p + "self_attn.q_proj.weight"], qkv)
        w[p + "input_layernorm.weight"] = w[p + "input_layernorm.weight"] / s
        for n in ("q", "k", "v"):
            w[p + f"self_attn.{n}_proj.weight"] = w[p + f"self_attn.{n}_proj.weight"] * s[None, :]
        gu = [w[p + "mlp.gate_proj.weight"], w[p + "mlp.up_proj.weight"]]
        s = factor(stats[p + "mlp.gate_proj.weight"], gu)
        w[p + "post_attention_layernorm.weight"] = w[p + "post_attention_layernorm.weight"] / s
        for n in ("gate", "up"):
            w[p + f"mlp.{n}_proj.weight"] = w[p + f"mlp.{n}_proj.weight"] * s[None, :]
        s = factor(stats[p + "mlp.down_proj.weight"], [w[p + "mlp.down_proj.weight"]])
        w[p + "mlp.up_proj.weight"] = w[p + "mlp.up_proj.weight"] / s[:, None]      # u' = u / s  (rows of up_proj)
        w[p + "mlp.down_proj.weight"] = w[p + "mlp.down_proj.weight"] * s[None, :]
    return w
