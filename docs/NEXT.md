# Status and next steps

DONE: KiCad-native DRC/export; Freerouting; SKiDL circuits (design.py bridge); recipes (ldo, buck + ngspice check, battery_input, can_node, mcu_core,
connector_breakout, mounting_holes); generic resolver; catalogue-based BOM/shopping list; footprints from KiCad libs only; hand-written block library,
parts catalogue, footprint generators, templates, purchase agent deleted; thermal adapter (pcb-analysis, needs native kernels) and IR-drop adapter (kicad-irdrop) written.

DONE since: thermal (pcb-analysis FEM) in every board package with invariant tests; IR drop (kicad-irdrop) in every package, validated against R = L/(sigma w t);
EMC adapter (pcb-analysis dipole superposition) validated against Ott's small-loop formula to 0.1 dB; heuristic thermal/EMI code deleted.

## Decisions
* PyOpenMagnetics is NOT adopted: recipes pick catalogue inductors by inductance/Isat/package from the open JLCPCB data (what a designer buys). PyOpenMagnetics designs custom
  wound magnetics (cores, windings), which is a different job; if custom magnetics are needed, a recipe can call it.
* The old hand-written motor driver was not ported: buy it (buy-vs-build policy) or have Claude author a recipe from a vendor reference design.

## Next (in order)
1. DONE: EMC in the flow via pcb-analysis' network model (copper port network + input capacitors + harmonic source), validated against Ott's formula within 2 dB; a buck declares its HotLoop only when a SOURCED switch-node edge time (`t_rise`) is stored (the TPS563201 datasheet has none), otherwise the report says NOT RUN. Capacitor ESR/ESL are typical Policy values. Open: other loops (SW node, output), cables, enclosure, spectrum from a SPICE waveform instead of the trapezoid envelope.
2. Thermal needs package theta_JA / enclosure: only copper spreading + convection is modelled; report says so.
3. Recipes Claude should author from datasheet reference designs when needed (motor driver, USB, IMU...). The old hand-written 3-phase driver was deliberately not ported.
4. Dependency pins are in pyproject; document prebuilt wheels / setup for the pcb-analysis kernels on Windows/macOS.
5. Fast suite ~11 s (target 10 s); CI scoped by changed files (tools/scoped_tests.py).
6. DONE (needs KiCadRoutingTools): diff pairs, length groups, BGA/QFN fanout, re-measured independently. KiCad's own DRC (a `.kicad_dru` skew rule per constraint, read by the router too) is the authority; with it the bus equalised (spread 0.42 mm vs 1.0 mm). Open: via-in-pad needs `fab_tier='advanced'` + rule_overrides; (impedance-controlled single-ended nets and pairs: DONE, router solves widths from our stackup, single-ended checked by IPC-2141 within tolerance, differential reported NOT VERIFIED); blind/buried vias (HDI) are not supported: high-layer boards use plated through-holes, with thickness and drill derived from the dielectric minimum and aspect-ratio limits (32 layers: 3.7 mm, 0.37 mm drill, DRC-clean), and plane assignment keeps every inner signal layer next to a reference plane.
7. Done: two-sided assembly (`constraints.Side`, `Rules.assembly_sides`). Parts flip about the Y axis; the placer lets opposite faces overlap (through-hole parts occupy both); pad geometry is checked against pcbnew, the CPL against `kicad-cli pcb export pos`, and a back-side heater against the front in the thermal model. Not covered: assemblers' own bottom-face rotation offsets (verify polarised parts with the fab), paste/glue process limits for the second reflow, bottom radiation in the thermal model (convection only), via-in-pad between faces.

Out of scope (marked in code): mechanical integration (`mechanical.py`), live distributor data and purchasing (`sourcing.py`).
Owner's rules: no sub-agents; Python only; console app on Win/macOS/Linux; adopt open source; minimal LLM use; fast scoped tests; no hardcoded values; no confidently wrong tests.

## KiCad-parity pass (this session)
Added as adapters (see docs/DEPENDENCIES.md): ERC + parity, review/test/mechanical exports, generic SPICE, scikit-rf lines, iBOM, KiKit (not runnable here). Own code: fiducial/test-point/corner-radius/silk-title/stitching options in `BoardSpec`. Found and fixed on the way: KiCad >= 9 files name nets on items, so outer pours were silently never added (now added and tested); catalogue download failed on compressed transfers; CPL rotation did not match `kicad-cli pos`.
Open: teardrops (no API), hierarchical/multi-sheet schematics and drawn wires/power symbols (the generated schematic uses global labels on every pin: correct but not pretty), KiKit verification, a `sparky simulate` CLI.

Second pass done: bottom radiation, junction temperature from datasheet theta_JB, JLCPCB placement corrections, differential impedance via atlc. Still open: blind/buried vias, cable/enclosure EMC, enclosure thermals (needs a user STEP), via-in-pad between faces, paste/glue limits for the second reflow (fab process data).

HDI (blind/buried/micro vias): built via Freerouting DSN padstacks + KiCad typing/DRC + independent span checker (docs/HDI.md). Works and is DRC-clean; routing benefit over through-only NOT demonstrated (control run equal). Open: fab-specific span tables, stacked-micro-via rules, via-in-pad under BGAs, a board where through vias genuinely cannot fit (needs via-in-pad), combination with KiCadRoutingTools constraints.

HDI follow-up done: staged KRT+Freerouting verified clean; via-in-pad built and measured (39 -> 2 unconnected on a 0.5 mm BGA); FAB_NOTES.md. Still the fab's: manufacturability review, span table, filling/capping, stacked-via rules.

Byte groups, delay matching, inner-layer stripline impedance, loss budgets, frequency-dependent dielectric and back-drilling added as integrations (see docs/GPU_GAP_ANALYSIS.md update). Silicon-flow integration plan: docs/SILICON_INTEGRATION_PLAN.md.

Silicon flow status: Phases 0-5 done (toolchain without Docker, LibreLane runner, cross-checks, sign-off re-runs, Verilator lint + RTL-vs-gate-level equivalence, power/IR/skew analyses, package + REPORT.md). Verified on an 8-bit counter on sky130A only (gate-level sim macros for gf180/ihp not yet verified). Equivalence is random-vector, not formal; a single-gate mutation went undetected by 300 cycles. Open: Phase 6 board glue; Phase 7 scale ladder (Borg needs Chisel/mill to generate Verilog - not run, see below), cocotb harness, die thermal analysis.

Scale ladder (4 cores, 15 GB, sky130A, LibreLane 3.0.17): counter ~70 s; PicoRV32 (RV32I) 17 min, full sign-off clean, gate-level equals RTL (400 random cycles, reference-X = don't-care). Borg core (`BorgOnlyCore`, 51 generated modules, Chisel via Mill 1.3.1) lints without errors. Flat Yosys/ABC synthesis was OOM-killed (12.5 GB, after 2.5 h) in one huge combinational cone; with `SYNTH_HIERARCHY_MODE=deferred_flatten` synthesis completes: 379,908 instances, 4.47 mm2 of sky130_fd_sc_hd cell area, 62,806 sequential cells. Pre-placement STA at a 125 ns clock with a plain single-clock SDC reports worst setup slack -65 ns (an estimate before CTS/routing; Borg's own constraints/multicycle paths were not used, so this is NOT a statement about Borg's timing). The flow then died in global placement on 'disk full': each LibreLane step keeps a ~0.7-1.1 GB ODB copy (4.9 GB after 24 of ~55 steps), more than this session's disk allowance. So on this machine: synthesis of a 380k-cell design is feasible only hierarchically; full P&R needs more disk (estimate >10 GB, not measured). Borg's own tapeout target is gf180 3.3 V with a padring; this run is the core only on sky130A, so it is a scaling probe, not a tapeout check.
