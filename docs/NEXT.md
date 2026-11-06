# Next steps (written before a session compaction; source of truth for the resume)

State: repo = https://github.com/CrimsonCosmos/sparky-ee-llm-tools (main), local clone /home/user/sparky-ee-llm-tools. KiCad 10.0.8 installed (kicad-cli, pcbnew);
ngspice 42; java; Freerouting jar cached under $SPARKY_HOME/tools. Package is still named `sparky` (entry points `forge`, `sparky`). Push to sparky without being asked.
Working rules from the owner: no sub-agents; do the work in this session; Python only; console app (Win/macOS/Linux); adopt open-source tools instead of writing them;
minimal LLM use (system plan + part research on a miss); fast scoped tests on push, integration only manual/nightly; design before code; loose coupling through files;
keep going until the limitations list is empty (do not just report limitations).

## Order of work
1. KiCad 10 native backend: use `kicad-cli pcb drc` / `sch erc` / `export ipc2581|odb|step|svg|pdf` (gate with toolchain.kicad_has). Delete our DRC, Gerber/Excellon/KiCad-file writers, SVG renderer.
2. Circuit model spike (1 day, written result in docs/): SKiDL vs atopile vs circuit-synth; pick one; port blocks; delete our Circuit/Component/Net and brittle contracts.
3. Part picker on jlcparts DB (yaqwsx.github.io/jlcparts/data/cache.zip, multi-part zip; verify schema) + KiCad libs; delete hand-written seed catalogue and hand-coded part params.
4. Router: Freerouting only (finish integration, benchmark vs builtin once), then delete grid router, plane/pour fill, stitching, our footprint generators.
5. Placement spike: benchmark keep-ours vs open-source option on 3 boards behind a file contract; decide and delete the loser.
6. Verification: ngspice testbenches per block family (replace equation-only trust), mutation corpus for false positives/negatives, datasheet reference check, EE rules only where KiCad/ngspice cannot express them; coverage always reported.
7. Thermal (Elmer FEM) and EMI (openEMS) adapters; mechanical floor plan (default small, roughly square outline; STEP export; FreeCAD/CadQuery later).
8. Remaining limitations to close: MCU pinouts and alternate functions come from the KiCad symbol libraries (STM32 included): no per-part verification work, just map pin-mux from those symbols; distributor links & live prices (APIs with keys), BGA/diff-pair/length matching via Freerouting+KiCad rules, doubled-sided assembly, scope beyond ten blocks (parts research + SKiDL-based custom circuits).
9. Hygiene: rename package to `sparky`, dependency pins from docs/DEPENDENCIES.md (newest versions), keep fast suite under 10 s, CI scoped by changed files.

## Status update (picker)
* DONE: `sparky/picker/` = deterministic parametric picker over CDFER/jlcpcb-parts-database (SQLite ~400 MB, 495k in-stock parts, ~330k with parametric attributes; LCSC SKU, stock, price tiers, datasheet).
  Recipes (`picker/recipes.py`) hold only the catalogue's attribute vocabulary. CLI: `sparky pick <recipe> k=v ...`, `sparky download-parts`. Data path: $SPARKY_PARTS_DB or <data_dir>/jlcpcb-components.sqlite3.
* TODO picker: TVS polarity filter (unidirectional for DC rails); footprint mapping (catalogue package -> KiCad footprint via KiCad library search); IC recipes via datasheet; SQL prefilter for slow categories (inductors ~3 s).
* TODO: delete the hand-written seed catalogue (`parts/seed.py`, `parts/generic.py`, `parts/db.py`) once blocks are ported to equations+SKiDL+picker (the old blocks still depend on it).

**Decision (user):** LLM/agent auto-purchasing is out of scope. The deliverable is the BOM + shopping list/cart CSVs with links; the user buys. Remove `purchase/agent.py` when the purchase module is ported.
