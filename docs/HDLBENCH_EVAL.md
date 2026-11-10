# hdlbench: first runs with an LLM proposer

Setup: a Claude subagent (same model as the supervising session) acted as the proposer. It could read only `spec.md` for each problem and call `score.sh`, which runs `python -m hdlbench score <problem> <file> --log ...`
(simulation on hidden vectors, formal equivalence, Yosys/ABC LUT4 cost). Every attempt is logged with its code in `docs/hdlbench_eval/{pilot,long}/<problem>.jsonl`. The subagent's own account of what it read is the only evidence
that it did not open the golden designs - nothing technically prevented it. The feedback shows cost ratios to the golden design, so the target is visible by design (`LoopConfig.show_reference_cost`).

## Results (one run, at most 15 scored attempts per problem; the long run used 36 attempts in total)
| problem | attempts | best reward | best LUT4 / FF / depth | golden |
|---|---|---|---|---|
| add8 | 1 | 0.950 | 8 / 0 / 6 | 8 / 0 / 6 |
| prienc8 | 3 | 0.980 | 7 / 0 / 2 | 11 / 0 / 3 |
| seqdet | 3 | 0.981 | 3 / 3 / 1 | 3 / 7 / 2 |
| lfsr8 | 2 | 0.950 | 3 / 8 / 1 | 3 / 8 / 1 |
| mulc45 | 7 | 0.977 | 19 / 0 / 8 | 48 / 0 / 8 |
| popcount8 | 6 | 0.950 | 10 / 0 / 4 | 10 / 0 / 4 |
| gray4 | 5 | 0.959 | 6 / 4 / 1 | 8 / 4 / 1 |
| sort4 | 4 | 0.953 | 71 / 0 / 5 | 48 / 0 / 9 |
| sha256_round | 5 | 0.949 | 468 / 0 / 26 | 469 / 0 / 25 |

All nine problems were SOLVED with a formal proof. The attempt logs were re-read by me and match the agent's report. I re-checked the best designs independently of the scorer: exhaustive simulation of all 256 inputs for
mulc45 / prienc8 / popcount8, 20,000 random vectors for add8 / sort4, and 5,000-cycle random runs against independent Python models for gray4 / seqdet / lfsr8 - all agree.

Wins over the golden design: mulc45 (x*45 as 5x then 9x with two shift-adds: 19 LUT4 vs 48), prienc8, seqdet (smaller state encoding), gray4 (Gray next-state equations written directly). One failure mode seen: a design that
simulates to X because of simulator-sensitive coding was correctly rejected (sort4, attempt 2).

## What this does and does not show
* It shows the loop works end to end with a real model: correct designs are recognised, wrong ones are refuted with usable feedback, and cost improvements are detected and verified.
* It does NOT measure model capability: one run, one agent, nine small problems of the textbook kind, with the golden cost visible. Treat it as a smoke test, not a benchmark result.
* The reward is flat (0.95 = equal to golden; the quality term is 10% of the total and caps at 2x better), and LUT count and depth are averaged with equal weight, so on sort4 71 LUT at depth 5 (0.953) outscored 48 LUT at depth 9 (0.950).
  If you want to optimise area or speed specifically, set the weights in `RewardConfig` / change the metrics averaged in `score.evaluate`.
* LUT4 counts exclude carry cells (SB_CARRY): adders that use the carry chain look cheap in LUTs (add8: 8 LUT4 + 8 SB_CARRY, one logic cell each on iCE40). A fairer unit would be logic cells.
* Yosys re-encodes FSMs; the same machine written two ways can differ in FF count (seen on seqdet).
