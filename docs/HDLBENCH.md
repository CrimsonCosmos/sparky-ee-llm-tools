# hdlbench - a verifiable reward and search loop for LLM-written logic circuits

`python -m hdlbench ...` (or the `hdlbench` script). Everything runs on a laptop with open-source tools: Yosys, Icarus Verilog, SymbiYosys (ABC engines), and optionally YoWASP nextpnr/icepack
(`pip install .[fpga]`). No FPGA board is needed; see "Physical board" below. Design rationale and phases: `docs/HDLBENCH_PLAN.md`.

## What a score means
One candidate Verilog file is scored against one problem (`spec.md` + golden `reference.v`) in gated stages, each earning part of the reward:

| stage | check | default weight |
|---|---|---|
| interface | ports (name/direction/width) equal the reference's, read by Yosys; no `$readmem*`/`$fopen`/`$system`/`include`/`force`; no reserved module names. Failing = reward 0 | gate |
| compiles | Icarus Verilog -g2012 | 0.05 |
| synthesizable | Yosys elaborates, no combinational loops (`check -assert`), no latches in combinational designs | 0.05 |
| visible tests | fraction of the VISIBLE vectors equal to the reference | 0.15 |
| held-out tests | fraction of vectors the proposer never sees (other seed, directed corner values; exhaustive for small combinational problems) | 0.25 |
| formal | proof of equivalence: combinational = Yosys miter + SAT (4-state, reference X = don't care); sequential = SymbiYosys with ABC PDR from the reset state (a bounded check earns a quarter of the weight and is NOT a proof) | 0.40 |
| quality | only for proved designs: LUT4 count, LUT levels, flip-flops, nextpnr Fmax (sequential only), relative to the reference scored by the same pipeline; equal to the reference = half of the weight, 2x better = all | 0.10 |

`solved` = all tests pass AND the formal stage proved equivalence (`Score.solved_by == "formal proof"`). Imported problems without a reset (VerilogEval sequential ones) are judged by tests only and say
`tests only (NOT proved)`. Weights are reward-shaping choices in `RewardConfig`, not physical values.

## Anti-hacking
Held-out vectors are never put in a prompt or feedback (only their pass fraction); feedback lists failing VISIBLE vectors and formal counterexamples. A design that hard-codes the visible vectors passes the visible stage and
loses the held-out and formal stages (tested). The formal proof is the final judge, so a rare-input bug that tests miss is still refuted (tested: 1 failing input in 131,072). A lookup-table-looking design only raises a warning.

## Validation of the scorer (why it can be trusted, and how it was found wrong once)
* Reference designs are checked against independent Python models; the SHA-256 round model is checked against `hashlib` through a full compression.
* Mutation testing: for several problems every single-edit mutant's formal verdict and full score must agree with EXHAUSTIVE simulation (`tests/test_hdlbench_validation.py`). This found a real hole: without
  `-enable_undef` Yosys' SAT ignored X semantics, so a `prienc8` with one wrong branch was called equivalent because the reference has an X default. Fixed, with a regression test.
* Quality numbers are checked against circuits with hand-countable LUT costs (a 4-input function is one LUT4; a 5-input XOR needs two) and ordering facts (deeper logic = lower Fmax).
* nextpnr numbers are the tool's timing-model estimates (labelled so), and pad-to-pad delay of combinational designs is NOT reported because it depends on arbitrary pin placement.

## Commands
```
hdlbench list | tools | baseline [problems]               # reference designs scored by the same pipeline
hdlbench score <problem|dir> design.v [--json]             # exit code 0 = solved
hdlbench run <problem> --proposer anthropic --model <id> --iterations 10 --candidates 4 --strategy population --archive runs/x.jsonl
hdlbench run <problem> --proposer command:"my-model-cli" ...   # prompt on stdin, Verilog on stdout
hdlbench mutate-baseline <problem>                         # LLM-free search baseline
hdlbench import-verilogeval <dataset_spec-to-rtl dir> <dest> # NVlabs/verilog-eval problems re-scored with our held-out + formal pipeline
hdlbench bitstream <problem> design.v --out x.bin --device hx8k --package ct256 [--pcf pins.pcf] [--load]
```
The Anthropic proposer needs `pip install anthropic`, `ANTHROPIC_API_KEY`, and an explicit model id (`--model` or `HDLBENCH_MODEL`); without them the run stops with "proposer unavailable", never silently.
Budgets: iterations, candidates, seconds, tokens. Every attempt is appended to the JSONL archive (code, stage results, cost); the best design is never lost.

## Built-in problems
add8, popcount8, sort4 (4x4-bit sorting network), mulc45 (constant multiplier), prienc8 (with don't-care), gray4, lfsr8, seqdet (sequential), sha256_round (hard; formal proof of a re-associated
version takes about 30 s). Add your own: a directory with `spec.md`, `reference.v`, `problem.json` (`top`, `kind`, `clock`, `reset`, vector counts, seeds).

## Physical board (final sanity check only)
Logic correctness is decided by simulation + formal proof; resource counts come from synthesis; Fmax is a tool estimate. A board adds measured clock speed, power, and real I/O. `hdlbench bitstream`
refuses unsolved designs, builds Yosys -> nextpnr-ice40 -> icepack, and loads through `openFPGALoader`/`iceprog` if present (otherwise "NOT RUN"). Board facts (device, package, pin constraints) are inputs.
Bitstream generation is tested (round trip through `iceunpack`); loading onto hardware has NOT been exercised here (no board).

## Known limits
* Sequential designs are proved from the reset state, with reset asserted for the first cycles and released for good; designs relying on reset pulses mid-run are not compared there.
* The reward's formal stage can time out (`unknown`), which earns nothing; large arithmetic with very different structure may need a longer `formal_timeout`.
* iCE40 is the cost model (LUT4); other architectures need another `Target`.
* First LLM runs (a subagent as proposer, 9 problems): docs/HDLBENCH_EVAL.md. The direct Anthropic API path is still untested (no API key here); the loop itself is tested with scripted proposers.
* Benchmarks measured by others (VerilogEval, RTLLM, CodeV-R1, VeriThoughts) report different numbers across versions; none of those numbers are reproduced here.
