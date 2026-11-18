# TPU for open-weight LLM serving: results (SmolLM2-135M, W8A8)

Criteria C1-C6 were fixed beforehand in `docs/TPU_PLAN.md`. Everything below is reproducible from files in this repo; raw numbers are in `results/tpu/*.json`.

## What was built
Weight-stationary INT8 systolic array (N x N, N=16 simulated) with int32 accumulation, K/N tiling in a core with activation buffer + accumulators, streaming weight fetch, three iterated versions kept side by side:

| version | RTL | idea |
|---|---|---|
| v1 | `hardware/tpu/v1` | baseline: single-buffered weights loaded by column shift, no overlap of load / stream / drain, one memory request per tile |
| v2 | `hardware/tpu/v2` | shadow weight registers per PE; the swap tag travels on the activation wavefront, so tile t+1 loads while tile t computes; decoupled fetch and compute engines |
| v3 | `hardware/tpu/v3` | one streaming memory request per GEMM with valid/ready backpressure (removes the per-tile request handshake) |
| v4 | `hardware/tpu/v4` | product registered before the accumulate add (multiplier and adder in separate stages; array latency +1) |
| v5 | `hardware/tpu/v5` | in-array partial sums 16+log2 N bits wide instead of 32 (area -12 %) |
| v6 | `hardware/tpu/v6` | zero-bubble tile hand-over: the swap is decided in the cycle of the last weight beat (decode 93.5 % -> 99.3 % of roofline) |
| v7 | `hardware/tpu/v7` | reset hygiene: no reset on data-path flops, local registered reset (post-route slow-corner path no longer dominated by the reset fanout) |
| v8 | `hardware/tpu/v8` | `tpu_multi`: K arrays with their own weight ports splitting the output columns (decode 8.44 M -> 4.23 M -> 2.13 M cycles/token for K = 1, 2, 4) |
| v9 | `hardware/tpu/v9` | K-group partial sums (`cmd_gk`, `out_g`): per-group activation/weight scales on the host (perplexity degradation +1.60 % -> +0.27 %) |

Model flow (`src/tpu/`): own safetensors reader + numpy float32 Llama forward (validated by perplexity, coherence, KV-cache equality); W8A8 with per-output-channel weight scales, dynamic per-token activation scales, SmoothQuant folding (alpha 0.5). Every matrix product (q/k/v/o, gate/up/down, tied lm_head, attention QK and PV) goes through one integer GEMM hook that is executed on the Verilator model of the RTL (`hardware/tpu/tb/harness.cpp`, persistent process) and compared to an independent numpy int32 matmul.

## Criteria
| id | result | evidence |
|---|---|---|
| C1 correctness | **met**: 0 mismatches over 2253 GEMMs (16-token prefill + 2 decode steps), 29718 GEMMs (16 sequences, batched decode), 1502 GEMMs (513-token perplexity run); random-shape tests with throttled/latent memory (`tests/test_tpu_rtl.py`) for v1-v3 | `results/tpu/v*_cosim*.json`, `v3_ppl_rtl_513.json` |
| C2 quality | **met, with a thin margin**: SmoothQuant alpha 0.6 (chosen on held-out validation text, not on the test set: 0.4 +4.51 %, 0.5 +2.00 %, 0.6 +1.17 %, 0.7 +2.63 %) gives +1.37 % perplexity on 4097 test tokens (25.781 vs float32 25.432) through the integer path, which the RTL reproduces bit for bit; executed on the v8 RTL: 26.075 vs 25.665 (+1.60 %, 1025 tokens, 3004 GEMMs, 0 mismatches). At alpha 0.5 the 4097-token result was +1.91 %; plain W8A8 without SmoothQuant +12.3 %. Activation-scale quantile clipping (+2.9 % and worse) and MSE weight scales (no change) were tried and rejected. Greedy agreement with float32 is NOT perfect: 87.5 % of 48 generated tokens (B=16 run) | `results/tpu/v8_ppl_rtl_1025_alpha0.6.json`, `results/tpu/quality_4097_alpha0.5.txt`, `tools/tpu_alpha_select.py` |
| C2b K-group quantisation (v9) | **recommended configuration, margin roughly 3-6x depending on the text**: separate activation and weight scales per group of 288 K columns (18 K tiles); the v9 core emits one exact int32 partial sum per group (`cmd_gk`, `out_g`), the host combines them in float. Executed on the v9 RTL: 25.734 vs float32 25.665 (**+0.27 %**, 1025 tokens, 1564 GEMMs, 0 mismatches) against +1.60 % without groups on the same tokens; numpy-path experiments: 1025 tokens group 288 +0.40 %, group 144 +0.57 %; 4097 tokens group 288 +0.72 % against +1.37 % without groups (`results/tpu/quality_groups_4097_alpha0.6.txt`). Cost: no extra array cycles; output traffic grows by the number of groups (2 for K=576, 6 for K=1536) | `results/tpu/v9_ppl_rtl_1025_g288.json`, `tools/tpu_quality_groups.py` |
| C3 compute utilisation | **met**: prefill 93.7 % (M=16 rows per GEMM, v3); 99.9 % with M=64 chunks (513-token run); batched decode (B=16) 81 % | json files |
| C4 memory efficiency | **met**: decode at M=1: 93.5 % of the memory roofline (v3), batched decode 86.4 % of roofline (compute/memory balanced); roofline = per GEMM max(MACs/N^2, weight bytes/(N bytes per cycle)), unpadded | json files |
| C5 physical | **partially met, NOT signed off**: see below | `results/tpu/v3_array_n8_flow_run*.json` |
| C6 iteration | **met**: v1 -> v3 measured on identical workload (table) | below |

### Iteration evidence (N=16, memory port 16 B/cycle, 0 latency; 16-token prefill + 3 decode)
| | prefill util | decode util (M=1) | decode % of memory roofline | decode cycles/token |
|---|---|---|---|---|
| v1 | 24.2 % | 2.0 % | 31.3 % | 27.0 M |
| v2 | 79.7 % | 5.0 % | 79.5 % | 10.6 M |
| v3 | 93.7 % | 5.8 % | 93.5 % | 9.03 M |
| v3, 16 sequences batched | 93.7 % | 81 % | 86.4 % | 0.65 M per token (41x fewer than v1 single-stream) |
| v6 (+GQA), 128-token prefill, 3 decode | 99.9 % | 6.1 % | 99.7 % | 8.52 M |
| v6 (+GQA), 16 sequences batched | 99.8 % | 85.8 % | 91.5 % | 0.615 M per token |

v4-v6 (registered product, narrow partial sums, zero-bubble tile hand-over) and the gap analysis are in `docs/TPU_GAP.md`; the table above was measured with the versions named.

M=1 decode is inherently memory bound (5.8 % compute utilisation is the roofline, not a defect): serving efficiency comes from batching, which the same hardware does with no change (M=B rows).

## Physical (sky130A, LibreLane/OpenROAD/Magic/KLayout/Netgen), v3 array only, N=8 (64 MACs)
| run | clock | DRC (Magic, KLayout) | LVS | antenna | tt/ff corners | ss_100C_1v60 corner | verdict |
|---|---|---|---|---|---|---|---|
| 1 | 12 ns | 0 / 0 | clean | 0 | setup +3.4 ns, hold +0.1 ns | setup -2.95 ns, hold -0.62 ns, 7589 slew violations | flow check **failed** |
| 2 | 16 ns (hold margin 0.6) | 0 / 0 | clean | 0 | setup +5.5 ns, hold +0.4 ns | setup -1.78 ns, hold -0.44 ns, 12365 slew violations | flow check **failed** |

So the layout is physically clean (DRC/LVS/antenna/route) and meets timing at typical and fast corners, but is **not sign-off clean at the slow corner**: the single-cycle 8x8 multiply plus 32-bit add needs about 18-19 ns there. Not claimed as clean.
Estimates (labelled): standard-cell area 0.67-0.74 mm^2 for the 64-MAC array including 4813 flops (about 10-11 k um^2 per MAC; the timing-driven mapping is not area-optimised); vectorless total power 0.10-0.125 W -> about 24 pJ per MAC-cycle at the flow clocks (activity is the tool default, so treat as order of magnitude only). Not done: the core (activation buffer and accumulators, which would be SRAM macros) and the N=16 array through the flow.

## Honest limits
- The array, not the whole core, went through the physical flow, and its slow-corner timing failed; closing it needs either a ~20 ns clock (re-run with `python tools/tpu_flow.py v3 8 20 <dir> tpu_array 40 1.0`; blocked here by disk space, the old 16 ns run directory `/home/user/data/tpu_flow/v3_array_n8_16ns/runs` can be deleted to make room) or a pipelined MAC.
- Memory is a parametrised model (bandwidth, latency), not a DRAM controller; host work (RMSNorm, RoPE, softmax, SiLU, scale arithmetic) is not on the TPU.
- Cycle counts come from the RTL simulation; absolute tokens/s depend on the closed clock, which is not established (at 62.5 MHz it would be about 96 tokens/s aggregate with 16 sequences, 1 GB/s weight bandwidth; this is a derived figure, not a measurement).
- ABUF fill by the host is excluded from the cycle counts (overlappable in a real system, not modelled).
- The 135M model is small; larger Llama-family shapes use the same GEMM interface but were not run.

## Reproduce
```
python tools/tpu_fetch_model.py ; PYTHONPATH=src python -m pytest tests/test_tpu_rtl.py
PYTHONPATH=src python tools/tpu_cosim.py v3 --n 16 --prefill 16 --decode 3 [--batch 16]
PYTHONPATH=src python tools/tpu_ppl.py v3 513
python tools/tpu_flow.py v7 8 14 <workdir> tpu_array 35 0.8
python -m sparky.cli tpu-perf --model llama-3-8b --n 128 --cores 4 --clock-mhz 1000 --batch 64 --ctx 1024   # projection from the RTL-validated cycle model
```

## End-to-end serving demo (v8, 4 arrays of 16x16, 4 prompts batched, greedy, 24 new tokens each)
`python tools/tpu_serve.py v8 --n 16 --cores 4 --new 24` runs the real SmolLM2-135M (W8A8 + SmoothQuant) with every matrix product (56,097 GEMMs) on the RTL, bit-exact against the integer reference (0 mismatches). Result (`results/tpu/serve_demo_v8_k4.json`): 572 k cycles per generated token, about 122 tokens/s at an ASSUMED 70 MHz clock (not a closed-timing figure). Completions (greedy, 135M model): "Photosynthesis is the process by which plants convert light energy into chemical energy ..."; "In 1969, the Apollo 11 mission was launched. The mission was a success, and the crew of Neil Armstrong and Buzz Aldrin became the first humans to ..."; one prompt degenerates into repetition ("The capital of France is the capital of the department of the department ..."), which is the 135M model under greedy decoding, not the accelerator (the same tokens are produced by the numpy integer reference, checked in `tools/tpu_cosim.py`).

### Serving demo with K-group quantisation (v9, 4 arrays, group 288)
`python tools/tpu_serve.py v9 --n 16 --cores 4 --new 24 --group-cols 288` (`results/tpu/serve_demo_v9_k4_g288.json`): same 56,097 GEMMs, 0 mismatches, 572 k cycles per generated token, plus 1.0 M un-overlapped host activation-buffer fill cycles over the whole decode (583 k per token, +1.9 %; a double-buffered activation buffer would hide it). Completions are better than the ungrouped run: "The capital of France is the capital of the country. The capital of France is Paris." / "Photosynthesis is the process by which plants convert light energy into chemical energy. The process is called photosynthesis." / "A systolic array is a collection of cells that are all the same size and shape ..." (a 135M model; the Apollo completion is factually wrong, which is the model, not the hardware).
