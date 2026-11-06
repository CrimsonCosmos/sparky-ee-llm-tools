# sparky-ee-llm-tools — system design

## 1. Purpose (the one thing this program does)
> Given a system-level description with named **inputs and outputs** (rails, buses, connectors, loads, budget), produce a
> **KiCad project** (schematic + placed + routed PCB + fab files) and a **bill of materials with sourcing**.

Claude supplies **system design judgement only**: decompose the request, pick architecture, decide buy vs build, research an
unknown part. Everything else is deterministic, open-source, and token-free. If a step can be done by an existing tool, we call
that tool; we do not write it.

## 2. Principles
1. **Adopt, don't build.** A new module needs a written reason why no open-source tool fits. Hand-rolled code is a liability.
2. **Loose coupling through files.** Stages communicate through on-disk artifacts with stable public formats (KiCad files, SPICE netlists,
   JSON specs). No shared in-memory object model across stages; no stage imports another stage's internals.
3. **One technology: Python ≥ 3.13**, console application (`sparky ...`), Windows/macOS/Linux. External tools are *invoked*, never reimplemented.
   No browser UI, no Rust/JS in this repo.
4. **Minimal LLM.** Two call sites only (§5). Both return schema-validated JSON; all else is deterministic and reproducible.
5. **Honest coverage.** Every check reports what it could *not* check. No silent passes.
6. **Fast feedback.** Unit tests are scoped and run in seconds on every push; integration runs are manual/nightly (§8).

## 3. Pipeline and contracts
```
 request ─► [A] system plan ─► spec.json ─► [B] design equations ─► [C] part picker ─► [D] schematic ─► [E] layout ─► [F] verify ─► [G] outputs
  (Claude)     (LLM, JSON)     (contract 1)   (solver + SPICE)        (parametric DB)     (KiCad .kicad_sch)  (place+route)   (DRC/ERC/sim)   (fab, BOM, buy list)
```
| # | Stage | Input → output (the contract) | Implemented by (adopted) |
|---|---|---|---|
| A | System plan | free text → `spec.json` (nodes, I/O, rails, buy/build, budget) | Claude (schema-validated), the only creative LLM step |
| B | Design equations | `spec.json` → component *requirements* (L ≥ x, C ≥ y, Vrating ≥ z) | published design procedures encoded as small pure functions; `pint` units; ngspice for verification |
| C | Part picker | requirements → concrete MPNs (+ alternates) | parametric DB: **jlcparts** (LCSC/JLCPCB catalogue, open data) + KiCad libraries; Nexar/Octopart & DigiKey/Mouser APIs when keys exist; Claude research only on a miss |
| D | Schematic | MPNs + connectivity → `.kicad_sch` + netlist | **SKiDL** (circuit as Python objects → KiCad netlist/schematic) over KiCad symbol/footprint libraries |
| E | Layout | netlist → `.kicad_pcb` | placement: KiCad has none → see §6; routing: **Freerouting** via KiCad Specctra DSN/SES; zones/fill: KiCad |
| F | Verification | design files → report | **KiCad** ERC/DRC (`kicad-cli`), **ngspice** simulation, datasheet reference check, rule engine (§6) |
| G | Outputs | design → manufacturing | `kicad-cli` (gerber, drill, position, STEP, IPC-2581 on KiCad ≥ 8), BOM/links from picker |

Each stage is a CLI subcommand reading/writing files in a project directory, so any stage can be replaced (e.g. swap router) without touching others.

## 4. Open-source map (replaces custom code)
| Need | Tool | Status here |
|---|---|---|
| EDA suite, DRC/ERC, fab export | **KiCad 10.0.x** (current is 10.0.8; this machine has 7.0.11 from Ubuntu) | 7.0 lacks `pcb drc`, `sch erc`, IPC-2581 → **upgrade to 10** (PPA install was blocked by the sandbox; see §9) |
| Circuit as objects | **SKiDL** (PyPI 3.0.0) | to adopt; replaces our `Circuit/Component/Net` |
| Schematic/PCB file I/O | **kiutils** 1.4.8, **kicad-python / kipy** 0.8.0 (KiCad IPC API) | kiutils in use; kipy for KiCad ≥ 9 live API |
| Autorouter | **Freerouting** (jar; 2.1.0 verified, newest unverified) | integrated (checksum-pinned) |
| Circuit simulation | **ngspice** (Ubuntu 42; newest from upstream) / Xyce for large | installed; testbenches pending |
| Parts data | **jlcparts** public DB (zip reachable; schema to verify), KiCad libs | to integrate (replaces hand-written seed catalogue) |
| Thermal | **Elmer FEM** (open) driven from the board's STEP/copper geometry | adapter pending; until then only a labelled 1-D estimate |
| EMI / shielding | **openEMS** (FDTD) for shield/aperture studies; rule-based pre-checks (loop area, return path) | adapter pending, behind the verification interface |
| Mechanical / enclosure | **FreeCAD** + **CadQuery** 2.8.0 via `kicad-cli pcb export step` | roadmap (§7), not core |
| Units / equations | **pint** 0.26.2, **sympy** 1.14.0 | adopt |
| CLI/config | **typer**, **rich**, **platformdirs** | adopt (cross-platform paths) |
| Alternative IRs evaluated | **atopile** 0.12.6, **circuit-synth** 0.12.1 | evaluate in spike (§7 step 2) before committing to SKiDL |

Not open-source equivalents of Cadence/Synopsys at full parity: that gap is real for signal-integrity/EMC sign-off. The honest position is
*KiCad + ngspice + Elmer + openEMS covers prototype-grade verification*, and the report states the residual risk.

## 5. Where the LLM is used (and how little)
1. **System plan** (stage A): one call (+ bounded repair loop on schema/resolver errors) → `spec.json`. Includes buy-vs-build policy.
2. **Part research on a picker miss** (stage C fallback): returns schema-validated JSON *with evidence quotes*; deterministic code verifies the quotes occur in the datasheet.
Nothing else. No LLM in routing, placement, DRC, equations, export, or sourcing. Prompts and schemas live in `prompts/` and are versioned.

## 6. Decisions that need a spike before code
* **Placement.** KiCad has no autoplacer. Candidates: keep our small simulated-annealing placer behind a file contract (netlist+constraints → placed
  `.kicad_pcb`), or an open-source placer if one fits. *Decision gate:* benchmark ≥ 2 options on 3 real boards; keep ours only if it wins.
* **Circuit IR.** SKiDL vs atopile vs circuit-synth: pick by (a) KiCad output fidelity, (b) library integration, (c) maintenance. One day, written result.
* **Checks.** Prefer KiCad ERC/DRC and ngspice; write rules only for what they cannot express (current/voltage ratings, sequencing, pull-ups) and keep each rule a tiny, cited function.

## 7. Roadmap (each step ships something runnable; nothing speculative)
1. **Repo + tooling**: move to this repo, `sparky` console entry, tool discovery (Win/macOS/Linux), CI with scoped fast tests. *(this commit)*
2. **IR spike** → choose SKiDL/atopile/circuit-synth; port blocks to it; delete our `Circuit`.
3. **Part picker** on jlcparts + KiCad libs; delete the hand-written seed catalogue.
4. **KiCad 10 backend**: DRC/ERC/step/IPC-2581 through `kicad-cli`; delete our Gerber/Excellon/KiCad-file writers, SVG, DRC.
5. **Router**: Freerouting only; delete our grid router, plane/pour fill, stitching.
6. **Simulation**: ngspice testbench per block family; mutation corpus measures false positives/negatives.
7. **Thermal/EMI adapters** (Elmer/openEMS) and **mechanical** floor-plan constraints (small, roughly square outline by default; STEP out).

## 8. Testing policy
* Unit tests only, each < 1 s, no network, no KiCad/Java/ngspice. `pytest` default excludes `slow`.
* `tools/scoped_tests.py` maps changed files → relevant test modules; CI runs exactly that on every push (3 OSes).
* Integration (`-m slow`: real boards, KiCad, Freerouting, ngspice) runs **only** on manual dispatch or nightly, and at the end of a work block — never between edits.

## 9. External requirements and known blockers
See `docs/DEPENDENCIES.md`. Blocked in the current sandbox: installing KiCad 10 from its official PPA (third-party apt source, denied by the safety policy), push access to this repository (permission rule needed).
