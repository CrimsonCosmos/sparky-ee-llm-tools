from sparky.circuits.pins import pin_for_function, pins
from sparky.datasheet import need as datasheet
from sparky.recipes import Param, PortSpec, Result

META = dict(name="mcu_core", summary="Microcontroller core: supply/ground pins from the KiCad symbol, datasheet bypass/reset/boot network, SWD header, "
                                     "and named signals bound to pins by alternate function (or explicit pin).", category="compute")
PARAMS = [Param("symbol", "string", None, "KiCad symbol of the MCU (e.g. MCU_ST_STM32F1:STM32F103C8Tx)", True),
          Param("vdd", "number", None, "supply voltage (V)", True), Param("rail", "string", "+3V3", "supply net name", False),
          Param("signals", "dict", None, "{net_name: {'function': regex} | {'pin': 'PA9'}, 'kind': 'uart|spi|i2c|can|pwm|gpio|analog'} - one entry per external signal", True),
          Param("swd_header_symbol", "string", None, "KiCad symbol of a 1x04 header for SWD (pins: 1=VDD 2=SWDIO 3=SWCLK 4=GND)", True),
          Param("swd_header_footprint", "string", None, "KiCad footprint for the SWD header", True)]
DATASHEET = {"vdd_min": "minimum supply, V", "vdd_max": "maximum supply, V", "c_bypass_pin": "bypass capacitor per supply pin, F",
             "c_bulk": "bulk capacitor on the supply, F", "c_nrst": "capacitor from NRST to ground, F", "r_boot0": "BOOT0 pull-down resistor, ohm"}
SWD = {"swdio": r".*SWDIO", "swclk": r".*SWCLK"}


def build(ctx, symbol, vdd, rail, signals, swd_header_symbol, swd_header_footprint):
    s = ctx.s
    u, ch = ctx.ic(symbol)
    ds = datasheet(u.name, DATASHEET, ch.datasheet if ch else "")
    if not ds["vdd_min"] <= vdd <= ds["vdd_max"]:
        raise ValueError(f"mcu_core: {u.name} supply range is {ds['vdd_min']:g}-{ds['vdd_max']:g} V, requested {vdd:g} V")
    vnet, gnd = s.Net(rail), s.Net("GND")
    sup = pins(u, "supply")
    for p in sup:
        p += vnet
    for p in pins(u, "gnd"):
        p += gnd
    for _ in sup:                                    # one bypass capacitor per supply pin, as the datasheet's power-supply scheme requires
        vnet & ctx.capacitor(ds["c_bypass_pin"], vdd) & gnd       # datasheet values are NOMINAL part values: no DC-bias derating here
    vnet & ctx.capacitor(ds["c_bulk"], vdd) & gnd
    nrst = s.Net("NRST")
    for p in pins_named(u, "NRST"):
        p += nrst
    nrst & ctx.capacitor(ds["c_nrst"], vdd) & gnd
    for p in pins_named(u, "BOOT0"):
        r = ctx.resistor(ds["r_boot0"])
        p += r[1]
        r[2] += gnd
    hdr = ctx.generic(swd_header_symbol, swd_header_footprint)
    swdio, swclk = s.Net("SWDIO"), s.Net("SWCLK")
    for key, net in (("swdio", swdio), ("swclk", swclk)):
        p = pin_for_function(u, SWD[key])
        if p is None:
            raise LookupError(f"{u.name}: no pin with a {key.upper()} function")
        p += net
    hdr[1] += vnet
    hdr[2] += swdio
    hdr[3] += swclk
    hdr[4] += gnd
    used, ports = {"NRST", "BOOT0"} | {str(p.name) for p in sup}, []
    for net_name, spec in signals.items():
        if "pin" in spec:
            cands = [p for p in u.pins if str(p.name) == spec["pin"]]
            p = cands[0] if cands else None
        else:
            p = pin_for_function(u, spec["function"], exclude=used)
        if p is None:
            raise LookupError(f"{u.name}: no pin for signal {net_name} ({spec})")
        used.add(str(p.name))
        n = s.Net(net_name)
        p += n
        ports.append(PortSpec(net_name, net_name, spec.get("kind", "gpio"), "bidir", vdd, 0.0, meta=dict(pin=str(p.name))))
    res = Result(calcs={"mcu": f"{u.name}: {len(sup)} supply pins bypassed, SWD header, {len(signals)} signals bound ({', '.join(f'{k}->{ports[i].meta['pin']}' for i, k in enumerate(signals))})"})
    res.warnings += [f"not in the parts catalogue (no price/stock): {n}" for n in ctx.unsourced]
    res.ports = ports + [PortSpec("vcc", rail, "power", "in", vdd, 0.0, meta=dict(vmin=ds["vdd_min"], vmax=ds["vdd_max"])), PortSpec("gnd", "GND", "ground")]
    return res


def pins_named(part, name):
    return [p for p in part.pins if str(p.name).upper() == name]
