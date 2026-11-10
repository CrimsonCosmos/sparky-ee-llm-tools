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
