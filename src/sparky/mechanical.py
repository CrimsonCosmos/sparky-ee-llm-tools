"""Mechanical integration: OUT OF SCOPE for sparky.

What exists: the default board floor plan is small and roughly square (pcb.placement), and each package includes `<project>.step` exported by
`kicad-cli pcb export step` (board body, pads, tracks, zones; 3D component models only if the footprints carry them).

How a mechanical integration could be done (not implemented, not planned):
  * Import the STEP into FreeCAD or CadQuery (both open source, Python-scriptable), place it in an enclosure/chassis assembly, and check clearances,
    connector access and mounting-hole alignment against the chassis model.
  * Feed the enclosure's measured thermal environment back as `ambient_c` / convection coefficient into the thermal analysis.
  * Feed the chassis envelope back as `BoardSpec.max_width_mm/max_height_mm` (already honoured by the flow) and mounting-hole positions as fixed components.
Anything beyond the STEP hand-off is the user's mechanical engineering toolchain; sparky will not grow a mechanical design module.
"""
