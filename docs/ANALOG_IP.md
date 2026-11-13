# Analog IP from OpenFASOC (Apache-2.0), run here with the user's approval

Source: https://github.com/idea-fasoc/OpenFASOC, commit `426c1702...` (read at that commit; nothing of it is vendored here except one patch). Both items below were run from scratch by the scripts named, and give the same results on a clean directory.

## 1. All-digital temperature sensor generator (`generators/temp-sense-gen`) - `tools/openfasoc_temp_sense.py`
The generator (Python + an OpenROAD-style flow for sky130hd) runs end to end with our Yosys/OpenROAD/Magic/Netgen/KLayout and produces a macro (GDS, DEF, SPICE, Verilog, SDC). Its flow scripts target an older OpenROAD,
so seven small edits were needed (`tools/openfasoc_compat/temp-sense-gen-openroad-2026.patch`): (1) drop a no-op `add_pdn_connect met4 met5` for the analog grid (PDN-0113), (2) drop the obsolete `-random` pin placement flag (pins were silently not placed),
(3) alias the renamed `place_cell` to `place_inst`, (4) alias the renamed `sta::endpoint_count` (log line only), (5) replace the deprecated `-bottom/-top_routing_layer` options by `set_routing_layers`, (6) mark the analog supply net VIN as power before global routing (GRT-0042),
(7) make the final IR-drop analysis and the GUI preview non-fatal. Also needed: `pip install mako`, `PDK_ROOT` pointing at the PDK, and an executable `/usr/bin/time` (the flow hard-codes it; a 3-line shell stand-in was created in this container).
Side effect to know: the generator rewrites `PDKPATH` in the PDK's `sky130A.magicrc`.

Results on the generated `tempsenseInst_error` macro (default spec: -20..100 C):
| check | result |
|---|---|
| generator flow | completes (about 2 minutes) |
| generator's Magic DRC | reports no violations |
| our independent KLayout DRC with the PDK's `sky130A_mr.drc` and its rule-group switches | FAIL: 18 violations, all via rules (`via.1a_b` 8, `via2.1a_b` 8, `via.2` 1, `via2.2` 1) |
| generator's Netgen LVS | devices and nets compare as equivalent, but "Top level cell failed pin matching": the layout has no pin for the analog supply VIN where the schematic has one |
| generator's IR-drop analysis | fails (PSM-0069 power-grid connectivity on VDD/VSS), made non-fatal; the unconnected shapes are in the analog voltage-domain region |
So: the macro is generated, but it is NOT sign-off clean. The via violations and the VIN pin/PDN-connectivity problems are plausibly consequences of porting the power-grid script to a newer OpenROAD (patches 1 and 6 touch exactly that) rather than of the sensor design;
not investigated further, and not verified either way. Nothing here shows the sensor works electrically: its simulations (ngspice with its characterisation model) were not run.

## 2. LC digitally-controlled oscillator core (`generators/lc-dco/simulations/Ngspice`) - `tools/openfasoc_lc_dco_sim.py`
The cross-coupled NMOS LC oscillator (PDK inductor `sky130_fd_pr__ind_05_220`, 800 uA tail current) simulated with ngspice and the sky130 models (the netlists point at a `PDK/open_pdks` tree; the script rebuilds that layout and drops two unused, uninstalled standard-cell includes):
| simulation | result |
|---|---|
| nominal (1 ps step, 100 ns) | oscillates at 3.4215 GHz, differential swing 6.33 V peak-to-peak (each output swings beyond the 1.8 V supply, as an inductor-fed cross-coupled pair does; the device voltage ratings are not checked here), common mode 1.80 V |
| half time step (0.5 ps) | 3.4216 GHz, same swing: numerically converged |
| half tail current (400 uA) | does not start up (the simulation can fail) |
Frequency measured by interpolated zero crossings of v(outp)-v(outn) after 50 ns in Python, not by the netlist's own `.measure` lines (those reference a node `v(out)` that does not exist). Not done: the digital control loop, the frequency-vs-code table, phase noise,
the PLL around it, any layout of the oscillator.

## What stays open
HBM/GDDR/SerDes PHYs and a real PLL are not available as open IP at GPU-class speeds; the two blocks above are small analog/mixed-signal building blocks, not those. PVT corners and Monte Carlo of the oscillator were not run.
