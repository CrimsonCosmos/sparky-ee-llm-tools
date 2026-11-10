# Shrinking the GPU holes with open-source code (loop plan)

Starting point (see the session's honest assessment): the repo can take small blocks from spec to verified RTL and small designs through the open RTL-to-GDS flow, but it is far from GPU scale.
Rule for this loop: every hole is addressed by ADOPTING existing open-source software/IP through a thin adapter, and each adapter is validated by a result that could have failed (deliberately broken input, independent reference, or a measured number). Where a hole cannot be closed with open source it is recorded as such.

| # | hole | open-source candidate (verified to exist) | integration | validation |
|---|------|--------------------------------------------|-------------|-----------|
| 1 | flat flow runs out of memory/disk beyond ~100k cells | LibreLane macro/hierarchical flow (`MACROS`, `DESIGN_IS_CORE=false`), OpenROAD macro placement | `silicon/hier.py`: harden sub-blocks as macros, then the top with them | two hardened blocks inside a top: DRC/LVS/timing clean; compare memory/time against flat |
| 2 | memories | OpenRAM-generated SRAM macros shipped in the sky130 PDK (`sky130_sram_macros`), DFFRAM (Apache-2.0), OpenRAM (BSD-3) | `silicon/macros.py`: macro views from the PDK (LEF/GDS/Liberty/Verilog) into a flow | design with an SRAM macro through the flow, LVS/DRC clean, read/write checked in simulation |
| 3 | verification depth: no coverage, ad-hoc stimulus | Verilator `--coverage` (+ `verilator_coverage`), cocotb (BSD-3) | `hdlbench/coverage.py`: line/toggle coverage of the golden design under the held-out stimulus; cocotb adapter in `silicon/` | coverage numbers on built-in problems; a deliberately weak stimulus must show LOW coverage |
| 4 | no ISA-level formal verification for CPU-like blocks | riscv-formal (YosysHQ, ISC), PicoRV32's RVFI wrapper | `silicon/riscv_formal.py` driver (sby) | PicoRV32 passes the instruction checks it supports; a mutated PicoRV32 FAILS |
| 5 | no real open GPU RTL pushed through the flow | Vortex (Apache-2.0), Borg | synthesize/scale-probe a Vortex sub-unit hierarchically; record cells/memory/time | measured numbers in docs, compared with the flat result |
| 6 | DFT (scan) | OpenROAD DFT commands (`scan_replace`, `insert_dft`) in the installed OpenROAD; AUCOHL/Fault (Apache-2.0) | check availability; adapter if present | scan chain inserted on the counter, chain length equals flop count |
| 7 | analog/IP blocks (PLL, SerDes, HBM PHY) | OpenFASOC generators (Apache-2.0), sky130 analog IP | catalogue only unless a generator runs here | recorded honestly as NOT closed by this repo |
| 8 | software stack | Vortex's own runtime/compiler, Mesa | out of scope for this repo (recorded) | - |

Loop: pick the next hole, research -> adopt -> validate -> commit -> update this table's status column in `docs/NEXT.md`. Stop conditions: all rows adopted or recorded as blocked with the reason. Resource limits of this machine (15 GB RAM, ~7 GB disk) bound what can be measured here.

## Status after the first pass of the loop
| # | hole | status | evidence |
|---|------|--------|----------|
| 1 | hierarchical flow with macros | DONE | `silicon/macros.py`, `tests/test_silicon_hier.py`: hardened counter used twice in a parent; DRC/LVS/STA sign-off clean with independent re-runs (incl. hierarchical STA that matches the flow exactly); overlapping macros rejected. Not yet measured at large scale on this machine. |
| 2 | memories | DONE with a limit | `macros.from_pdk` + `openram_flow_extra`, `tests/test_silicon_sram.py`: OpenRAM 1 KB SRAM from the PDK inside a parent through the flow. The flow's own DRC/LVS cannot run on OpenRAM GDS (Magic limitation): KLayout DRC is re-run on the streamed GDS (can fail: injected violation detected), LVS is NOT RUN. Macro Liberty has one corner only; its 0.04 ns input limit needed a documented relaxation. |
| 3 | verification depth | DONE (coverage); cocotb not added | `hdlbench/coverage.py`, `tests/test_hdlbench_coverage.py`: Verilator line/branch/toggle coverage of a design under a problem's stimulus (e.g. rv32i_exec held-out: 100% line/branch, 94% toggle; visible set 90%/91%/92%). Existing gate-level equivalence and Icarus flows already cover what cocotb would. |
| 4 | ISA-level formal verification | BLOCKED | `silicon/riscv_formal.py` written against riscv-formal's documented interface, never run: executing the cloned repo's generator was denied by the session's permission check. Needs the user's approval to run that code. |
| 5 | real open GPU through the flow | BLOCKED | Vortex was cloned (read only) but its RTL needs generated headers (`VX_config.vh`, `VX_types.vh`) produced by a repo script; running it was not attempted after the denial above. Borg (generated via Mill, which the user approved) was run earlier: synthesis feasible only hierarchically, P&R memory-bound at 15 GB. |
| 6 | DFT (scan) | DONE for scan insertion | `silicon/dft.py`, `tests/test_silicon_dft.py`: OpenROAD `set_dft_config`/`scan_replace`/`execute_dft_plan` on the counter: 8 flops, 1 chain; chain traced in the netlist; gate-level shift test passes; a cut chain fails it. ATPG/fault coverage (AUCOHL/Fault, OpenROAD has no ATPG) NOT done. |
| 7 | analog IP (PLL, SerDes, HBM PHY) | NOT CLOSED | OpenFASOC (Apache-2.0) generators exist for some blocks; nothing integrated or run here. HBM/SerDes PHYs are not available as open IP at GPU-class speeds. |
| 8 | software stack | OUT OF SCOPE | Vortex's own runtime/compiler; Mesa. |
