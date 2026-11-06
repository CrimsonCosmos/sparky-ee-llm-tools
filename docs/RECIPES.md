# Writing a recipe

A recipe is one Python file: a SKiDL circuit built from KiCad's libraries plus the design procedure for it. Recipes are discovered from
`src/sparky/recipes/`, `$SPARKY_RECIPES` and `<data_dir>/recipes/` (later entries override earlier ones). The supervisor writes new ones with
`sparky_write_recipe` when nothing fits; they are validated on load and then cached like researched parts.

```python
from sparky.recipes import Param, PortSpec, Result

META = dict(name="my_recipe", summary="...", category="power",
            auto={"iout": ("vout", "current"), "vin_min": ("vin", "vmin")})   # params the resolver derives: (port, current|vmin|vmax|voltage)
PARAMS = [Param("symbol", "string", None, "KiCad symbol of the main IC", True), Param("vout", "number", None, "output (V)", True), ...]

def ports(policy, vout=None, iout=None, **_):          # interface from parameters alone (missing values allowed) - used by the resolver before building
    return [PortSpec("vout", "VOUT", "power", "out", vout or 0.0, iout or 0.0), ...]

def build(ctx, symbol, vout, iout, ...):               # ctx.s is SKiDL; build parts and nets; return a Result
    ic, choice = ctx.ic(symbol, need)                  # KiCad symbol; the catalogue is a SPEC CHECK + sourcing, never a silent substitution
    ds = ctx.datasheet(...)                            # datasheet numbers: from sparky.datasheet.need(); raises NeedsDatasheet if not cached
    ...
    return Result(ports=ports(ctx.policy, ...), calcs={"my_recipe": "..."}, warnings=[...])
```

Rules (the same as the project's top rules):
1. **No hardcoded engineering values.** Numbers come from the parameters, the datasheet (`sparky.datasheet`), or `Policy` (documented margins).
   Only unit conversions and standards constants (e.g. the ISO 11898 120 ohm termination) may be literals, and they are named.
2. **Pins by name or function from the KiCad symbol** (`sparky.circuits.pins`: roles, `pin_for_function`), never by hand-written pin numbers.
   A missing role raises; it never leaves a pin silently unconnected.
3. **Parts:** ICs = designer-chosen KiCad symbol; discretes = parametric catalogue pick + footprint found in KiCad's library by package
   (`ctx.resistor/capacitor/capacitors/inductor/electrolytics/fuse/tvs`). Unsourced parts are warned about; vendor imports (`ctx.ic_import`) are flagged unreviewed.
4. **Verify what you can:** compare equations with ngspice (`sparky.verify.spice`) or another independent reference, and write a test whose expected values
   are hand-computed. Say "NOT VERIFIED" in the output when a check could not run.
5. Equations are a first pass, not the proof. Datasheet design procedures are used; loop stability, EMC and enclosure thermals are not verified.
