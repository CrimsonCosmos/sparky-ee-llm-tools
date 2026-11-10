# hdlbench: verifiable-reward loop for LLM-written logic circuits (plan)

Goal (from the discussion that started this): an LLM writes Verilog for a well-specified circuit; deterministic, open-source, laptop-speed tools score it; the same scorer serves
(a) an agent / evolutionary search loop (FunSearch/AlphaEvolve style) with NO training, and (b) later as an RL reward. No physical FPGA is needed in the loop.
Everything in the logic is digital and checkable by tools; the physical board is a final sanity check only (documented, adapter detected at runtime, NOT RUN when absent).

Top rules of this repo apply: integrate (Yosys, Icarus, Verilator, SymbiYosys/ABC, nextpnr via YoWASP) instead of reinventing; no hard-coded engineering values (device, LUT size,
reward weights, budgets are inputs with documented sources); and a CONFIDENTLY WRONG SCORER IS WORSE THAN NONE: every verifier stage is validated with designs that must fail.

## Pipeline (reward stages, each gates the next; partial credit)
0. interface   - candidate top has exactly the reference's ports (names/directions/widths read by Yosys, never hand-typed); no forbidden constructs (file I/O, $system, hierarchy
                 reaching for the reference). Score 0 if violated.
1. compiles    - Icarus -g2012 compiles and Yosys elaborates (`proc`) without error.
2. synthesizable - Yosys `synth` completes, no latch/blackbox for combinational problems, no unresolved `x`-driven outputs.
3. visible tests - fraction of the VISIBLE vectors where all outputs equal the reference (X in the reference = don't care; X in the candidate where the reference is defined = fail).
4. held-out tests - same on vectors the proposer never saw (separate seed; includes directed corner vectors from the problem). Reported separately so hard-coding the visible vectors shows.
5. formal      - equivalence to the reference: combinational = Yosys miter + SAT; sequential = miter + SymbiYosys (abc pdr / bounded) with reset sequence. Results: equivalent / counterexample / unknown (unknown never counts as pass).
6. quality     - only for formally equivalent designs: LUT count and logic depth from Yosys (`abc -lut K`, K an input), flip-flop count, optional nextpnr Fmax on a stated device (NOT RUN if the tool is missing),
                 reported as ratios to the reference design scored by the same pipeline (so "beat the human design" is meaningful).
Reward = shaped sum with explicit, user-tunable weights (RewardConfig); a design is "solved" only when stage 5 says equivalent.

## Anti-hacking
Held-out vectors; formal as the final judge; forbidden-construct scan; interface check; candidate may not read files; size cap on candidate text; a lookup-table heuristic is only a WARNING (formal decides).

## Search loop
Proposer protocol (callable prompt -> Verilog text) with adapters: scripted (tests), external command, Anthropic API (optional import, model from env/arg, cost/iteration caps).
Archive = JSONL of every attempt (code hash, per-stage result, score); best-so-far always kept. Structured feedback to the proposer: failing input vectors with expected/actual, synth stats,
score delta, critical-path depth, formal counterexample. Strategies: best-of-N refine, and population (keep top-k, mutate/combine). Budgets: iterations, candidates, wall-clock, tokens.

## Problem set (each = spec.md + reference.v + problem.json), references hand-written and cross-validated
sorting network (4x4-bit), popcount, constant multiplier (x*K), adder/subtractor, priority encoder, Gray counter, sequence-detector FSM, LFSR, SHA-256 round (hard). Metadata: kind (comb/seq),
clock/reset names, vector counts, seeds, "known best" numbers only when cited.

## Validation of the scorer itself (before it is trusted)
Equivalent-but-different designs pass formal; a one-bit-wrong design fails formal AND is caught by held-out tests while sometimes passing visible ones; a testbench-hard-coding design passes visible
and fails held-out/formal; latch/unsynthesizable designs fail stage 2; LUT counts checked against hand counts on tiny circuits; nextpnr Fmax labelled a tool estimate, not silicon.

## Phases
H1 problem format + simulator with failing-vector feedback. H2 formal equivalence. H3 metrics (Yosys LUT/depth/FF, nextpnr). H4 reward + anti-hack. H5 archive + proposers + loop + feedback.
H6 problem set + baselines. H7 CLI + docs + HIL note. Each phase: tests that can fail, commit, push.
