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
| C2b K-group quantisation (v9) | **recommended configuration, margin roughly 2.5-6x depending on the text**: separate activation and weight scales per group of 288 K columns (18 K tiles); the v9 core emits one exact int32 partial sum per group (`cmd_gk`, `out_g`), the host combines them in float. Executed on the v9 RTL: 25.734 vs float32 25.665 (**+0.27 %**, 1025 tokens, 1564 GEMMs, 0 mismatches) against +1.60 % without groups on the same tokens; numpy-path experiments: 1025 tokens group 288 +0.40 %, group 144 +0.57 %; 4097 tokens group 288 +0.72 % against +1.37 % without groups (`results/tpu/quality_groups_4097_alpha0.6.txt`). With groups the held-out-selected SmoothQuant alpha moves to 0.7 (held-out +0.75 %, test +0.58 % on 4097 tokens; alpha 0.5/0.6/0.8/0.9: +1.77/+1.11/+0.83/+1.83 % held-out, `results/tpu/alpha_select_group288_*.txt`); RTL run with it: 25.735 vs 25.665 (+0.27 %, 1025 tokens, 0 mismatches, `results/tpu/v9_ppl_rtl_1025_g288_a0.7.json`). Cost: no extra array cycles; output traffic grows by the number of groups (2 for K=576, 6 for K=1536) | `results/tpu/v9_ppl_rtl_1025_g288.json`, `tools/tpu_quality_groups.py` |
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

## Physical (sky130A, LibreLane/OpenROAD/Magic/KLayout/Netgen), array only, N=8 (64 MACs)
| version / run | clock target | DRC (Magic, KLayout) | LVS | antenna | tt / ff corners (setup) | ss_100C_1v60 corner | hold | flow verdict |
|---|---|---|---|---|---|---|---|---|
| v3, 12 ns | 12 ns | 0 / 0 | clean | 0 | +3.4 ns | setup -2.95 ns | ss -0.62 ns | failed (hold, ss) |
| v3, 16 ns | 16 ns | 0 / 0 | clean | 0 | +5.5 ns | setup -1.78 ns | ss -0.44 ns | failed (hold) |
| v5, 14 ns | 14 ns | 0 / 0 | clean | 0 | +4.6 ns | setup -1.41 ns | ss -0.28 ns | failed (hold); worst ss path was the `rst_n` fanout |
| v7, 12 ns | 12 ns | 0 / 0 | clean | 0 | +4.8 / +6.5 ns | setup -1.16 ns | ss -0.12 ns | failed (hold in one corner) |
| **v7, 17 ns (util 35 %, hold margin 0.8, setup margin 2.4)** | 17 ns | **0 / 0** | **clean** | **0** | **+7.7 / +9.9 ns** | **setup +1.02 ns (worst corner)** | **+0.16 ns worst, positive in all 9 corners** | **flow ok, ALL 9 corners clean; independent re-runs agree** |
| v7, 16.5 ns (util 35 %, hold margin 0.8, setup margin 1.5) | 16.5 ns | **0 / 0** | **clean** | **0** | **+7.1 / +9.3 ns** | setup -0.28 ns (max corner; nom_ss +0.003, min_ss +0.29) | **+0.085 ns worst, positive in all 9 corners** | **flow ok**; independent re-runs agree |
| **v7, 14 ns (util 35 %, hold margin 0.8)** | 14 ns | **0 / 0** | **clean** | **0** | **+5.8 / +7.7 ns** | setup -1.76 ns | **+0.21 ns in all 9 corners** | **flow ok** |

The v7 14 ns run passed the flow and then the independent sign-off re-runs (`results/tpu/v7_array_n8_flow_14ns.json`): KLayout DRC with a separate rule deck, Magic DRC, Netgen LVS, OpenSTA at the nominal corner all agree with the flow (setup 5.763 ns and hold 0.707 ns match to the picosecond). **The 8x8 array is signed off at 17 ns (about 59 MHz worst case, about 130 MHz typical) in all nine PVT corners** (`results/tpu/v7_array_n8_flow_17ns_signoff_clean.json`; 0.535 mm^2 standard-cell area, 0.080 W vectorless power estimate). The slow-corner fix that worked was a resizer setup margin (the resizer sees all nine corners but stops when its placement-level estimate reaches the margin, and routing then adds about 1.4-2 ns, so the margin must exceed that degradation; a looser clock alone makes the slow corner worse: 17.5 ns with margin 1.5 gave -0.59 ns, 16.5 ns gave -0.28 ns, 17 ns with margin 2.4 gives +1.02 ns): at 16.5 ns with a 1.5 ns margin the slow corner is -0.28 ns at the worst parasitic corner and non-negative at nominal/min (`results/tpu/v7_array_n8_flow_165ns_setupmargin.json`, independent re-runs agree). For the 14 ns run: the slow corner (ss_100C_1v60) setup is -1.76 ns with max-slew violations because the resizer repairs only the nominal corner; read as a slow-corner minimum period of about 15.8 ns (v7 synthesised for 12 ns reached about 13.2 ns), i.e. roughly 63-76 MHz worst case and about 120 MHz typical at N=8. The datapath change history is visible in the table: removing reset fanout (v7) is what brought the slow-corner need down from about 15.4 ns (v5) / 15 ns (v3).
Estimates (labelled): standard-cell area 0.56-0.58 mm^2 for the 64-MAC array including about 4,750 flops (v3: 0.67-0.74 mm^2); vectorless power estimate 0.06-0.12 W depending on run (the tool's default activity; order of magnitude only). 

Whole core (N=8, small flop-based buffers). First run (v9, `results/tpu/v9_core_n8_flow_17ns.json`): all 76 flow steps completed with DRC (Magic and KLayout) 0, LVS 0, antenna 0 and hold positive in all nine corners, but slow-corner setup was -8.6 ns at a 17 ns target (worst-case period about 25.6 ns, about 39 MHz). A fast synthesis+STA check on the whole core (`tools/tpu_synth_sta.py v9 8 ss_100C_1v60 17 core full`) pointed to the real cause: the accumulator read-mux + 32-bit add + write in a single cycle, and runtime multipliers (`kt*nt`, `m*nt*groups`) in the control logic. **v10** pipelines the accumulator (hazard free: a row recurs at least N cycles apart) and takes the tile and output counts from the host. v10 passes the same RTL and co-simulation tests (903 GEMMs of the real model bit-exact, decode 99.7 % of roofline, `results/tpu/v10_cosim_small_g288.json`) and in the flow gives **setup positive in all nine corners at 17 ns (worst +0.19 ns at the slow corner, versus -8.57 ns for v9)** with DRC 0 / LVS 0 (`results/tpu/v10_core_n8_flow_17ns.json`; MMAX=4, KTMAX=2, std-cell area about 0.84-0.88 mm^2, 8,233 flops, power estimate about 0.07 W vectorless). Remaining blemish: hold -0.02 ns in the ff corners on flop-to-flop skew-chain paths (the flow flags it); one antenna net was fixed with more repair iterations; a hold-margin increase from 0.8 to 1.0 did not remove the ff hold residue (`results/tpu/v10_core_n8_flow_17ns_holdmargin1.json`), a retry with a larger hold-repair buffer budget is the open item. The independent sign-off re-runs for the core are NOT RUN. Not done: the N=16 array or core through the flow, and a core with SRAM macros instead of flop buffers.

## Honest limits
- Physical: the 8x8 array is signed off at 17 ns in all nine corners; the whole core (N=8, MMAX=4, KTMAX=2, flop buffers) has setup positive in all corners but a -0.02 ns ff hold residue and no independent re-run yet. N=16 and SRAM-macro buffers were not run through the flow (each flow run takes 1-2 hours and 4-6 GB of disk here).
- Memory is a parametrised model (bandwidth, latency), not a DRAM controller; host work (RMSNorm, RoPE, softmax, SiLU, scale arithmetic) is not on the TPU.
- Cycle counts come from the RTL simulation; absolute tokens/s depend on the clock: the signed-off 8x8 array closes the slow corner at 17 ns (about 59 MHz); at that clock the 4-array serving demo (572 k cycles per generated token) would be about 100 tokens/s for 4 sequences batched on a 135M model. Derived from measured cycles and the signed-off clock of the smaller N=8 array, not a measurement of the N=16 design.
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
