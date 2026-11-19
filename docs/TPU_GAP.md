# How far is this TPU from modern inference TPUs, and what closes the gap

All "ours" numbers come from RTL simulation or the sky130 flow in this repo (`docs/TPU.md`, `results/tpu/`). Published numbers are from Google's own documentation (links at the end) and were NOT re-measured; clock and process-related factors that are not in those pages are marked as assumptions.

## 1. Size of the gap
| | this TPU (N=16, sky130) | TPU v1 (2015) | TPU v5e | TPU v7 "Ironwood" |
|---|---|---|---|---|
| int8 / fp8 MACs | 256 | 65,536 | 4 MXUs per chip, 393 TOPS int8 peak | 4,614 TFLOPs fp8 peak |
| peak ops | ~0.036 TOPS at an assumed 70 MHz | 92 TOPS | 393 TOPS | ~4,600 TFLOPs |
| memory | weight port N bytes/cycle = ~1.1 GB/s at 70 MHz (model, not DRAM) | 28 MiB on-chip + DDR | 16 GB HBM, 819 GB/s | 192 GB HBM, ~7.4 TB/s |
| ratio (peak ops) | 1x | ~2,500x | ~10,900x | ~130,000x |
| ratio (memory bandwidth) | 1x | - | ~730x | ~6,700x |

Decomposition of the v5e peak ratio (about 10,900x): 256x from MAC count alone (65,536 vs 256); the remaining ~43x is clock frequency times any per-cell int8 rate advantage (Google's clock is not in the pages I read, so I do not split it further). Process node (130 nm vs a modern node) is the main driver of both area per MAC and clock; it is not an architecture lever and cannot be closed here.

What the gap is NOT: utilisation. On the workloads run here the design reaches 99.9 % array utilisation in prefill and 99.7 % of the memory roofline in decode (v6); published utilisation numbers for production TPUs are workload dependent and I did not find a comparable figure, so I make no claim either way. The remaining gap is scale (MACs, clock, bandwidth, memory hierarchy), which the architecture can be extended to but not simulated at full size on this machine.

## 2. Levers, measured or projected (one row each; "measured" = RTL co-simulation of SmolLM2-135M, every GEMM bit-exact against numpy int32)
| lever | status | effect |
|---|---|---|
| weight double-buffering with swap tag on the activation wavefront (v2) | measured | prefill 24 % -> 80 %, decode cycles/token 27.0 M -> 10.6 M |
| one streaming weight request per GEMM + backpressure (v3) | measured | decode 93.5 % of roofline |
| registered product, multiplier and accumulate in separate stages (v4) | measured by RTL tests + pre-layout STA | slow-corner reg-to-reg path at N=8: -10.2 ns -> -2.3 ns slack at a 12 ns target (about 8 ns of cycle time); array latency +1 cycle |
| in-array partial sums only 16+log2 N bits wide instead of 32 (v5) | measured (RTL tests, synthesis) | synthesised area -12 % (326k -> 288k um^2 at N=8), shorter adder |
| zero-bubble tile hand-over (v6): swap decided in the cycle of the last weight beat | measured | decode 93.5 % -> 99.3 % of roofline, 9.29 M -> 8.74 M cycles/token; with GQA grouping 99.7 %, 8.52 M |
| GQA-grouped attention (rep query heads become rows of one GEMM) | measured | GEMMs 2253 -> 1173, bit-exact, KV streamed once per group; -2.5 % decode cycles at 128-token context (grows with context) |
| batching sequences (M = B on every weight GEMM) | measured | decode compute utilisation 5.8 % (B=1) -> 81 % (B=16, v3) -> 85.8 % (B=16, v6 + GQA grouping; 23,958 GEMMs bit-exact, 615 k cycles/token, 91.5 % of roofline) |
| reset hygiene (v7): no reset on data-path flops, local registered reset | RTL-tested; post-layout effect pending (flow v7 running) | v5's worst post-route slow-corner path was `rst_n` fanning out to ~2600 flop resets, not the multiplier |
| scale-out: K arrays with their own weight ports splitting the output columns (v8 `tpu_multi`) | measured | SmolLM2-135M decode (GQA, v8, N=16): 8.44 M -> 4.23 M -> 2.13 M cycles/token for K = 1, 2, 4 (1.99x, 3.97x), bit-exact, 99.0-99.7 % of each configuration's memory roofline; also cycle-model checked |
| K-group quantisation (v9): core emits one int32 partial sum per group of K tiles, host applies per-group scales | measured on RTL | perplexity degradation +1.60 % -> +0.27 % (1025 tokens); no array-cycle cost; output vectors x(number of groups). Turns the thin W8A8 quality margin into a comfortable one |
| fused projections (q/k/v and gate/up as single wider GEMMs, host-side, bit-identical) | measured | GEMMs 1173 -> 903 per 128-token-context run (-23 %), decode 8,523,499 -> 8,520,169 cycles/token (-0.04 % at N=16; the saving is per-command overhead and activation-buffer fills, which grows with array size) |
| tolerance to memory latency | measured | 100-cycle latency costs 0.8 % of decode; 400 cycles ~3 %: command prefetch is not worth building at this scale |
| 4-bit weights (RTN, per-group scales) to halve weight bytes | **rejected on quality**: +27 % (group 32) to +45 % (group 128) perplexity on this model (1025 tokens, `tools/tpu_w4.py`); would need GPTQ/AWQ-class calibration, not available here |
| pipelined commands (hide ~2N+4 fill/drain per GEMM) | projection (cycle-model what-if, `tools/tpu_levers.py`) | ~0.05 % at N=16, 1.24-1.3x at N=128 x4 where thousands of tiny attention GEMMs dominate |
| multi-row weight write per cycle (needs proportionally more memory bandwidth) | projection | 1.12x at N=16 B=16, 1.4-1.67x at N=128 x4 (tile load N cycles caps rows-per-tile utilisation) |

The cycle model (`src/tpu/perf.py`) reproduces the RTL cycle counts exactly for integer beats-per-tile and within max(2 cycles, 1 %) otherwise (`tests/test_tpu_perf.py`), and reproduces the measured full-network decode cycles of the co-simulation to the cycle. The projection rows reuse that model with the lever's assumed behaviour; they are NOT implemented in RTL.

## 3. Scaled projections (assumptions are inputs; `tools/tpu_gap.py`)
Llama-3-8B-class int8, batch 64, 1024-token context, validated-model schedule: the as-built design would need ~31 s per decode step (2 tokens/s); N=128 x4 arrays at an ASSUMED 1 GHz and 512 GB/s gives ~45 ms/step (~1,400 tokens/s); a v5e-class chip is bounded by HBM at <= ~100 steps/s for 8 GB of weights (published bandwidth), i.e. a few thousand tokens/s at batch 64 before KV traffic. At scale the schedule is limited by (i) tile weight load of N cycles vs batch rows and (ii) KV-cache streaming in attention, which is matrix-vector work that a weight-stationary array uses at 4/128 row occupancy.

## 4. Status of the levers and what is left, ranked
Done and measured (RTL, bit-exact): v2 shadow weights, v3 streaming fetch, v4 registered product, v5 narrow partial sums, v6 zero-bubble hand-over, v7 reset hygiene, v8 multi-array scale-out, v9 K-group partial sums (quality), v10 pipelined accumulator (whole-core timing), GQA grouping, fused projections; two open-weight models run (SmolLM2-135M and -360M). Rejected on evidence: 4-bit RTN weights, activation clipping, MSE weight scales, memory-latency hiding at this scale.
Scale-out beyond one chip is documented in `docs/TPU_SCALEOUT.md` (measured on 3 and 5 RTL chips, projected beyond).
Open, in order of expected value:
1. Multi-row weight write plus matching memory bandwidth. Projected 1.12x at N=16 with 16 sequences, 1.4-1.7x at N=128 x4. The simple version fails the swap-hazard check (the new tile cannot be written ahead of the old tile's swap wavefront), so it needs a third weight register per PE (about +13 % PE area) or a skewed write; not built.
2. Pipelined command queue with a double-buffered activation buffer: removes about 3 % of per-command overhead and activation-fill cycles at N=16 (1.2-1.3x only at N=128 with thousands of tiny attention GEMMs). Not built.
3. SRAM macros for the activation buffer and accumulators (OpenRAM), and the N=16 core through the flow; the flop-based buffers dominate area now (about 8,200 flops at N=8 with tiny buffers).
4. On-chip requantisation/dequantisation and the vector work (SiLU, RMSNorm, softmax): removes int32 output traffic and host work; not built, host work is not in the cycle counts.
5. Hold residue of -0.02 ns in the ff corners of the core (flop-to-flop skew chains) and a clean independent re-run of the whole core.
6. Long-context KV handling (int4 KV, MLA/windowing are model-level and need accuracy validation), sparsity, fp8, calibrated int4 weights (GPTQ/AWQ-class tooling not available here).

## Sources
- [TPU v5e specifications (Google Cloud)](https://docs.cloud.google.com/tpu/docs/v5e): 393 TOPS int8, 197 TFLOPs bf16, 16 GB HBM; bandwidth 819 GB/s on one Google page and 800 GB/s on another.
- [TPU7x (Ironwood) (Google Cloud)](https://docs.cloud.google.com/tpu/docs/tpu7x): 4,614 TFLOPs fp8, ~7,380 GB/s HBM, 192 GB.
- [In-Datacenter Performance Analysis of a Tensor Processing Unit](https://research.google/pubs/pub46078/): 65,536 8-bit MACs, 92 TOPS, 28 MiB on-chip memory.
