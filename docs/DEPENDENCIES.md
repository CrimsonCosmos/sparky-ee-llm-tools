# Dependencies (newest versions as of 2026-11-06; "unverified" = could not be confirmed from this environment)

## Runtime (Python ≥ 3.13)
| Package | Version | Use |
|---|---|---|
| numpy | 2.5.4 | numeric core |
| shapely | 2.2.0 | geometry (placement/DRC-side helpers) |
| kiutils | 1.4.8 | read/write KiCad files |
| kicad-python (kipy) | 0.8.0 | KiCad IPC API (KiCad ≥ 9) |
| skidl | 3.0.0 | circuit objects → KiCad (adoption pending spike) |
| pint | 0.26.2 | units |
| sympy | 1.14.0 | symbolic design equations |
| pydantic | 2.14.3 | schemas for spec.json / LLM output |
| requests | 2.34.2 | downloads (datasheets, parts DB) |
| typer / rich / platformdirs | 0.28.0 / 15.0.0 / 4.14.5 | console UX, cross-platform paths |
| mcp | 2.3.0 | optional MCP server |
| anthropic | 2.6.0 | optional API backend (default: headless `claude` CLI) |
## Dev
| pytest 9.2.0 · gerbonara 1.6.3 (independent gerber check) |
## External programs (invoked, not bundled)
| Program | Version | Notes |
|---|---|---|
| KiCad | **10.0.8** (installed here, from the official PPA) | `kicad-cli`; Windows/macOS installers, Linux PPA/Flatpak |
| Freerouting | 2.1.0 verified (newest unverified) | needs Java 21+; jar fetched with SHA-256 check |
| ngspice | upstream latest (installed here: 42) | batch mode |
| Elmer FEM | unverified | thermal, optional |
| openEMS | unverified (not in Ubuntu repos) | EMI, optional |
| FreeCAD / CadQuery | unverified / 2.8.0 | mechanical, roadmap |
| Claude Code CLI | installed | only for the two LLM call sites |
## Data
| jlcparts DB (yaqwsx.github.io/jlcparts/data/cache.zip, multi-part zip, ~12.7 MB first part verified reachable) · KiCad libraries (ship with KiCad) |

## Analysis (open source)
| Tool | Use | License | Notes |
|---|---|---|---|
| kicad-irdrop | DC IR drop on real copper | Apache-2.0 | `pip install` into the interpreter that can import pcbnew |
| pcb-analysis 0.12.1 | thermal FEM, EMC (CISPR 32/FCC) | MIT | beta. **Needs its C++ kernels**: without them rasterising is pure NumPy (80 s at a 1 mm grid, ~20 min at 0.25 mm); with them 0.3 s. Build: clone AFLOY/pcb-analysis at the tag, `pip install pybind11`, `cmake -S . -B build/native -G Ninja -Dpybind11_DIR=$(python -m pybind11 --cmakedir)` and `cmake --build build/native`; the build FetchContents SuperLU 7.0.1 and pocketfft (pass `-DFETCHCONTENT_SOURCE_DIR_SUPERLU=` / `_POCKETFFT=` to use local clones if tarball downloads are blocked). Needs cmake, ninja, a C++17 compiler (OpenMP). Heavy install on Windows/macOS: expect to ship prebuilt wheels or document it. |

## Notes (current)
* Python >= 3.13. Versions are pinned in `pyproject.toml` (numpy 2.3, shapely 2.2, skidl 3.0.0, pint 0.26, platformdirs 4.14; dev: pytest 9, gerbonara 1.6.3, kiutils 1.4.8).
* KiCad >= 8 (developed against 10.0.8): `kicad-cli` and its `pcbnew` Python module. Freerouting 2.1.0 (SHA-256 pinned, auto-downloaded) needs Java.
* ngspice (external process) for the recipe power-stage checks.
* `kicad-irdrop` (Apache-2.0) must be installed into the **KiCad Python** (the interpreter that imports pcbnew): `<kicad-python> -m pip install git+https://github.com/firedanceio/kicad-irdrop`.
* `easyeda2kicad` 1.0.1 is AGPL-3.0: only ever run as an external command by `libsrc` (flagged-unreviewed fallback; the EasyEDA API returned HTTP 403 after a few requests, so imports are cached).
* pcb-analysis (thermal + EMC) needs its compiled kernels; without them rasterising is pure NumPy (80 s at 1 mm, ~20 min at 0.25 mm) and `available()` returns False.

## Constraint-aware routing (optional): KiCadRoutingTools
MIT, https://github.com/drandyhaas/KiCadRoutingTools (needs KiCad 9/10 files; numpy, scipy, shapely; Rust router). Used for boards that carry differential pairs, length-match groups or BGA/QFN
fanout (`constraints.DiffPair / LengthGroup / Fanout`, declared by recipes in `Result.constraints`); other boards keep using Freerouting. Install:
`git clone https://github.com/drandyhaas/KiCadRoutingTools <data_dir>/tools/KiCadRoutingTools && python build_router.py` (downloads a prebuilt binary or builds with cargo), or point `SPARKY_KRT` at a checkout.
Our `Rules` are passed to it as its fab-floor file (so its via/annular/track/clearance limits equal ours); `Rules.fab_tier='standard'` forbids via-in-pad and its own escalation.
Each constraint becomes a named KiCad custom rule (`.kicad_dru`, `sparky_<i>`): the router reads it and KiCad's own DRC (`skew_out_of_range`) is the authoritative verdict; we also measure the copper (tracks + via barrels) as a cross-check (it differs from KiCad by a few hundredths of a mm). Without the rule file the tool graded its own different metric and left the bus unmatched.

## Optional analysis/routing tools per platform
Only Linux has been exercised (every line in the table below that says "not tried" has NOT been run; treat it as the expected route, not a result). `sparky doctor` shows which of these are usable.

| Tool | Linux (verified) | macOS / Windows (not tried) |
|---|---|---|
| pcb-analysis compiled kernels | cmake + ninja + g++ (OpenMP) as above | Same CMake build: macOS needs `brew install cmake ninja libomp` and Apple clang with `-Xpreprocessor -fopenmp`; Windows needs the MSVC Build Tools (OpenMP is built in) and `cmake -G Ninja` from an "x64 Native Tools" prompt. No prebuilt wheel exists upstream; until one does, `available()` stays False and the thermal/EMC report sections say NOT RUN. |
| KiCadRoutingTools Rust router | `python build_router.py` (cargo) | `build_router.py` also downloads a prebuilt binary for some platforms (see its README); otherwise `rustup` + `cargo build --release`. Without it boards with DiffPair/LengthGroup/Fanout/ControlledImpedance constraints are not routed (reported, not silently ignored). |
| kicad-irdrop | `pip install` into KiCad's Python | KiCad's bundled Python (`SPARKY_KICAD_PYTHON` points at it) |
| ngspice | distro package | `brew install ngspice` / ngspice installer; put it on PATH |

A tool that is missing never produces a passing result: its report section reads NOT RUN with the reason.

## KiCad and open-source companions we call (adapters only; nothing re-implemented)
| Need | Tool we call | Where | Status |
|---|---|---|---|
| Schematic ERC, board<->schematic parity | `kicad-cli sch erc`, `kicad-cli pcb drc --schematic-parity` | `kicad_backend.run_erc/run_drc` | verified (fails when a rail flag or a pad net is removed) |
| Layer PDF, 3D renders, schematic PDF, IPC-D-356, ODB++, GenCAD, GLB, VRML, DXF outline, board statistics | `kicad-cli pcb export ...`, `sch export pdf` | `kicad_backend.export_extras` | verified by opening each file independently |
| Generic circuit simulation | `kicad-cli sch export netlist --format spice` + `ngspice` | `analysis/simulate.py` | verified against the analytic RC low pass (AC and step) |
| Microstrip / CPW impedance | scikit-rf (`pip install scikit-rf`) | `analysis/lines.py` | verified against Pozar's design equation; KiCad's PCB Calculator has no CLI |
| Interactive placement/BOM page | InteractiveHtmlBom (MIT) | `export/external.py` | verified; run headless (`INTERACTIVE_HTML_BOM_NO_DISPLAY`) |
| Panelisation (rails, tabs, V-cuts, tooling, fiducials) | KiKit (MIT) | `export/external.py` | adapter written; **NOT RUN in the development container** (wxPython unusable there). Its test skips and says so; the panel must pass KiCad DRC when it does run |
| Fiducials, test points, mounting holes | KiCad library footprints/symbols | `pcb/features.py`, `recipes/mounting_holes.py` | verified through KiCad DRC and parity |

Not built, on purpose: teardrops (KiCad implements them in its GUI/pcbnew core with no scripting or CLI entry point; use KiCad's own Edit > Add Teardrops on the delivered board), a PCB calculator (scikit-rf), a simulator (ngspice), panelisation (KiKit).
Ground-stitching vias (`pcb/stitching.py`) are our own small step because KiCad has no CLI for them; KiCad DRC judges the result.
