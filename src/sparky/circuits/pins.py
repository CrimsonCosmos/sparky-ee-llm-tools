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
