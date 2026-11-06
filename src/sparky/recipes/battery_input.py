from sparky.recipes import Param, PortSpec, Result

META = dict(name="battery_input", summary="DC/battery input: connector, fuse, unidirectional TVS clamp and bulk electrolytics; exposes the protected bus rail.",
            category="power", auto=("ibat_max", "vbat_nom", "vbat_max", "vbat_min", "c_bulk"))
PARAMS = [Param("vbat_nom", "number", None, "nominal pack voltage (V)", True), Param("vbat_max", "number", None, "max voltage incl. full charge (V)", True),
          Param("vbat_min", "number", None, "min voltage at cutoff (V)", True), Param("ibat_max", "number", None, "max continuous bus current (A)", True),
          Param("c_bulk", "number", None, "bulk capacitance the downstream stages need (F)", True),
          Param("connector_symbol", "string", None, "KiCad symbol of the 2-pin power connector (pin 1 = +, pin 2 = -)", True),
          Param("connector_footprint", "string", None, "KiCad footprint of the connector", True),
          Param("connector_rating_a", "number", None, "connector continuous current rating from its datasheet (A)", True),
          Param("rail", "string", "VBAT", "net name of the protected bus", False)]


def build(ctx, vbat_nom, vbat_max, vbat_min, ibat_max, c_bulk, connector_symbol, connector_footprint, connector_rating_a, rail):
    s, pol = ctx.s, ctx.policy
    if ibat_max > connector_rating_a * pol.connector_derating:
        raise ValueError(f"battery_input: {ibat_max:g} A exceeds {pol.connector_derating:g} x the connector rating ({connector_rating_a:g} A); choose a bigger connector or split the bus")
    j = ctx.generic(connector_symbol, connector_footprint)
    raw, bus, gnd = s.Net("RAW"), s.Net(rail), s.Net("GND")
    j[1] += raw
    j[2] += gnd
    f = ctx.fuse(ibat_max, vbat_max)
    raw & f & bus
    tvs = ctx.tvs(vbat_max * pol.surge_margin)
    tvs["K"] += bus                      # unidirectional: cathode to the bus, anode to ground
    tvs["A"] += gnd
    caps = ctx.electrolytics(c_bulk, vbat_max)
    for c in caps:
        bus & c[1]
        c[2] += gnd
    res = Result(calcs={"battery_input": f"{vbat_min:g}-{vbat_max:g} V, {ibat_max:g} A; fuse {f.value}; TVS {tvs.value}; bulk {len(caps)} x {caps[0].value}"})
    res.warnings += [f"not in the parts catalogue (no price/stock): {n}" for n in ctx.unsourced]
    res.ports = [PortSpec("vbus", rail, "power", "out", vbat_nom, ibat_max, meta=dict(vmin=vbat_min, vmax=vbat_max, source="battery")),
                 PortSpec("src", "RAW", "power", "in", vbat_nom, ibat_max, meta=dict(vmin=vbat_min, vmax=vbat_max, external=True)),
                 PortSpec("gnd", "GND", "ground")]
    return res
