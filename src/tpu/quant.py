"""W8A8 quantisation of the Llama forward pass. Weights: symmetric int8, one scale per output channel. Activations: symmetric int8, one dynamic scale per row (token). Every matrix product becomes
`igemm(Xq [M,K] int8, Wq [N,K] int8) -> int32 [M,N]` followed by a float rescale on the host; `igemm` is the single point the TPU replaces. Attention products use the same scheme: keys/values are quantised
per cache row/column, scores and probabilities per row."""
from __future__ import annotations

import numpy as np

from .llm import Llama

QMAX = 127


def quant_rows(x: np.ndarray, clip_q: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Per-row symmetric int8: returns (int8 [M,K], float32 scale [M]). `clip_q` (optional): the scale covers that quantile of |x| instead of the maximum (values beyond it saturate)."""
    s = (np.abs(x).max(axis=-1) if clip_q is None else np.quantile(np.abs(x), clip_q, axis=-1)) / QMAX
    s = np.where(s == 0, 1.0, s).astype(np.float32)
    q = np.clip(np.rint(x / s[..., None]), -QMAX, QMAX).astype(np.int8)
    return q, s


def quant_rows_mse(w: np.ndarray, ratios=(1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7)) -> tuple[np.ndarray, np.ndarray]:
    """Per-row symmetric int8 with the clipping ratio (of absmax) that minimises the row's squared quantisation error."""
    amax = np.abs(w).max(axis=-1)
    best_err = np.full(w.shape[0], np.inf); best_s = None
    for r in ratios:
        s = np.where(amax == 0, 1.0, amax * r / QMAX).astype(np.float32)
        err = ((np.clip(np.rint(w / s[:, None]), -QMAX, QMAX) * s[:, None] - w) ** 2).sum(axis=-1)
        if best_s is None:
            best_s = s.copy()
        m = err < best_err
        best_err = np.where(m, err, best_err); best_s = np.where(m, s, best_s)
    q = np.clip(np.rint(w / best_s[:, None]), -QMAX, QMAX).astype(np.int8)
    return q, best_s.astype(np.float32)


def igemm_reference(xq: np.ndarray, wq: np.ndarray) -> np.ndarray:
    """int8 x int8 -> int32 with exact integer arithmetic (the specification every backend must match bit for bit)."""
    return (xq.astype(np.int32) @ wq.astype(np.int32).T)


class QuantLlama(Llama):
    def __init__(self, model_dir, igemm=igemm_reference):
        super().__init__(model_dir)
        self.igemm = igemm
        self._wq: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.act_clip: float | None = None                      # activation scale quantile (None = absmax)
        self.w_mse = False                                      # per-output-channel weight scale chosen to minimise squared error instead of absmax
        self.log: list[tuple[str, int, int, int]] = []          # (name, M, K, N) of every GEMM issued

    def qweight(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        if name not in self._wq:
            self._wq[name] = quant_rows_mse(self.w[name]) if self.w_mse else quant_rows(self.w[name])         # per output channel = per row of [out, in]
        return self._wq[name]

    def linear(self, x, name):
        wq, sw = self.qweight(name)
        xq, sx = quant_rows(x, self.act_clip)
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


DEFAULT_ALPHA = 0.6        # chosen on HELD-OUT validation text (tools/tpu_alpha_select.py: 0.4 +4.51 %, 0.5 +2.00 %, 0.6 +1.17 %, 0.7 +2.63 %); test-set result reported separately


def load_smooth(model_dir, alpha: float = DEFAULT_ALPHA, calib_text_file: str = "/home/user/data/wikitext2/validation.txt") -> dict:
    """SmoothQuant-folded weight dict for `alpha`, cached next to the model (calibration: first 1537 tokens of the validation split)."""
    from pathlib import Path
    from . import llm
    f = Path(model_dir) / f"smooth_a{alpha}.npz"
    if f.exists():
        return dict(np.load(f))
    tk = llm.tokenizer(model_dir)
    calib = np.array(tk.encode(open(calib_text_file).read()[:60000]).ids)[:1537]
    w = smooth_weights(llm.Llama(model_dir), calib, alpha)
    np.savez(f, **w)
    return w
