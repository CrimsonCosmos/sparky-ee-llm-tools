"""Requirement -> `Need` translations. The only knowledge here is the catalogue's attribute vocabulary (verified against the live data);
no part values or part numbers. Every function returns a `Need`; call `Catalog.pick(need, qty)`."""
from __future__ import annotations

from ..units import fmt_si
from .catalog import Need

_SMD_PKG = ("0402", "0603", "0805", "1206", "1210")


def resistor(ohms: float, tolerance_pct: float = 1.0, package: tuple[str, ...] = ("0402", "0603"), min_power_w: float | None = None) -> Need:
    n = Need("Chip Resistor - Surface Mount", package=package, near={"Resistance": (f"{fmt_si(ohms)}ohm", 0.001)})
    n.maximum["Tolerance"] = f"{tolerance_pct}%"
    return n


def mlcc(farads: float, volts_min: float, package: tuple[str, ...] = ("0402", "0603", "0805", "1206"), dielectric: tuple[str, ...] | None = None) -> Need:
    n = Need("Multilayer Ceramic Capacitors MLCC - SMD/SMT", package=package, near={"Capacitance": (f"{fmt_si(farads)}F", 0.05)},
             minimum={"Voltage Rating": f"{volts_min}V"})
    return n


def inductor(henries: float, isat_min: float, max_over: float = 0.5, dcr_max_ohm: float | None = None) -> Need:
    """Inductance must be AT LEAST the design value (less raises ripple/ISAT) and at most `max_over` above it (more costs size and loop speed)."""
    n = Need("Power Inductors", minimum={"Inductance": f"{fmt_si(henries * 0.98)}H", "Current - Saturation(Isat)": f"{isat_min}A"},
             maximum={"Inductance": f"{fmt_si(henries * (1 + max_over))}H"})
    if dcr_max_ohm is not None:
        n.maximum["DC Resistance(DCR)"] = f"{dcr_max_ohm}ohm"
    return n


def mosfet(vds_min: float, id_min: float, rds_on_max_ohm: float | None = None, channel: str = "N-Channel") -> Need:
    n = Need("MOSFETs", equals={"Type": channel}, minimum={"Drain to Source Voltage": f"{vds_min}V", "Current - Continuous Drain(Id)": f"{id_min}A"})
    if rds_on_max_ohm is not None:
        n.maximum["RDS(on)"] = f"{rds_on_max_ohm}ohm"
    return n


def schottky(vr_min: float, i_min: float) -> Need:
    return Need("Schottky Diodes", minimum={"Voltage - DC Reverse(Vr)": f"{vr_min}V", "Current - Rectified": f"{i_min}A"})


def tvs(standoff_min: float, max_over: float = 0.35) -> Need:
    """Stand-off just above the working voltage: a much higher rating clamps far above what the protected parts can take."""
    return Need("ESD And Surge Protection (TVS/ESD)", minimum={"Reverse Stand-Off Voltage (Vrwm)": f"{standoff_min}V"},
                maximum={"Reverse Stand-Off Voltage (Vrwm)": f"{standoff_min * (1 + max_over):.4g}V"})


def ldo(vout: float, iout_min: float) -> Need:
    return Need("Voltage Regulators - Linear, Low Drop Out (LDO) Regulators", near={"Output Voltage": (f"{vout}V", 0.01)}, minimum={"Output Current": f"{iout_min}A"})


def buck_regulator(vin_min: float, vin_max: float, iout_min: float, adjustable: bool = True) -> Need:
    """Step-down regulator whose supply range covers the whole input window and whose output current covers the load."""
    n = Need("DC-DC Converters", equals={"Topology": "Buck"}, minimum={"Output Current": f"{iout_min}A"}, covers={"Voltage - Supply": (f"{vin_min}V", f"{vin_max}V")})
    if adjustable:
        n.equals["Output Type"] = "Adjustable"
    return n


def mlcc_between(low_f: float, high_f: float, volts_min: float, package: tuple[str, ...] = _SMD_PKG) -> Need:
    """MLCC whose nominal capacitance lies in [low_f, high_f] and whose rating is at least volts_min."""
    return Need("Multilayer Ceramic Capacitors MLCC - SMD/SMT", package=package, minimum={"Capacitance": f"{fmt_si(low_f)}F", "Voltage Rating": f"{volts_min}V"},
                maximum={"Capacitance": f"{fmt_si(high_f)}F"})


def electrolytic(farads_min: float, volts_min: float, farads_max: float) -> Need:
    return Need("Aluminum Electrolytic Capacitors - SMD", minimum={"Capacitance": f"{fmt_si(farads_min)}F", "Voltage Rating": f"{volts_min}V"},
                maximum={"Capacitance": f"{fmt_si(farads_max)}F"})


def fuse(amps_min: float, volts_min: float, package: tuple[str, ...]) -> Need:
    return Need("Fuses", package=package, minimum={"Current Rating": f"{amps_min}A", "Voltage Rating": f"{volts_min}V"})
