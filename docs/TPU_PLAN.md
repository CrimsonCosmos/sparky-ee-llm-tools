# TPU for serving an open-weight LLM: plan and success criteria (fixed BEFORE the design work)

Goal: an INT8 systolic-array accelerator ("TPU", in the sense of the original weight-stationary matrix unit) described in RTL in this repository, taken through the open silicon flow, and shown - with real
open-weight LLM inference run through its simulated RTL - to be efficient. "Efficient" is defined below by measurable criteria so that it cannot drift while the design evolves.

Workload: `HuggingFaceTB/SmolLM2-135M` (Apache-2.0, Llama architecture: 30 layers, d=576, 9 heads / 3 KV heads, FFN 1536, tied 49,152-token embedding), weights downloaded by `tools/tpu_fetch_model.py`.
The model is small enough to run through RTL simulation on this machine; everything is parametric in the layer shapes, so the same design serves larger Llama-family models (that extension is NOT demonstrated here).

Division of labour (stated so nothing is hidden): every matrix multiplication of inference (q/k/v/o projections, gate/up/down, the tied output projection, and the attention score and value products) runs on the
TPU RTL. RMSNorm, RoPE, softmax, SiLU, residual adds and quantisation scale arithmetic run on the host in float32 and are counted as host work (their share of operations is reported).

## Success criteria
| id | criterion | how it is measured |
|---|---|---|
| C1 | functional correctness | every TPU GEMM of every token run through the RTL (Verilator) is bit-exact against an integer numpy reference |
| C2 | model quality | perplexity of the W8A8 model executed on the TPU within 2 % of the float32 model on a fixed text; greedy generation agreement with float32 reported |
| C3 | compute utilisation | achieved MACs / (cycles x array MACs) in the matrix-heavy phases (prefill, batched decode) >= 90 % |
| C4 | memory efficiency | in weight-streaming phases (decode) the measured tokens/s is >= 85 % of min(compute roofline, memory roofline) for the stated memory bandwidth |
| C5 | physical | the core (smaller array size if needed) completes the sky130 flow with clean DRC/LVS and timing at the stated clock; area, power estimate and energy/MAC reported, with the extrapolation to the full array labelled as an estimate |
| C6 | iteration evidence | each design version is kept in the repo with its measured numbers; the final design must beat the first on C3/C4 by a measured margin |

Notes on honesty: (a) power from the flow is a vectorless estimate (labelled); (b) the memory system is a parametrised model in the testbench (bandwidth and latency are inputs), not a real DRAM controller;
(c) clock rates are derived from the sky130 timing results, so absolute tokens/s are low versus modern silicon - efficiency is argued through utilisation, roofline fractions and energy per MAC, not through absolute speed;
(d) the float32 reference forward is written in numpy and validated by perplexity, coherent generation and KV-cache/full-recompute equality (no torch is installed here for an independent logits comparison).

## Plan
T1 model loader + numpy float32 forward + W8A8 quantisation reference. T2 RTL: PE, systolic array, buffers, tile controller, requantiser, bit-exact tests. T3 co-simulation harness running the model's GEMMs on the RTL.
T4 iterate versions (single-buffered -> double-buffered weights -> memory prefetch -> batched decode) with measured efficiency per version. T5 sky130 flow of the core. T6 final report (`docs/TPU.md`).
