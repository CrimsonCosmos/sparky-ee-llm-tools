"""Float32 numpy Llama-family forward pass (SmolLM2-135M shapes come from config.json) with every matrix product routed through overridable hooks, so the same model can run its GEMMs on the TPU.

Layout: weights are stored [out_features, in_features] as in the checkpoint; `linear(x, name)` computes x @ W.T. Attention products go through `attn_qk` / `attn_pv`. Everything else (RMSNorm, RoPE, softmax, SiLU,
residual adds) is host float32 arithmetic."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def bf16_to_f32(raw: np.ndarray) -> np.ndarray:
    """bfloat16 stored as uint16 -> float32 (bf16 is the top half of a float32)."""
    return (raw.astype(np.uint32) << 16).view(np.float32)


def load_safetensors(path: Path) -> dict[str, np.ndarray]:
    """Minimal safetensors reader (header JSON + raw buffers); handles BF16/F32/F16 without torch."""
    buf = np.memmap(path, dtype=np.uint8, mode="r")
    n = int.from_bytes(bytes(buf[:8]), "little")
    header = json.loads(bytes(buf[8:8 + n]))
    out = {}
    for name, meta in header.items():
        if name == "__metadata__":
            continue
        s, e = meta["data_offsets"]
        raw = buf[8 + n + s:8 + n + e]
        if meta["dtype"] == "BF16":
            arr = bf16_to_f32(np.frombuffer(raw, dtype=np.uint16))
        elif meta["dtype"] == "F32":
            arr = np.frombuffer(raw, dtype=np.float32)
        elif meta["dtype"] == "F16":
            arr = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
        else:
            raise ValueError(f"unsupported dtype {meta['dtype']}")
        out[name] = np.array(arr).reshape(meta["shape"])
    return out


@dataclass
class Config:
    hidden: int
    inter: int
    layers: int
    heads: int
    kv_heads: int
    vocab: int
    rope_theta: float
    eps: float
    tie: bool

    @property
    def head_dim(self) -> int:
        return self.hidden // self.heads

    @staticmethod
    def load(path: Path) -> "Config":
        c = json.loads(Path(path).read_text())
        return Config(c["hidden_size"], c["intermediate_size"], c["num_hidden_layers"], c["num_attention_heads"], c["num_key_value_heads"], c["vocab_size"], float(c["rope_theta"]),
                      float(c["rms_norm_eps"]), bool(c.get("tie_word_embeddings", False)))


def rmsnorm(x: np.ndarray, w: np.ndarray, eps: float) -> np.ndarray:
    return (x / np.sqrt((x * x).mean(-1, keepdims=True) + eps)) * w


def silu(x: np.ndarray) -> np.ndarray:
    return x / (1.0 + np.exp(-x))


def softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max(-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(-1, keepdims=True)


class Llama:
    def __init__(self, model_dir: str | Path):
        d = Path(model_dir)
        self.cfg = Config.load(d / "config.json")
        self.w = load_safetensors(d / "model.safetensors")
        c = self.cfg
        inv = 1.0 / (c.rope_theta ** (np.arange(0, c.head_dim, 2, dtype=np.float64) / c.head_dim))
        self.inv_freq = inv.astype(np.float32)

    # --- hooks the TPU backend overrides -------------------------------------------------------------------------------------------------------------
    def weight(self, name: str) -> np.ndarray:
        return self.w[name]

    def linear(self, x: np.ndarray, name: str) -> np.ndarray:
        return x @ self.weight(name).T

    def attn_qk(self, q: np.ndarray, k: np.ndarray) -> np.ndarray:        # q [T, hd], k [S, hd] -> [T, S]
        return q @ k.T

    def attn_pv(self, p: np.ndarray, v: np.ndarray) -> np.ndarray:        # p [T, S], v [S, hd] -> [T, hd]
        return p @ v

    # --- model ---------------------------------------------------------------------------------------------------------------------------------------
    def rope(self, x: np.ndarray, pos: np.ndarray) -> np.ndarray:         # x [T, H, hd]; non-interleaved (rotate_half) as in HF Llama
        ang = pos[:, None].astype(np.float32) * self.inv_freq[None, :]
        cos, sin = np.cos(ang)[:, None, :], np.sin(ang)[:, None, :]
        h = x.shape[-1] // 2
        x1, x2 = x[..., :h], x[..., h:]
        return np.concatenate([x1 * cos - x2 * sin, x2 * cos + x1 * sin], axis=-1)

    def new_cache(self):
        return [{"k": np.zeros((0, self.cfg.kv_heads, self.cfg.head_dim), np.float32), "v": np.zeros((0, self.cfg.kv_heads, self.cfg.head_dim), np.float32)} for _ in range(self.cfg.layers)]

    def forward(self, tokens: np.ndarray, cache=None, start: int = 0) -> np.ndarray:
        """Logits for `tokens` [T] appended after `start` cached positions. Returns [T, vocab]."""
        c = self.cfg
        T = len(tokens)
        pos = np.arange(start, start + T)
        x = self.w["model.embed_tokens.weight"][tokens].astype(np.float32)
        rep = c.heads // c.kv_heads
        for L in range(c.layers):
            p = f"model.layers.{L}."
            h = rmsnorm(x, self.w[p + "input_layernorm.weight"], c.eps)
            q = self.linear(h, p + "self_attn.q_proj.weight").reshape(T, c.heads, c.head_dim)
            k = self.linear(h, p + "self_attn.k_proj.weight").reshape(T, c.kv_heads, c.head_dim)
            v = self.linear(h, p + "self_attn.v_proj.weight").reshape(T, c.kv_heads, c.head_dim)
            q, k = self.rope(q, pos), self.rope(k, pos)
            if cache is not None:
                cache[L]["k"] = np.concatenate([cache[L]["k"], k]); cache[L]["v"] = np.concatenate([cache[L]["v"], v])
                K, V = cache[L]["k"], cache[L]["v"]
            else:
                K, V = k, v
            S = K.shape[0]
            mask = (np.arange(S)[None, :] > (pos[:, None]))                # key index > query position -> masked
            out = np.zeros((T, c.heads, c.head_dim), np.float32)
            for hh in range(c.heads):
                g = hh // rep
                sc = self.attn_qk(q[:, hh], K[:, g]) / np.sqrt(c.head_dim)
                sc = np.where(mask, -1e30, sc)
                out[:, hh] = self.attn_pv(softmax(sc).astype(np.float32), V[:, g])
            x = x + self.linear(out.reshape(T, c.hidden), p + "self_attn.o_proj.weight")
            h = rmsnorm(x, self.w[p + "post_attention_layernorm.weight"], c.eps)
            g = self.linear(h, p + "mlp.gate_proj.weight")
            u = self.linear(h, p + "mlp.up_proj.weight")
            x = x + self.linear(silu(g) * u, p + "mlp.down_proj.weight")
        x = rmsnorm(x, self.w["model.norm.weight"], c.eps)
        return self.linear(x, "lm_head.weight" if "lm_head.weight" in self.w else "model.embed_tokens.weight")


def tokenizer(model_dir: str | Path):
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json"))


def log_softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max(-1, keepdims=True)
    return x - np.log(np.exp(x).sum(-1, keepdims=True))


def perplexity(model: Llama, ids: np.ndarray, window: int = 256) -> float:
    """exp(mean negative log-likelihood) of next-token prediction over `ids`, in non-overlapping windows."""
    nll, n = 0.0, 0
    for s in range(0, len(ids) - 1, window):
        chunk = ids[s:s + window + 1]
        if len(chunk) < 2:
            break
        lp = log_softmax(model.forward(chunk[:-1]))
        nll -= lp[np.arange(len(chunk) - 1), chunk[1:]].sum()
        n += len(chunk) - 1
    return float(np.exp(nll / n))
