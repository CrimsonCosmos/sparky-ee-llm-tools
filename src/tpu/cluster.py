"""Multi-chip tensor-parallel execution of a Llama-architecture model on TPU chips (scale-out).

Sharding (Megatron style), C = tp chips, every chip has its own GEMM backend (a Verilator simulation of the RTL, or the numpy reference):
  * attention: query heads and KV heads split over chips (each chip keeps the KV cache of its own KV heads); q/k/v projections are column-parallel (one fused GEMM per chip, no communication);
    o_proj is row-parallel: each chip multiplies its local attention output with its K-shard of o_proj and produces a full-width PARTIAL output;
  * MLP: gate/up column-parallel (fused), down_proj row-parallel (K-shard, partial output);
  * lm_head (tied embedding): vocabulary rows split over chips.
Row-parallel partial outputs are combined with an all-gather over the ring interconnect (hardware/tpu/v11/tpu_ring.v: data movement only) followed by an ordered sum on every chip
(rank 0, 1, ..., C-1), so the result is deterministic and identical on all chips. K-group quantisation groups never straddle a shard (group size = largest multiple of the tile that divides the shard).
Cycle accounting: chips run in parallel between collectives, so each synchronisation point costs the SLOWEST chip's cycles since the previous one, plus the RTL-validated ring cost
(perf.ring_allgather_cycles). Host float work (norms, rope, softmax, SiLU, scales) is not counted, as everywhere else in this repo."""
from __future__ import annotations
from pathlib import Path
import numpy as np
from . import llm, perf, quant

TILE = 16


def pick_group(k_local: int, target: int = quant.DEFAULT_GROUP_COLS, tile: int = TILE) -> int:
    """Largest multiple of `tile` that is <= target and divides k_local (so groups never straddle a shard)."""
    g = min(target, k_local) // tile * tile
    while g > tile and k_local % g:
        g -= tile
    return max(g, tile)


class RefBackend:
    """numpy integer reference of one chip's matrix unit."""
    cycles = 0
    mismatches = 0
    calls = 0

    def igemm(self, xq, wq):
        self.calls += 1
        return quant.igemm_reference(xq, wq)

    def igemm_groups(self, xq, wq, g):
        self.calls += 1
        return quant.igemm_groups_reference(xq, wq, g)


class RtlBackend:
    """One chip = one persistent Verilator simulation (single array or tpu_multi); every GEMM is also checked against the numpy reference."""

    def __init__(self, sim):
        self.sim = sim
        self.mismatches = 0
        self.calls = 0

    @property
    def cycles(self) -> int:
        return self.sim.cycles

    def igemm(self, xq, wq):
        c = self.sim.gemm(xq, np.ascontiguousarray(wq.T))
        self.calls += 1
        self.mismatches += int(not np.array_equal(c, quant.igemm_reference(xq, wq)))
        return c

    def igemm_groups(self, xq, wq, g):
        c = self.sim.gemm(xq, np.ascontiguousarray(wq.T), group_tiles=g // TILE)
        self.calls += 1
        self.mismatches += int(not np.array_equal(c, quant.igemm_groups_reference(xq, wq, g)))
        return c


def _combine(parts: np.ndarray, sx: np.ndarray, sw: np.ndarray) -> np.ndarray:
    """[groups, M, N] int32 partials with per-(row, group) activation scales and per-(channel, group) weight scales -> float32 [M, N]."""
    return np.einsum("gmn,mg,ng->mn", parts.astype(np.float32), sx, sw, optimize=True).astype(np.float32)


class _QW:
    """A quantised, K-grouped weight matrix [N, K] held by one chip."""

    def __init__(self, w: np.ndarray, g: int):
        self.g = g
        self.wq, self.sw = quant.quant_rows_grouped(np.asarray(w, np.float32), g)


class ClusterModel(llm.Llama):
    def __init__(self, model_dir, tp: int, backends: list, weights: dict[str, np.ndarray], group_tp: int | None = None, link_bytes_per_cycle: int = 16, group_target: int = quant.DEFAULT_GROUP_COLS,
                 reduce: str = "gather", ring_fn=None):
        super().__init__(model_dir)
        self.w = weights                                    # SmoothQuant-folded float weights (quant.load_smooth)
        c = self.cfg
        self.tp, self.backends, self.link = tp, backends, link_bytes_per_cycle
        self.reduce = reduce                                 # "gather": ring all-gather of the partials + ordered sum (rank 0..C-1); "allreduce": ring all-reduce (fixed ring order, fp32 adders in the ring)
        self.ring_fn = ring_fn                               # optional callable(list of C float32 vectors) -> list of C results: the REAL RTL ring replaces the numpy emulation
        assert len(backends) == tp
        gt = group_tp or tp                                  # sharding the group sizes are derived from (tp=1 with group_tp=C is the unsharded reference with identical groups)
        assert c.heads % gt == 0 and c.kv_heads % gt == 0 and c.inter % gt == 0
        self.hd = c.head_dim
        self.hl, self.kvl, self.il = c.heads // tp, c.kv_heads // tp, c.inter // tp
        self.rep = c.heads // c.kv_heads
        self.g_h = pick_group(c.hidden, group_target)                              # column-parallel GEMMs: K = hidden
        self.g_o = pick_group((c.heads // gt) * self.hd, group_target)            # o_proj shard K
        self.g_d = pick_group(c.inter // gt, group_target)                        # down_proj shard K
        self.shards: list[dict] = []
        self.cyc_total = 0
        self.cyc_collective = 0
        self.collectives = 0
        self._last = [0] * tp
        self._prepare()

    # ------------------------------------------------------------------ weights
    def _prepare(self):
        c, hd = self.cfg, self.hd
        vb = np.array_split(np.arange(c.vocab), self.tp)
        emb = self.w["model.embed_tokens.weight"]
        for ch in range(self.tp):
            s = {"layers": [], "vocab": (int(vb[ch][0]), int(vb[ch][-1]) + 1)}
            for L in range(c.layers):
                p = f"model.layers.{L}."
                q = self.w[p + "self_attn.q_proj.weight"][ch * self.hl * hd:(ch + 1) * self.hl * hd]
                k = self.w[p + "self_attn.k_proj.weight"][ch * self.kvl * hd:(ch + 1) * self.kvl * hd]
                v = self.w[p + "self_attn.v_proj.weight"][ch * self.kvl * hd:(ch + 1) * self.kvl * hd]
                o = self.w[p + "self_attn.o_proj.weight"][:, ch * self.hl * hd:(ch + 1) * self.hl * hd]
                gt = self.w[p + "mlp.gate_proj.weight"][ch * self.il:(ch + 1) * self.il]
                up = self.w[p + "mlp.up_proj.weight"][ch * self.il:(ch + 1) * self.il]
                dn = self.w[p + "mlp.down_proj.weight"][:, ch * self.il:(ch + 1) * self.il]
                s["layers"].append({"qkv": _QW(np.concatenate([q, k, v], 0), self.g_h), "o": _QW(o, self.g_o), "gu": _QW(np.concatenate([gt, up], 0), self.g_h), "down": _QW(dn, self.g_d)})
            s["lm"] = _QW(emb[s["vocab"][0]:s["vocab"][1]], self.g_h)
            self.shards.append(s)

    def new_caches(self):
        c = self.cfg
        return [[{"k": np.zeros((0, self.kvl, self.hd), np.float32), "v": np.zeros((0, self.kvl, self.hd), np.float32)} for _ in range(c.layers)] for _ in range(self.tp)]

    # ------------------------------------------------------------------ cycle accounting
    def _barrier(self, nbytes: int):
        """A synchronisation point: chips ran in parallel since the last one; then an all-gather of `nbytes` per chip."""
        now = [b.cycles for b in self.backends]
        seg = max(n - l for n, l in zip(now, self._last))
        coll = perf.ring_allgather_cycles(self.tp, nbytes, self.link) if self.reduce == "gather" else perf.ring_allreduce_cycles(self.tp, nbytes, self.link)
        self.cyc_total += seg + coll
        self.cyc_collective += coll
        self.collectives += 1 if self.tp > 1 else 0
        self._last = now

    def _reduce(self, partials: list[np.ndarray]) -> np.ndarray:
        """Combine the row-parallel partial outputs of the chips."""
        if self.tp == 1:
            return partials[0]
        if self.reduce == "gather":
            y = partials[0]
            for pt in partials[1:]:
                y = y + pt                                       # ordered sum, rank 0..C-1
            return y
        shape = partials[0].shape
        lanes = max(1, self.link // 4)
        n = partials[0].size
        npad = -(-n // (self.tp * lanes)) * self.tp * lanes      # the ring moves C equal chunks of whole beats
        vecs = []
        for pt in partials:
            v = np.zeros(npad, np.float32); v[:n] = pt.ravel(); vecs.append(v)
        if self.ring_fn is not None:
            res = self.ring_fn(vecs)
            assert all(np.array_equal(r.view(np.uint32), res[0].view(np.uint32)) for r in res), "ring all-reduce results differ between chips"
            out = res[0]
        else:
            from . import rtl
            out = rtl.ring_allreduce_reference(vecs)
        return out[:n].reshape(shape)

    # ------------------------------------------------------------------ attention on one chip (GQA grouped: the query heads of a KV group are rows of one GEMM)
    def _attn_qk(self, ch, q, k):
        qq, sq = quant.quant_rows(q)
        kq, sk = quant.quant_rows(k)
        return (self.backends[ch].igemm(qq, kq).astype(np.float32) * sq[:, None]) * sk[None, :]

    def _attn_pv(self, ch, p, v):
        pq, sp = quant.quant_rows(p)
        sv = np.abs(v).max(axis=0) / quant.QMAX
        sv = np.where(sv == 0, 1.0, sv).astype(np.float32)
        vq = np.clip(np.rint(v / sv[None, :]), -quant.QMAX, quant.QMAX).astype(np.int8)
        return (self.backends[ch].igemm(pq, np.ascontiguousarray(vq.T)).astype(np.float32) * sp[:, None]) * sv[None, :]

    def _attention(self, ch, L, q, k, v, caches, pos, T):
        c, hd = self.cfg, self.hd
        q, k = self.rope(q, pos), self.rope(k, pos)
        cache = caches[ch][L]
        cache["k"] = np.concatenate([cache["k"], k]); cache["v"] = np.concatenate([cache["v"], v])
        K, V = cache["k"], cache["v"]
        S = K.shape[0]
        mask = np.arange(S)[None, :] > pos[:, None]
        out = np.zeros((T, self.hl, hd), np.float32)
        for g in range(self.kvl):
            hs = list(range(g * self.rep, (g + 1) * self.rep))
            qg = np.concatenate([q[:, h] for h in hs], axis=0)
            sc = self._attn_qk(ch, qg, K[:, g]) / np.sqrt(hd)
            sc = np.where(np.tile(mask, (self.rep, 1)), -1e30, sc)
            o = self._attn_pv(ch, llm.softmax(sc).astype(np.float32), V[:, g])
            for i, h in enumerate(hs):
                out[:, h] = o[i * T:(i + 1) * T]
        return out.reshape(T, self.hl * hd)

    def _grouped(self, ch, x_q, sx, qw):
        return _combine(self.backends[ch].igemm_groups(x_q, qw.wq, qw.g), sx, qw.sw)

    # ------------------------------------------------------------------ forward (last-row logits only)
    def forward_cluster(self, tokens, caches, start: int, all_rows: bool = False) -> np.ndarray:
        c, hd = self.cfg, self.hd
        T = len(tokens)
        pos = np.arange(start, start + T)
        x = self.w["model.embed_tokens.weight"][tokens].astype(np.float32)
        for L in range(c.layers):
            p = f"model.layers.{L}."
            h = llm.rmsnorm(x, self.w[p + "input_layernorm.weight"], c.eps)
            hq, sh = quant.quant_rows_grouped(h, self.g_h)
            partials = []
            for ch in range(self.tp):
                sh_ = self.shards[ch]["layers"][L]
                qkv = self._grouped(ch, hq, sh, sh_["qkv"])
                q = qkv[:, :self.hl * hd].reshape(T, self.hl, hd)
                k = qkv[:, self.hl * hd:(self.hl + self.kvl) * hd].reshape(T, self.kvl, hd)
                v = qkv[:, (self.hl + self.kvl) * hd:].reshape(T, self.kvl, hd)
                a = self._attention(ch, L, q, k, v, caches, pos, T)
                aq, sa = quant.quant_rows_grouped(a, self.g_o)
                partials.append(self._grouped(ch, aq, sa, sh_["o"]))
            self._barrier(T * c.hidden * 4)
            x = x + self._reduce(partials)
            h = llm.rmsnorm(x, self.w[p + "post_attention_layernorm.weight"], c.eps)
            hq, sh = quant.quant_rows_grouped(h, self.g_h)
            partials = []
            for ch in range(self.tp):
                sh_ = self.shards[ch]["layers"][L]
                gu = self._grouped(ch, hq, sh, sh_["gu"])
                act = llm.silu(gu[:, :self.il]) * gu[:, self.il:]
                aq, sa = quant.quant_rows_grouped(act, self.g_d)
                partials.append(self._grouped(ch, aq, sa, sh_["down"]))
            self._barrier(T * c.hidden * 4)
            x = x + self._reduce(partials)
        x = llm.rmsnorm(x if all_rows else x[-1:], self.w["model.norm.weight"], c.eps)
        xq, sx = quant.quant_rows_grouped(x, self.g_h)
        logits = np.concatenate([self._grouped(ch, xq, sx, self.shards[ch]["lm"]) for ch in range(self.tp)], axis=1)
        self._barrier(2 * 4)                                 # per-chip (max, argmax) pair gathered; modelled as 8 bytes per chip
        return logits if all_rows else logits[0]

    def forward(self, tokens, cache=None, start: int = 0):
        """All-rows logits [T, vocab] (llm.perplexity interface; uses fresh caches)."""
        return self.forward_cluster(np.asarray(tokens), self.new_caches(), 0, all_rows=True)

    def generate(self, prompt: np.ndarray, n_new: int, forced: list[int] | None = None):
        """Greedy decode. With `forced` (token ids), the model is fed THOSE tokens instead of its own argmax (teacher forcing) so that logits of different implementations are compared on identical contexts."""
        caches = self.new_caches()
        self._reset_stats()
        lg = self.forward_cluster(prompt, caches, 0)
        pre = {"cycles": self.cyc_total, "collective": self.cyc_collective}
        out, logits = [int(np.argmax(lg))], [lg]
        for i in range(n_new - 1):
            lg = self.forward_cluster(np.array([forced[i] if forced is not None else out[-1]]), caches, len(prompt) + i)
            out.append(int(np.argmax(lg))); logits.append(lg)
        dec = {"cycles": self.cyc_total - pre["cycles"], "collective": self.cyc_collective - pre["collective"]}
        return out, np.stack(logits), pre, dec

    def _reset_stats(self):
        self.cyc_total = 0
        self.cyc_collective = 0
        self.collectives = 0
        self._last = [b.cycles for b in self.backends]
