# hdlbench difficulty ladder (single sequential subagent, one problem at a time)

Protocol: one subagent, problems given one after another, each only after the previous was solved; at most 20 scored attempts per problem; stop at the first failure. The agent is told not to exploit the scorer
and to report weaknesses instead. Because its goals may differ from ours, every level is checked by me, not taken from its report:
1. its best logged design is re-scored in a FRESH process with the default config (`/home/user/eval/verify.py`);
2. the same circuit is checked against an INDEPENDENT model (binascii, Python shifts, exhaustive division, an independently built AES S-box table) - not the scorer;
3. its tool calls are scanned for access to the references/scorer source (`/home/user/eval/audit.py`; file reads cannot be technically prevented, so this is an audit, not a guarantee).
Attempt logs (with code): `docs/hdlbench_eval/ladder/<problem>.jsonl`. Scorer hardening done before the ladder (reset obeyed at any time, no initial values, non-LUT primitives get no quality credit) came from asking
how a proposer with a different goal could cheat.

| level | problem | attempts | solved | best reward | best LUT4 / depth | golden LUT4 / depth | independent check | audit |
|---|---|---|---|---|---|---|---|---|
| 1 | crc16 (CRC-16/CCITT byte update) | 1 | yes | 0.950 | 20 / 2 | 20 / 2 | pass (binascii.crc_hqx) | clean |
| 2 | shift32 (4-mode 32-bit shifter) | 2 | yes | 0.966 | 302 / 7 | 477 / 6 | pass (6000 random vs Python) | clean |
| 3 | div8 (8-bit divider, b=0 defined) | 2 | yes | 0.950 | 49 / 31 | 49 / 29 | pass (all 65,536 inputs vs Python) | clean |

Level 3 note: the design is a restoring array divider = the golden design's cost. The agent suspected the 49-LUT figure hides carry logic; I confirmed it: the mapped design is 49 LUT4 + 75 SB_CARRY cells.
LUT4 counts therefore under-state adder/comparator-heavy designs (carry cells are not counted). A logic-cell count after packing would be fairer; the ladder keeps the original metric so levels stay comparable.
| 4 | sbox (AES S-box) | 2 | yes | 1.000 | 315 / 5 | 877 / 26 | pass (all 256 entries vs an independently built table) | clean |

Level 4 notes: attempt 1 was the algebraic x^254 design (770 LUT4, 0.954); attempt 2 a 256-way case table that the agent generated with its own throwaway script from the spec's definition (checked against published
FIPS-197 entries). That is a legitimate solution for an 8-input function, formally proved and checked exhaustively; the reward saturates at 1.0 only because my golden design (algebraic square-and-multiply, 877 LUT4) is a weak baseline,
not because the scorer was fooled. The scorer printed its lookup-table warning, as designed. Lesson: a golden design that is far from the best known makes the reward cap trivial; compare against a stronger baseline (e.g. a composite-field S-box).

## Scorer limits found while preparing level 5 (sequential multi-cycle multiplier, mul8s)
* The unbounded sequential proof (ABC PDR) cannot decide equivalence of two different shift-add multiplier architectures (120 s timeout). The SMT bounded check (z3) takes 0.4 s at depth 10, 109 s at depth 14, >300 s at depth 18.
* So mul8s is judged by a BOUNDED proof (12 cycles after a 1-cycle reset, i.e. one complete multiplication for ALL operand values) plus 300,000 held-out simulated cycles with mid-run resets. `solved_by` says
  "bounded check ... NOT an unbounded proof". A bug seen only after two completed operations would not be caught by the proof; the simulation is the only guard there.
* With a 3-cycle reset a rare-operand-pair bug (a=201,b=77) was NOT caught (depth too short to see the first result; random simulation hit 22k operand pairs of 65,536): scorer accepted a wrong design. With reset_cycles=1 the bounded proof sees it.
  Rule recorded in the problem metadata: formal_depth >= reset_cycles + latency + 1.
