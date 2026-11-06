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
Pair/skew and group spread are re-measured from the routed copper (tracks + via barrels): the tool's own reports have disagreed with its written copper.
