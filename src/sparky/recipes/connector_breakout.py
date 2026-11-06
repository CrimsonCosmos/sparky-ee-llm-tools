from sparky.recipes import Param, PortSpec, Result

META = dict(name="connector_breakout", summary="A connector whose pins are named nets (interface to another board, a harness or a purchased module).", category="interface")
PARAMS = [Param("symbol", "string", None, "KiCad symbol of the connector (pin count must equal len(pins))", True),
          Param("footprint", "string", None, "KiCad footprint of the connector", True),
          Param("pins", "list", None, "net name per pin, in pin order; the net 'GND' is ground", True),
          Param("kind", "string", "bundle", "port kind exposed (power | can_bus | uart | motor_phase | bundle ...)", False),
          Param("voltage", "number", 0.0, "highest voltage on the connector (V)", False), Param("current_a", "number", 0.0, "highest pin current (A)", False)]


def build(ctx, symbol, footprint, pins, kind, voltage, current_a):
    j = ctx.generic(symbol, footprint)
    if len(j.pins) != len(pins):
        raise ValueError(f"connector_breakout: {symbol} has {len(j.pins)} pins but {len(pins)} nets were given")
    nets = {}
    for i, name in enumerate(pins, 1):
        nets[name] = nets.get(name) or ctx.s.Net(name)
        j[i] += nets[name]
    return Result(ports=[PortSpec(n, n, "ground" if n == "GND" else kind, "bidir", voltage, current_a) for n in nets],
                  calcs={"connector": f"{symbol}: {len(pins)} pins ({', '.join(pins)})"})
