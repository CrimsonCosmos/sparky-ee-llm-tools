# Status and next steps

DONE: KiCad-native DRC/export; Freerouting; SKiDL circuits (design.py bridge); recipes (ldo, buck + ngspice check, battery_input, can_node, mcu_core,
connector_breakout, mounting_holes); generic resolver; catalogue-based BOM/shopping list; footprints from KiCad libs only; hand-written block library,
parts catalogue, footprint generators, templates, purchase agent deleted; thermal adapter (pcb-analysis, needs native kernels) and IR-drop adapter (kicad-irdrop) written.

## Next (in order)
1. Wire analysis into the build + report: kicad-irdrop per power rail (hand-calculable test R = rho*L/(w*t)); thermal (uniform-slab test dT = P/(h*A)); EMC via pcb-analysis
   (needs switching currents/frequencies from the recipes' calcs). Then delete `pcb/analysis.py` heuristics and the rule-based EMI checks.
2. Dependencies: pin versions in docs/DEPENDENCIES.md / pyproject; document the pcb-analysis native build for Windows/macOS.
3. Recipes to author from datasheet reference designs when needed (motor driver, USB, IMU...): Claude writes them with `sparky_write_recipe`; not hand-ported.
4. Fast suite under 10 s; CI scoped by changed files (tools/scoped_tests.py).
5. Later: BGA, diff-pair/length matching (Freerouting + KiCad rules), double-sided assembly.

Out of scope (marked in code): mechanical integration (`mechanical.py`), live distributor data and purchasing (`sourcing.py`).
Owner's rules: no sub-agents; Python only; console app on Win/macOS/Linux; adopt open source; minimal LLM use; fast scoped tests; no hardcoded values; no confidently wrong tests.
