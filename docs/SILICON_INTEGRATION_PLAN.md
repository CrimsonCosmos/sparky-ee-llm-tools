# Plan: integrating the open silicon flow as deeply as we integrate KiCad

Status: PLAN (nothing below is built). Goal: the same relationship to LibreLane/OpenROAD/KLayout/Yosys/Magic/Netgen/OpenSTA/Verilator/cocotb that `sparky` has to KiCad:
we supply design intent and judgement, the open tools do the work and give the verdicts, every check is validated against an independent reference, and a tool that did not run is
reported NOT RUN, never as a pass.

## 1. What our KiCad integration consists of (the template to match)
| Layer | KiCad today | Silicon equivalent |
|---|---|---|
| Project writer | `.kicad_pcb/.kicad_pro/.kicad_dru`, library tables, stackup from `Rules` | LibreLane config (JSON), SDC constraints, PDK selection, macro/IP list, floorplan intent from a `SiliconSpec` |
| Runner adapters | `kicad_backend` (kicad-cli), `routing` (Freerouting), `krt` | `silicon/flow.py` (LibreLane), `silicon/sim.py` (Verilator/cocotb), `silicon/klayout.py`, `silicon/netgen.py`, `silicon/sta.py` |
| Authoritative verdicts | KiCad DRC/ERC/parity | KLayout + Magic DRC, Netgen LVS, OpenSTA timing, OpenROAD antenna/IR/EM reports |
| Independent cross-checks | our centre-line skew vs KiCad's rule; pad positions vs pcbnew; drill files vs gerbonara | Liberty/SPEF hand timing on tiny paths; gate-level sim vs RTL sim; GDS read back with gdstk/KLayout for area, layer, cell counts; DRC deck run in two engines |
| Analyses | irdrop, thermal, EMC adapters (pcb-analysis) | OpenROAD PDN/IR drop, power (OpenSTA), clock skew, congestion; thermal of the die via pcb-analysis FEM (chip as a layered mesh) |
| Exports/package | gerbers, IPC-2581, BOM, report | GDSII, LEF/DEF, netlist, SDF, SPEF, Liberty, `REPORT.md` with every verdict, tapeout checklist |
| Inputs policy | no hardcoded engineering values; fab profile object | PDK is an input (`PdkProfile`): DRC deck, corners, supply, utilisation, clock targets come from the user/PDK, never inline |
| Tests | fast scoped suite + slow integration, tests that can fail | fast: unit + tiny designs; slow: whole flows; every check has a deliberately broken design that must fail |
| Reporting honesty | NOT RUN / NOT VERIFIED lines | same, plus "timing met at corner X only" |

Package layout (follows `AGENTS.md`-style one-way imports; a new top-level package, not folded into the PCB code):
`src/silicon/{flow,rtl_sim,layout,signoff,pdk,report}/` with `py.typed`; nothing in `sparky.pcb` imports it, and the system planner may call it as another recipe-like builder.

## 2. Tool roles and how each is driven
- **LibreLane** (successor to OpenLane 2; Python, Nix or Docker): the orchestrator. We never reimplement a stage: we generate its config and call its API/CLI, read its metrics JSON and reports.
- **Yosys / pyosys**: synthesis (inside LibreLane); we also use `pyosys` directly for lint-level netlist queries (cell counts, hierarchy) in cross-checks.
- **OpenROAD (+ OpenSTA)**: floorplan/place/CTS/route inside LibreLane; we call `openroad`/`sta` scripts directly for extra reports (worst paths per clock group, IR drop, density).
- **KLayout** (pip `klayout`, also standalone): GDS reader/writer, DRC (Ruby/Python decks), LVS (KLayout LVS), screenshots; the Python module gives layout queries without a GUI.
- **Magic + Netgen**: second DRC engine and LVS (for open PDKs that ship Magic/Netgen decks): two engines agreeing is our independent check.
- **Verilator + cocotb**: RTL simulation, lint, coverage; cocotb for Python testbenches; gate-level re-simulation with SDF where the PDK allows.
- **gdstk / gdsfactory** (read-only use): independent GDS parsing for cross-checks.

## 3. Phases (each ends in verified, committed code; a phase is not "done" until its deliberately-broken test fails correctly)
### Phase 0: feasibility spike (days)
Install LibreLane + tools in this container (Nix and Docker are present; pip has `librelane`, `klayout`, `pyosys`, `cocotb`). Run a trivial counter on SKY130, GF180 and IHP-SG13G2.
Deliverables: `docs/SILICON_DEPENDENCIES.md` (versions, licences, per-OS install: Nix/Docker; Windows via WSL/Docker), a doctor entry (`sparky doctor` lists the silicon tools), recorded run times/memory on this 4-core, 15 GB machine.
Exit: reproducible install script; a go/no-go on what scale this machine can run.
### Phase 1: runner + project writer (1-2 weeks)
`SiliconSpec` (top module, RTL files, clocks and targets, IO, PDK profile, utilisation) to a LibreLane config; `run_flow()` returns a structured `FlowResult` (stage metrics, report paths, artefacts), never raises silently; resumable runs; timeouts; cache by input hash.
Verification: parse LibreLane's own metrics and cross-check three numbers independently (cell count via pyosys on the netlist, die area from the DEF/GDS via gdstk, clock period vs SDC read back).
### Phase 2: signoff verdict layer (2-3 weeks)
`signoff.drc` (KLayout and Magic), `signoff.lvs` (Netgen and KLayout LVS), `signoff.sta` (OpenSTA multi-corner with SDF/SPEF), antenna check from OpenROAD. Unified `Verdict` objects with: tool, version, rule set, counts, locations, NOT RUN reasons.
Verification: for each check, a known-bad layout/netlist (shorted net, DRC spacing violation injected with gdstk, a path made to fail setup) must be caught by every engine; known-good reference designs (Tiny Tapeout examples) must pass. Cross-engine disagreement is itself reported.
### Phase 3: RTL verification layer (2 weeks)
Verilator lint + cocotb harness generator for the spec's interfaces; RTL vs gate-level equivalence by simulation with random vectors (and Yosys `equiv` for small blocks); coverage report in the package.
Verification: seeded RTL bugs must be caught; gate-level results equal RTL results for the reference designs.
### Phase 4: analyses on par with irdrop/thermal/EMC (3-4 weeks)
OpenROAD IR/EM reports surfaced with budgets from the spec; power from OpenSTA with user activity; die thermal via pcb-analysis (layered mesh from the GDS stack, heat sources from placed-cell power) validated against a hand 1-D conduction case; clock skew/uncertainty tables; congestion maps as PNG for the report.
Verification: hand calculations (R = rho L / A for a rail, 1-D conduction, a 3-gate path delay from Liberty tables) before any number is trusted.
### Phase 5: package + tapeout checklist (1-2 weeks)
`REPORT.md` like the PCB one (verdicts, NOT RUN, estimates labelled), GDS/LEF/DEF/netlist/Liberty bundle, shuttle submission checklist (e.g. Tiny Tapeout / wafer.space constraints read from the programme, as inputs), reproducibility manifest (tool versions, hashes).
### Phase 6: system-level glue to the board tool (1-2 weeks)
A chip is a part on a board: emit the board-side facts from the silicon flow (pad ring/package pin list, supply currents from the power report, IO standards) as `datasheet` inputs with source = our own sign-off report, so the PCB recipes use them; and the reverse: board-level supply impedance constraints feed the die power budget.
### Phase 7: scale ladder and GPU-class target (ongoing)
Benchmark ladder: counter, RISC-V core, small SoC (Croc/Basilisk-class), then a small open GPU (Borg or a Vortex configuration) from RTL to signed-off layout on an open PDK. Record where flow time, memory and QoR stop scaling (the honest answer to "how far can this go").

## 4. Cross-cutting rules (inherited from CLAUDE.md)
- **No hardcoded engineering values**: PDK profile object (supply, corners, DRC deck path, site/track data from the PDK itself), clock targets from the spec.
- **Integrate, do not reinvent**: no placer, router, STA, DRC engine or extractor of ours.
- **Confidently wrong checks are worse than none**: every verdict has an independent reference and a failing counter-example in tests; estimates labelled.
- **Loose coupling**: tools found at runtime (PATH, env var, Nix/Docker), versions pinned in the manifest, missing tool means NOT RUN with the install hint.
- **Licences**: LibreLane/OpenROAD/Yosys/Verilator (Apache/BSD/ISC/LGPL), KLayout (GPL), Magic (MIT-like), Netgen (MIT-like): run as external processes or optional imports; KLayout's GPL is why we call it as a tool/optional dependency, not bundle it.

## 5. Risks and open questions
- Install weight and platform support (Nix/Docker on Linux; macOS via Docker; Windows via WSL): scope `sparky doctor` accordingly.
- This container (4 cores, 15 GB): large designs will not fit; the ladder will stop where the machine stops, and the report must say which limit it was.
- PDK choice: SKY130/GF180/IHP-SG13G2 are open; advanced nodes are not available to us (NDA), so "on par" means on par in integration depth, not in node.
- Licensing/IP: using third-party open GPU RTL requires checking each licence before shipping artefacts.
- Analog/mixed-signal blocks (PHYs, PLLs): outside the digital flow; see xschem/ngspice and open analog IP only after Phase 5.
- Verification gap: sign-off-grade STA (OCV, CDC, X-prop) is thin in open tools; the report must say what was and was not checked.

## 6. First concrete steps
1. Phase 0 spike (install script, doctor entry, trivial design on three PDKs).
2. `SiliconSpec` + `PdkProfile` data model with tests (no tools needed).
3. LibreLane runner returning a typed `FlowResult` for the counter.
