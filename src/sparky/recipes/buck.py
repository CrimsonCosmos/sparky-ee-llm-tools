from sparky.circuits.pins import pins
from sparky.datasheet import need as datasheet
from sparky.equations.buck import BuckSpec, design_buck
from sparky.picker import recipes as needs
from sparky.recipes import Param, PortSpec, Result
from sparky.verify.spice import simulate_buck
from sparky.toolchain import ngspice

META = dict(name="buck", summary="Synchronous buck converter: KiCad symbol chosen by the designer, datasheet design procedure for L/C/divider, "
                                 "parts spec-checked against the catalogue, power stage verified in ngspice.",
            category="power", auto=("vin_min", "vin_max", "iout"))
PARAMS = [Param("symbol", "string", None, "KiCad symbol 'Library:Name' of the regulator (e.g. Regulator_Switching:TPS563201)", True),
          Param("vin_min", "number", None, "min input (V)", True), Param("vin_max", "number", None, "max input (V)", True),
          Param("vout", "number", None, "output (V)", True), Param("iout", "number", None, "load current (A)", True),
          Param("rail", "string", None, "output rail net name", True),
          Param("inductor_footprint", "string", None, "KiCad footprint for the inductor when chip-size parts do not fit", False)]
DATASHEET = {"vref": "feedback reference voltage, V", "fsw": "switching frequency, Hz", "l_min": "datasheet-recommended minimum inductance, H",
             "l_max": "datasheet-recommended maximum inductance, H",
             "c_boot": "bootstrap capacitor value, F", "v_boot": "voltage across the bootstrap capacitor (BOOT-SW), V"}


def build(ctx, symbol, vin_min, vin_max, vout, iout, rail, inductor_footprint=None):
    s, pol = ctx.s, ctx.policy
    ic, ch = ctx.ic(symbol, needs.buck_regulator(vin_min, vin_max, iout * pol.current_margin))
    mpn = ch.mpn if ch else ic.name
    ds = datasheet(ic.name, DATASHEET, ch.datasheet if ch else "")
    d = design_buck(BuckSpec(vin_min, vin_max, vout, iout, ds["fsw"], ds["vref"], l_range=(ds["l_min"], ds["l_max"])))
    vin, vo, gnd, sw, fb = s.Net("VIN"), s.Net(rail), s.Net("GND"), s.Net("SW"), s.Net("FB")
    for role, net in (("vin", vin), ("sw", sw), ("fb", fb), ("gnd", gnd)):
        for p in pins(ic, role):
            p += net
    for p in pins(ic, "pad", required=False):
        p += gnd
    for p in pins(ic, "en", required=False):
        p += vin
    r_top, r_bot = ctx.resistor(d.r_top), ctx.resistor(d.r_bottom)
    vo & r_top & fb & r_bot & gnd
    l = ctx.inductor(d.inductance, d.inductor_isat_min, inductor_footprint)
    sw & l & vo
    for p in pins(ic, "boot", required=False):
        cb = ctx.capacitor(ds["c_boot"], ds["v_boot"])
        p += cb[1]
        cb[2] += sw
    for c in ctx.capacitors(d.cin_required, vin_max):
        vin & c & gnd
    for c in ctx.capacitors(d.cout_required, vout):
        vo & c & gnd
    res = Result(notes=list(d.notes), calcs={"buck": f"{mpn}: {vin_min:g}-{vin_max:g} V -> {vout:g} V @ {iout:g} A; L={d.inductance * 1e6:g} uH "
                                                     f"(ripple {d.inductor_ripple:.2f} A, peak {d.inductor_peak:.2f} A, Isat>={d.inductor_isat_min:.2f} A); "
                                                     f"Cout>={d.cout_required * 1e6:.1f} uF; divider {d.r_top:g}/{d.r_bottom:g} -> {d.vout_actual:.3f} V"})
    res.warnings += [f"not in the parts catalogue (no price/stock): {n}" for n in ctx.unsourced]
    if ngspice():
        sim = simulate_buck(vin_max, vout, iout, ds["fsw"], d.inductance, d.cout_required, pol.cap_esr_ohm, ron=ds.get("rds_on", pol.switch_ron_ohm),
                            cycles=pol.spice_cycles, settle_cycles=pol.spice_settle_cycles, points_per_cycle=pol.spice_points_per_cycle)
        err_r = abs(sim.il_ripple_pp / d.inductor_ripple - 1)
        err_p = abs(sim.il_peak / d.inductor_peak - 1)
        res.calcs["spice"] = (f"ngspice at Vin={vin_max:g} V: ripple {sim.il_ripple_pp:.2f} A (eq {d.inductor_ripple:.2f}), peak {sim.il_peak:.2f} A "
                              f"(eq {d.inductor_peak:.2f}), Vout {sim.vout_avg:.3f} V, Vripple {sim.vout_ripple_pp * 1e3:.1f} mV")
        if max(err_r, err_p) > pol.spice_tolerance:
            res.warnings.append(f"SPICE disagrees with the design equations by {max(err_r, err_p) * 100:.0f} % (> {pol.spice_tolerance * 100:g} %): review the inductor/capacitor sizing")
    else:
        res.calcs["spice"] = "NOT VERIFIED: ngspice unavailable"
    res.ports = [PortSpec("vin", "VIN", "power", "in", (vin_min + vin_max) / 2, iout * vout / vin_min, meta=dict(vmin=vin_min, vmax=vin_max)),
                 PortSpec("vout", rail, "power", "out", d.vout_actual, iout, meta=dict(source="buck", tolerance=abs(d.vout_actual / vout - 1))),
                 PortSpec("gnd", "GND", "ground")]
    return res
