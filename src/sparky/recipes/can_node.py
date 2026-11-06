from sparky.circuits.pins import pins
from sparky.datasheet import need as datasheet
from sparky.recipes import Param, PortSpec, Result

CAN_TERMINATION_OHM = 120.0        # ISO 11898-2 nominal bus termination (a standard value, not a design choice)

META = dict(name="can_node", summary="CAN transceiver node: KiCad symbol chosen by the designer, supply/bit-rate spec-checked against datasheet numbers, "
                                     "bypass cap from the datasheet, optional ISO 11898 termination.", category="comms")
PARAMS = [Param("symbol", "string", None, "KiCad symbol of the CAN transceiver (e.g. Interface_CAN_LIN:SN65HVD230)", True),
          Param("vcc", "number", None, "logic supply (V)", True), Param("bitrate", "number", None, "bus speed (bit/s)", True),
          Param("terminated", "bool", False, "fit the 120 ohm termination resistor (end nodes only)", False),
          Param("termination_jumper_footprint", "string", None, "KiCad footprint of a 2-pin solder jumper in series with the termination (else fixed)", False)]
DATASHEET = {"vcc_min": "minimum supply, V", "vcc_max": "maximum supply, V", "bitrate_max": "maximum bit rate, bit/s", "c_bypass": "recommended VCC bypass capacitor, F", "icc_max": "maximum supply current, A"}


def ports(policy=None, vcc=None, vcc_min=None, vcc_max=None, symbol=None, icc_max=None, **_):
    if icc_max is None and symbol:
        from sparky.datasheet import load
        icc_max = load(symbol.split(":")[-1]).get("values", {}).get("icc_max")
    return [PortSpec("logic", "TXD", "can_logic", "bidir", vcc or 0.0, signals={"tx": "TXD", "rx": "RXD"}, meta=dict(role="device")),
            PortSpec("bus", "CANH", "can_bus", "bidir", 0.0, signals={"canh": "CANH", "canl": "CANL"}, meta=dict(role="peer", needs_connector=True)),
            PortSpec("vcc", "VCC", "power", "in", vcc or 0.0, icc_max or 0.0, meta=dict(vmin=vcc_min, vmax=vcc_max)), PortSpec("gnd", "GND", "ground")]

def build(ctx, symbol, vcc, bitrate, terminated=False, termination_jumper_footprint=None):
    s = ctx.s
    u, ch = ctx.ic(symbol)
    ds = datasheet(u.name, DATASHEET, ch.datasheet if ch else "")
    if not ds["vcc_min"] <= vcc <= ds["vcc_max"]:
        raise ValueError(f"can_node: {u.name} supply range is {ds['vcc_min']:g}-{ds['vcc_max']:g} V, requested {vcc:g} V")
    if bitrate > ds["bitrate_max"]:
        raise ValueError(f"can_node: {u.name} supports up to {ds['bitrate_max']:g} bit/s, requested {bitrate:g}")
    u.fields["PowerW"] = vcc * ds["icc_max"]         # worst-case supply power (datasheet maximum supply current x VCC)
    vnet, gnd, txd, rxd, canh, canl = s.Net("VCC"), s.Net("GND"), s.Net("TXD"), s.Net("RXD"), s.Net("CANH"), s.Net("CANL")
    for role, net in (("vin", vnet), ("gnd", gnd), ("txd", txd), ("rxd", rxd), ("canh", canh), ("canl", canl)):
        for p in pins(u, role):
            p += net
    for p in pins(u, "slope", required=False):
        p += gnd                                     # slope/standby pin to ground = high-speed mode (datasheet table of operating modes)
    vnet & ctx.capacitor(ds["c_bypass"], vcc) & gnd              # datasheet value = nominal part value
    if terminated:
        r = ctx.resistor(CAN_TERMINATION_OHM)
        if termination_jumper_footprint:
            jp = ctx.generic("Jumper:SolderJumper_2_Open", termination_jumper_footprint)
            term = s.Net("TERM")
            canh & r & term & jp & canl
        else:
            canh & r & canl
    res = Result(calcs={"can": f"{u.name}: {bitrate / 1e3:g} kbit/s at {vcc:g} V, slope pin to GND, termination "
                               f"{'none' if not terminated else 'jumper-selectable' if termination_jumper_footprint else 'fixed'} {CAN_TERMINATION_OHM:g} ohm"})
    res.warnings += [f"not in the parts catalogue (no price/stock): {n}" for n in ctx.unsourced]
    res.ports = ports(None, vcc, ds["vcc_min"], ds["vcc_max"], icc_max=ds["icc_max"])
    return res
