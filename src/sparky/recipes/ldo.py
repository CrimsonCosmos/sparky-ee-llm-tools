from sparky.circuits.pins import pins
from sparky.equations import ldo as eq
from sparky.picker import recipes as needs
from sparky.recipes import Param, PortSpec, Result

META = dict(name="ldo", summary="Fixed-output linear regulator (KiCad symbol chosen by the designer, spec-checked against the catalogue) with input/output caps and a dissipation check.",
            category="power", auto=("vin_min", "vin_max", "iout"))
PARAMS = [Param("symbol", "string", None, "KiCad symbol 'Library:Name' of the regulator (e.g. Regulator_Linear:AMS1117-3.3)", True), Param("vin_min", "number", None, "min input (V)", True), Param("vin_max", "number", None, "max input (V)", True),
          Param("vout", "number", None, "fixed output (V)", True), Param("iout", "number", None, "load (A)", True),
          Param("c_in", "number", None, "input capacitance (F), from the regulator datasheet", True),
          Param("c_out", "number", None, "output capacitance (F), from the regulator datasheet", True),
          Param("rail", "string", None, "output rail net name", True)]


def build(ctx, symbol, vin_min, vin_max, vout, iout, c_in, c_out, rail):
    s, pol = ctx.s, ctx.policy
    u, ch = ctx.ic(symbol, needs.ldo(vout, iout * pol.current_margin))
    mpn = ch.mpn if ch else u.name
    vin, vo, gnd = s.Net("VIN"), s.Net(rail), s.Net("GND")
    for role, net in (("vin", vin), ("vout", vo), ("gnd", gnd)):
        for p in pins(u, role):
            p += net
    for p in pins(u, "pad", required=False):
        p += gnd
    for p in pins(u, "en", required=False):
        p += vin
    cin, cout = ctx.capacitor(c_in, vin_max), ctx.capacitor(c_out, vout)
    vin & cin & gnd
    vo & cout & gnd
    pw = eq.dissipation_w(vin_max, vout, iout)
    res = Result(calcs={"ldo": f"{mpn}: {vin_max:g}->{vout:g} V @ {iout:g} A, dissipation {pw:.2f} W, needs theta_JA <= "
                                f"{eq.max_theta_ja(pw, pol.ambient_c, pol.tj_max_c):.0f} K/W"})
    if pw > pol.ldo_dissipation_warn_w:
        res.warnings.append(f"ldo {mpn}: dissipates {pw:.2f} W at Vin {vin_max:g} V - consider a switching stage first")
    res.ports = [PortSpec("vin", "VIN", "power", "in", (vin_min + vin_max) / 2, iout, meta=dict(vmin=vin_min, vmax=vin_max)),
                 PortSpec("vout", rail, "power", "out", vout, iout, meta=dict(source="ldo")), PortSpec("gnd", "GND", "ground")]
    res.warnings += [f"not in the parts catalogue (no price/stock): {n}" for n in ctx.unsourced]
    return res
