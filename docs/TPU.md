# TPU for open-weight LLM serving: results (SmolLM2-135M, W8A8)

Criteria C1-C6 were fixed beforehand in `docs/TPU_PLAN.md`. Everything below is reproducible from files in this repo; raw numbers are in `results/tpu/*.json`.

## What was built
Weight-stationary INT8 systolic array (N x N, N=16 simulated) with int32 accumulation, K/N tiling in a core with activation buffer + accumulators, streaming weight fetch, three iterated versions kept side by side:

| version | RTL | idea |
|---|---|---|
| v1 | `hardware/tpu/v1` | baseline: single-buffered weights loaded by column shift, no overlap of load / stream / drain, one memory request per tile |
| v2 | `hardware/tpu/v2` | shadow weight registers per PE; the swap tag travels on the activation wavefront, so tile t+1 loads while tile t computes; decoupled fetch and compute engines |
| v3 | `hardware/tpu/v3` | one streaming memory request per GEMM with valid/ready backpressure (removes the per-tile request handshake) |

Model flow (`src/tpu/`): own safetensors reader + numpy float32 Llama forward (validated by perplexity, coherence, KV-cache equality); W8A8 with per-output-channel weight scales, dynamic per-token activation scales, SmoothQuant folding (alpha 0.5). Every matrix product (q/k/v/o, gate/up/down, tied lm_head, attention QK and PV) goes through one integer GEMM hook that is executed on the Verilator model of the RTL (`hardware/tpu/tb/harness.cpp`, persistent process) and compared to an independent numpy int32 matmul.

## Criteria
| id | result | evidence |
|---|---|---|
| C1 correctness | **met**: 0 mismatches over 2253 GEMMs (16-token prefill + 2 decode steps), 29718 GEMMs (16 sequences, batched decode), 1502 GEMMs (513-token perplexity run); random-shape tests with throttled/latent memory (`tests/test_tpu_rtl.py`) for v1-v3 | `results/tpu/v*_cosim*.json`, `v3_ppl_rtl_513.json` |
| C2 quality | **met**: perplexity executed on the RTL 21.092 vs float32 20.925 (+0.80 %, 513 tokens); numpy int path (bit-identical) +1.56 % over 2049 tokens (26.920 vs 26.508); plain W8A8 without SmoothQuant was +11.56 % (failed) | `v3_ppl_rtl_513.json`, `docs` history in git. Greedy agreement with float32 is NOT perfect: 87.5 % of 48 generated tokens (B=16 run) |
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
python tools/tpu_flow.py v3 8 16 <workdir> tpu_array 40 0.6
```
