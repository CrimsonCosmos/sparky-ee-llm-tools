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
