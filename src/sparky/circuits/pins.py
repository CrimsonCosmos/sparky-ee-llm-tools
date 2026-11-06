"""Pin roles resolved from the KiCad symbol itself: pin NAME patterns plus the pin's electrical type. Never hand-written pin numbers."""
from __future__ import annotations

import re

_ROLE = {
    "vin": r"^(VIN|IN|VI|PVIN|AVIN|VCC|VDD|V\+|VS|VBUS)\d*$",
    "vout": r"^(VOUT|OUT|VO|VREG)\d*$",
    "sw": r"^(SW|LX|PH|SWITCH|SW_?\d)$",
    "boot": r"^(BOOT|VBST|BST|CB|BOOTSTRAP)$",
    "fb": r"^(FB|VFB|VSENSE|ADJ|SENSE)$",
    "en": r"^(EN|ENA|ENABLE|ON/OFF|~?SHDN|~?\{?SHDN\}?)$",
    "gnd": r"^(GND|PGND|AGND|DGND|VSS|V-|COM)\w*$",
    "pad": r"^(PAD|EP|EPAD|DAP|TPAD|POWERPAD|THERMAL|TAB)$",
    "comp": r"^COMP$",
    "supply": r"^(VDD|VDDA|VDDIO\d*|VDDUSB|VCC|VCCIO\d*|VBAT|VREF\+)(_\d+)?$",
    "txd": r"^(TXD|TX|D|DIN)$",
    "rxd": r"^(RXD|RX|R|ROUT)$",
    "canh": r"^CAN_?H$",
    "canl": r"^CAN_?L$",
    "slope": r"^(RS|S|SLOPE)$",
}
# SKiDL pin function codes: 7 = PWRIN, 8 = PWROUT
_FUNC = {"vin": {7}, "vout": {8}}


def pins(part, role: str, required: bool = True) -> list:
    """All pins of `part` playing `role`; by name first, else by electrical type for vin/vout. Raises if a required role has no pin."""
    found = [p for p in part.pins if re.match(_ROLE[role], str(p.name), re.I)]
    if not found and role in _FUNC:
        found = [p for p in part.pins if int(getattr(p, "func", 0)) in _FUNC[role] and not re.match(_ROLE["gnd"], str(p.name), re.I)]
    if required and not found:
        raise LookupError(f"{part.name}: no pin for role '{role}' (pins: {[str(p.name) for p in part.pins]})")
    return found


def pin_for_function(part, function: str, exclude: set[str] | None = None):
    """The pin whose alternate-function list (from the KiCad symbol, e.g. 'USART1_TX/PA9/TIM1_CH2') has an entry matching regex `function` (plain names match literally); None if absent.
    `exclude` = pin names already used. Raises if several pins offer it and none is unambiguous (caller must name the pin)."""
    exclude = exclude or set()
    hits = [p for p in part.pins if str(p.name) not in exclude and any(re.fullmatch(function, a, re.I) for a in str(getattr(p, "aliases", p.name)).split("/"))]
    hits = [p for p in hits if not re.fullmatch(r"p\d+", str(p.name))] or hits
    if len(hits) > 1:
        names = sorted({str(p.name) for p in hits})
        if len(names) > 1:
            raise LookupError(f"{part.name}: {function} is available on several pins {names}; name the pin explicitly")
    return hits[0] if hits else None
