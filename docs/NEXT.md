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
1. EMC in the flow: each recipe must declare its switching-current model (hot-loop terminals + harmonic amplitude from fsw/rise time/duty) so the adapter can run per board;
   the proper tool for parts joining nets is pcb-analysis' `BoardNetwork`/`network_emission` (the adapter's single straight return element is the simple case).
2. Thermal needs package theta_JA / enclosure: only copper spreading + convection is modelled; report says so.
3. Recipes Claude should author from datasheet reference designs when needed (motor driver, USB, IMU...). The old hand-written 3-phase driver was deliberately not ported.
4. Dependency pins are in pyproject; document prebuilt wheels / setup for the pcb-analysis kernels on Windows/macOS.
5. Fast suite ~11 s (target 10 s); CI scoped by changed files (tools/scoped_tests.py).
6. Later: BGA, diff-pair/length matching (Freerouting + KiCad rules), double-sided assembly.

Out of scope (marked in code): mechanical integration (`mechanical.py`), live distributor data and purchasing (`sourcing.py`).
Owner's rules: no sub-agents; Python only; console app on Win/macOS/Linux; adopt open source; minimal LLM use; fast scoped tests; no hardcoded values; no confidently wrong tests.
