# Scaling the TPU out: from one array to a multi-chip cluster

Three levels of scale-out, each with RTL that is verified bit-exact (or cycle-exact) in simulation, then a projection layer for sizes this machine cannot simulate.

| level | what | RTL | status |
|---|---|---|---|
| 1. arrays per chip | K arrays, each with its own weight port, splitting the output columns of every GEMM (`tpu_multi`) | `hardware/tpu/v8/tpu_multi.v` | measured: 1.99x / 3.97x decode speedup for 2 / 4 arrays, `docs/TPU_GAP.md` |
| 2. chips per model | tensor parallelism over C chips (Megatron style): heads, KV cache, FFN and vocabulary sharded; row-parallel layers combined by a ring collective | ring all-gather `v11/tpu_ring.v`; float32 adder `v12_fp32_add.v` / `v13_fp32_add_p.v`; ring all-reduce `v12_ring_allreduce.v` / `v13_ring_allreduce.v` | measured on real models: 135M on 3 chips, 360M on 5 chips |
| 3. clusters | pipeline stages and data-parallel replicas on top of level 2 | none (composition of validated parts) | **projection only** (`tools/tpu_scaleout.py`) |

## Level 2 in detail
**Sharding** (`src/tpu/cluster.py`). With C chips: q/k/v projections are one fused column-parallel GEMM per chip (no communication); each chip runs the attention of its own query/KV heads against its own KV cache; `o_proj` and `down_proj` are row-parallel (each chip multiplies its K-shard and produces a full-width *partial* output); `gate/up` are column-parallel; the tied embedding / `lm_head` is split by vocabulary rows. The residual stream is replicated, so after each row-parallel layer the partial outputs are combined across chips: **2 collectives per layer**. K-group quantisation groups never straddle a shard (`pick_group`), so each chip applies its own group scales and the combine is a plain float32 sum.

**Collectives (RTL).**
- *Ring all-gather* (`tpu_ring.v`): data movement only (own vector first, then cut-through forwarding); every chip receives the other C-1 vectors; cycles = max((C-1)*beats + 1, 2(C-1)); exact against the RTL.
- *Float32 adder* (`v12_fp32_add.v` combinational, `v13_fp32_add_p.v` 4-stage pipelined): IEEE-754 binary32, round-to-nearest-even, **bit-exact against numpy** over 1.5 million random operand pairs including heavy cancellation, ties and large exponent gaps (`tests/test_tpu_fp32_add.py`). Operating range stated in the file: finite normal-range values (subnormals flushed, no inf/NaN handling).
- *Ring all-reduce* (reduce-scatter + all-gather, adders in the nodes): chunk j accumulates in the fixed order ((x_j + x_{j+1}) + ...) + x_{j-1}; every chip ends with the identical, deterministic result; traffic per chip 2(C-1)/C of the vector instead of C-1 vectors; cycles = 2(C-1)(chunk beats + 6) + 1 for the v13 node (`+1` for v12); **bit-exact against an independent numpy emulation of the fixed order and cycle-exact against the formula for 2-8 chips** (`tests/test_tpu_ring.py`; the test also checks that the result is the sum, against float64).

## Measured: real models on multiple RTL chips
Every chip is its own Verilator simulation of the v10 core (one 16x16 array per chip here), every GEMM on every chip is checked against numpy (0 mismatches below), and cycles are the slowest chip between synchronisation points plus the RTL-validated ring cost. Prefill 16 tokens, decode with teacher forcing (all implementations are fed the same tokens so logits are compared on identical contexts). Link: 16 bytes/cycle. Clock-independent (cycle counts).

| model | chips | collective | GEMMs checked | decode speedup vs 1 chip | decode efficiency | prefill speedup | prefill efficiency |
|---|---|---|---|---|---|---|---|
| SmolLM2-135M (9/3 heads) | 3 | all-gather | 1,629 | 2.98x | 99.3 % | 2.73x | 90.9 % |
| SmolLM2-135M | 3 | **all-reduce, RTL ring in the loop** | 2,172 | 2.98x | 99.4 % | 2.81x | 93.7 % |
| SmolLM2-360M (15/5 heads) | 5 | all-gather | 2,895 | 4.93x | 98.6 % | 4.11x | 82.1 % |
| SmolLM2-360M | 5 | **all-reduce, RTL ring in the loop** | 3,860 | 4.96x | 99.3 % | 4.59x | 91.9 % |

(`results/tpu/cluster_*.json`; the all-reduce rows run every all-reduce of the model through the RTL ring with the float32 adders, not through a model of it.) The cluster's logits are **exactly equal** to a numpy-sharded computation of the same algorithm in all runs (`rtl_cluster_logits_equal_numpy_sharded_exactly`), which is what verifies the chips, the shard bookkeeping and the ring together. The all-reduce engine raises prefill efficiency by 3-10 points over the all-gather.

**Sharded vs unsharded.** The two are different valid quantised implementations: they sum the row-parallel partial products in a different order, and a different float rounding sometimes flips an int8 rounding, so they are NOT bit-identical. Measured: max |logit difference| 1.2 (135M, tp 3) and 0.97 (360M, tp 5) on logits of scale about 23-24; perplexity (135M, 1025 tokens): float32 25.665, unsharded +/- noise -0.24 %, tensor-parallel +0.21 % (`results/tpu/cluster_quality_smollm2_135m_tp3.txt`), i.e. a 0.45 % difference that is rounding noise rather than a systematic loss. Greedy argmax agrees with the unsharded model except at near-ties: 135M flips once at a reference top-1/top-2 margin of 0.04 logits; 360M flips twice at margins of 0.086 and 0.015 (and agrees where the margin is 0.99 and 1.29). A claim of "identical tokens" is therefore NOT made; the claim is identical logits to the numpy-sharded algorithm and equivalent quality.

## Projections (not simulated): larger models, more chips
`tools/tpu_scaleout.py` composes per-chip schedule (`perf.gemm_cycles`, exact vs RTL) and ring collectives (exact vs RTL) into decode step times for tensor x pipeline x data parallel layouts. The composition is anchored to the measured runs: `tests/test_tpu_scaleout.py` reproduces the measured multi-chip decode cycles to within 1 % (135M: 0.006 %). **Chip clock, memory capacity and link bandwidth are inputs (assumptions), printed above every table; nothing below is a measurement.**

Assumed chip: 4 arrays of 128x128 at 1 GHz (131 TOPS peak, 512 GB/s weight port), 32 GB, link 64 GB/s per direction; Llama-3-8B-class, batch 64, 1024-token context, int8 weights and KV (`results/tpu/scaleout_projection_allreduce.txt`):

| chips (TP x PP x DP) | step ms | tokens/s | comm share | scaling efficiency |
|---|---|---|---|---|
| 1 (1x1x1) | 44.3 | 1,444 | 0 % | 100 % |
| 2 (2x1x1) | 23.2 | 2,755 | 4.5 % | 95 % |
| 4 (4x1x1) | 12.7 | 5,044 | 12.4 % | 87 % |
| 8 (8x1x1) | 7.49 | 8,550 | 24.6 % | 74 % |

Why the all-reduce matters at scale: with the all-gather instead, TP=8 spends 56.5 % of the step communicating and reaches 4,927 tokens/s (43 % efficiency) - `scaleout_projection_gather.txt`. With a narrow link (16 B/cycle = 16 GB/s) even the all-reduce is link-bound: 2 chips 84 %, 4 chips 64 %, 8 chips 43 % (`scaleout_projection_allreduce_link16.txt`) - tensor parallelism needs a fast link; pipeline and data parallelism do not. Beyond tensor-parallel degree = number of KV heads (8 for the 8B model) the layout adds pipeline stages and data-parallel replicas; 64 chips as 8x1x8 serve independent batches at 8x the single-group rate by construction (68 k tokens/s projected), and a 70B-class model needs at least 4 of these chips just to hold its weights (about 20 GB per chip at TP 4; throughput and layouts for TP x PP configurations are in the same results file).

## What is not done / honest limits
- The ring is bulk-synchronous (all nodes start together, no flow control, no link PHY, no error handling) and the nodes are simulated as one Verilator model; chips are separate processes simulated one after another (the cycle accounting takes the slowest chip per synchronisation point).
- Pipeline parallelism and data parallelism are projections only; no pipeline stage was run on RTL. Tensor-parallel degree is limited by the number of KV heads.
- Only 135M (3 chips) and 360M (5 chips) were run; the 8B and 70B rows are projections with assumed clock, memory and link.
- The ring nodes were not taken through the physical flow. A pre-layout timing check of the 4-stage float adder alone gave a worst stage of about 16 ns in the slow corner (sky130, no layout; comparable to the 17 ns clock the array closes at); the check of the whole node was dominated by unbuffered memory-select fanout and is not informative, so no ring-node timing claim is made. The node's memories are flops here; a real design would use SRAM macros.
- The reduction operates on float32 partial outputs because the K-group scales are applied per chip before the combine; an int32 all-reduce would be exact but would require scales shared across chips (loses the K-group quality gain on row-parallel layers).
- Host work (norms, RoPE, softmax, SiLU, scale arithmetic) is outside the cycle counts everywhere in this repository.

## Reproduce
```
PYTHONPATH=src python -m pytest tests/test_tpu_ring.py tests/test_tpu_fp32_add.py tests/test_tpu_scaleout.py
PYTHONPATH=src python tools/tpu_cluster.py --tp 3 --reduce allreduce --rtl-ring --decode 4                      # 135M on 3 chips
PYTHONPATH=src TPU_MODEL_DIR=/home/user/data/smollm2_360m python tools/tpu_cluster.py --tp 5 --reduce allreduce --rtl-ring --decode 4
PYTHONPATH=src python tools/tpu_cluster_quality.py 1025 3                                                       # sharded vs unsharded perplexity
PYTHONPATH=src python tools/tpu_scaleout.py --reduce allreduce [--link 16 --n 128 --cores 4 --clock-mhz 1000 --mem-gb 32 --batch 64 --ctx 1024]
```
