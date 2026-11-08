# What is missing to design "an entire NVIDIA GPU"

Research date 2026-11-08. Sources are cited inline; many are fabricator blogs (indicative, not authoritative) and each claim says so where it matters. Statements marked
**(ours)** are our own judgement or knowledge, not taken from a source.

## 0. Two different things
| Meaning | Verdict |
|---|---|
| **A. The GPU chip (silicon)** | Not feasible with open tools, a PDK we can obtain, or any realistic team. |
| **B. A graphics card / accelerator board around a GPU** | Partly feasible; large gaps listed below. The blocker is documentation access, not just software. |
| **C. A carrier board for an NVIDIA Jetson module** | The realistic way to "design an NVIDIA GPU board": public design guides exist (see section 3). |

## 1. The chip (A)
- Scale: AD102 76.3 B transistors on a 608 mm2 die; Hopper H100 814 mm2 on TSMC N4; Blackwell 208 B transistors on TSMC 4NP as two reticle-limited dies joined by a
  10 TB/s link ([TweakTown AD102](https://www.tweaktown.com/news/88536/nvidias-next-gen-ad102-gpu-76-3b-transistors-up-to-18432-cuda-cores/),
  [PC Gamer on Blackwell](https://pcgamer.com/hardware/graphics-cards/nvidias-new-blackwell-ai-gpu-raises-some-tricky-questions-about-its-next-gen-rtx-50-series-graphics-cards)).
- Cost: Jensen Huang reportedly put Blackwell R&D at about $10 B ([TweakTown](https://www.tweaktown.com/news/96987/nvidia-spent-10-billion-on-developing-its-next-generation-blackwell-gpu/index.html));
  an executive-stated approximation. No source found for team size or the exact EDA tools used.
- Open flow: OpenROAD documents tapeouts up to 12 nm (self-reported) and open PDKs GF180, SKY130, Nangate45, ASAP7 (predictive 7 nm)
  ([OpenROAD docs](https://openroad.readthedocs.io/en/latest/main/README.html)); OpenLane is in maintenance, LibreLane is the successor
  ([wafer.space](https://www.crowdsupply.com/wafer-space/gf180mcu-run-1/updates/librelane-template)). A 4 nm-class PDK, foundry signoff decks and
  commercial EDA are NDA/licence-bound **(ours)**.
- Open GPU RTL: Vortex (RISC-V SIMT with OpenCL/OpenGL, FPGA-optimised; a small configuration synthesised on a 15 nm educational library at 300 MHz)
  ([Vortex paper](https://arxiv.org/pdf/2110.10857)), MIAOW (AMD Southern Islands compute, no graphics) ([MIAOW](https://pages.cs.wisc.edu/~vinay/pubs/MIAOW-HotChips.pdf)), Nyuzi.
  None is a taped-out product-class GPU.
- What IS reachable: a small educational GPU on an FPGA or SKY130/GF180 through OpenROAD. That is a different project from this repository (RTL/physical design, not boards).

## 2. The board (B): what a high-end card needs vs what sparky has
Status key: HAVE = built and validated here; PART = exists but not at this scale or not verified for it; MISSING.

### 2.1 Inputs we cannot get (the real blocker)
- GPU package ballmaps, power-rail and sequencing specs, memory-interface layout rules, IBIS/IBIS-AMI models and reference PCBs are under NDA for add-in-card partners; partners
  design to NVIDIA's reference and submit design files for NVIDIA review before samples ([IgorsLab, secondary source](https://www.igorslab.de/en/nvidias-rules-and-amds-protectionism-gaengelung-of-the-manufacturer-cleveres-quality-management-or-profit-maximization-insights-behind-the-scenes/));
  I found no public statement of current terms. Without the BGA footprint (thousands of balls **(ours)**) and the datasheet numbers, `datasheet.py` cannot be filled from an official source, which our rules require.
- GDDR6 has a public design guide ([Micron TN-ED-04 via Mouser](https://www.mouser.com/pdfDocs/tn-ed-04_gddr6_design_guide.pdf)): at least 8 layers recommended, high-speed signals in stripline between ground planes,
  blind vias suggested for stripline. GDDR6X-specific skew numbers were not found; GDDR6X is PAM4 at 21-24 Gb/s ([Micron brief](https://www.micron.cn/content/dam/micron/global/public/products/technical-marketing-brief/gddr6x-pam4-2x-speed-tech-brief.pdf)).

### 2.2 Layer count and fabrication
- Consumer high-end cards: about 8-14 layers; AI accelerators 30+ layer HDI (fabricator blogs: [NextPCB](https://www.nextpcb.com/blog/why-ai-gpu-pcbs-require-30-plus-layer-hdi), [PCBgogo](https://www.pcbgogo.com/Article/gpu-pcb-guide.html)).
- HAVE: any layer count (32-layer synthetic board DRC-clean), plane assignment, HDI spans, via-in-pad (tested on a 100-ball 0.5 mm BGA).
- PART: never tested on a board with thousands of balls, 20+ nets per mm2 of escape, or 100+ components; router run time and memory at that scale are unknown. A benchmark ladder is needed.
- MISSING: back-drilling (stub removal) for fast vias; frequency-dependent materials (our stackup has a single er and loss tangent; low-loss laminates need Dk/Df vs frequency);
  impedance-coupon generation; fab capability tables for >20 layers; (the fab's own review stays the fab's).

### 2.3 High-speed interfaces (PCIe 5.0 x16, GDDR6X, display)
- HAVE: diff-pair / length-group / fanout constraints via KiCadRoutingTools, skew graded by KiCad DRC, single-ended and (outer-layer) differential impedance verified (scikit-rf, atlc).
- MISSING: byte-group constraint model (data, EDC, WCK, command/address with their own matching scopes); timing-based matching (ps including via and stub delay) instead of length;
  stripline and inner-layer differential impedance verification (atlc has stripline bitmap helpers: adapter not written); stub/via-transition optimisation; AC-coupling and connector (PCIe edge fingers)
  footprints and rules; insertion/return-loss budget.
- Open-source channel tools exist and can be integrated: scikit-rf (S-parameters), pyBERT (IBIS-AMI link simulation), openEMS and Antmicro's `gerber2ems` (Gerber to FDTD SI simulation)
  ([search results](https://github.com/antmicro/gerber2ems)). None is validated for PCIe 5.0 compliance; compliance testing remains a PCI-SIG/lab matter. IBIS-AMI models come from the chip vendor (NDA) **(ours)**.

### 2.4 Power delivery (the dominant problem)
- Sources agree that power integrity drives complexity: frequency-dependent target impedance and decap optimisation are published methods
  ([KAIST](https://koasas.kaist.ac.kr/handle/10203/313079), [Springer](https://link.springer.com/article/10.1007/s42452-025-07224-6)); vendor tools are FPGA-specific
  ([Intel PDN Tool](https://www.intel.com/content/www/us/en/docs/programmable/683293/current/further-optimizing-for-better-accuracy.html)). I found no vendor-neutral GPU-class PDN tool and no verified
  worked 400 A example. Vendor blog numbers conflict (target impedance 1 mOhm vs micro-ohm range): derive from the chip's current/ripple spec instead.
- HAVE: single-phase buck recipe with ngspice check, IR drop on real copper (kicad-irdrop, validated at low current), board thermal FEM, switching-loop EMC.
- MISSING: multi-phase controller + DrMOS recipe (phase count, interleaving, inductor/current sharing, sensing), 12V-2x6 connector rules, 300-600 A copper design (our IR-drop/thermal are validated far below this),
  parts not in the JLCPCB catalogue (power stages, controllers, GDDR). 
- **Feasible integration, not yet built:** PDN impedance versus frequency from the copper itself using pcb-analysis' port-impedance solver (already used for our EMC network) plus decap models and VRM output
  impedance, graded against a target impedance computed from the chip's current/ripple spec (input from the datasheet, per our rules). Validate against a hand RLC network.

### 2.5 Thermal and mechanical
- A 300-600 W card needs a cooler (heat spreader / vapour chamber, fans, airflow, TIM). We model board copper plus convection only; enclosure and heat sink are out of scope (`mechanical.py`).
  pcb-analysis can voxelise a user-supplied heat-sink/enclosure STEP; airflow CFD would be OpenFOAM (listed by [Altium](https://resources.altium.com/p/best-signal-integrity-analysis-tools-pcbs)). Neither is wired in.

### 2.6 Firmware, compliance, process
- VBIOS/firmware, power tables, fan control, signed boot and drivers are the vendor's. EMC (we have a switching-loop estimate only), safety and PCI-SIG compliance need a lab.
- Partner approval by NVIDIA (above) is a gate no tool replaces.

## 3. The realistic NVIDIA target: a Jetson carrier board (C)
NVIDIA publishes design guides and, for some carrier boards, PCB design files for Jetson modules (forum threads:
[Nano carrier board files](https://forums.developer.nvidia.com/t/jetson-nano-carrier-board-pcb-design-files/82071),
[TK1 Gerber notes](https://forums.developer.nvidia.com/t/jetson-tk1-gerber-fabrication-notes/37792)); availability varies by module generation and I could not confirm current terms.
A carrier board (module connector, PCIe, USB, Ethernet, camera inputs, power tree) uses what we have: recipes + datasheet numbers + diff pairs + impedance + HDI if needed.
This is the only NVIDIA-GPU deliverable I would call in reach.

## 4. Ordered roadmap (integrate first; nothing here reimplements an existing tool)
1. Scale benchmark ladder: BGA with 400, 1000, 2000+ balls on 8-30 layers (time, memory, DRC, completion). Needed to know whether the toolchain scales at all.
2. PDN impedance analysis via pcb-analysis port network + decap models; target-impedance input; validated against hand RLC.
3. Multi-phase VRM recipe (controller + power stages from datasheet numbers), IR drop and thermal re-validated at hundreds of amps against hand calculations.
4. Interface constraint model (byte groups, timing-based matching, stripline/inner-layer differential impedance via atlc), channel simulation adapters (scikit-rf, pyBERT, gerber2ems/openEMS).
5. Back-drill and frequency-dependent materials in the stackup; fab capability tables.
6. Cooler/enclosure thermal via pcb-analysis voxel mesh from a user STEP; optional OpenFOAM adapter.
7. Jetson-module carrier-board recipe once the module's public design guide is stored as datasheet inputs.
Not doable here: chip design, vendor NDA data, vendor IBIS-AMI models, VBIOS, NVIDIA partner approval, PCI-SIG compliance.

## Update (same day): items from 2.2 / 2.3 that turned out to be integrations
Asked "why do we not have these", the honest answer was that they had not been done, and most already exist in tools we use. Now built as adapters/constraints, each with an independent check:
| Item | How | Check |
|---|---|---|
| Byte-group constraints | `DramGroups` runs KiCadRoutingTools' own DRAM classifier (byte lanes = data + strobe + mask; command/address) | the classifier's lane on a fixture equals the expected set |
| Delay-based matching | `LengthGroup.time_ps` / `DramGroups(time_ps=)` drive KRT's `--time-matching`; our own delay measurement (`analysis/delay.py`: scikit-rf outer layers, TEM stripline, via barrels) grades it | hand TEM value, Hammerstad value within 4 %; on the BGA fixture KRT's matching left 6.6 ps by our measure against a 5 ps limit: reported NOT met |
| Inner-layer stripline impedance | atlc on a cross-section bitmap with real copper thickness (`diffpair.stripline`, `stripline_pair`); used for `ControlledImpedance` and diff pairs on inner layers | Cohn's exact zero-thickness result within 4 %; thickness/offset/uncoupled-pair physics |
| Channel-loss budgets | `LossBudget`; scikit-rf microstrip (Debye-Djordjevic dielectric, roughness) and stripline (atlc Z0 + Pozar conductor loss) | dielectric part vs closed forms; **stripline conductor loss is a literature formula, not independently validated** (labelled) |
| Frequency-dependent materials | `Rules.dielectric_f_ref_hz`, `copper_roughness_rms_m` feed scikit-rf's wideband Debye model | reproduces pi f sqrt(er) tan(d) / c at the reference frequency |
| Back-drilling | `BackdrillNets` + `Rules.backdrill` (fab capability); KiCad 10 stores and exports back-drills; we plan depth/end layer and re-check | the depth we compute equals the depth KiCad prints in its drill file (within 5 um) |
Still not covered: vias in the loss budget (input only), PCIe/GDDR channel simulation (pyBERT / gerber2ems not wired), KiCad has no verdict for delay-matched groups.
