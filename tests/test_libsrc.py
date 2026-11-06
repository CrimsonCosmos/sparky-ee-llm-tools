from sparky import libsrc
from sparky.kicad_mod import parse_footprint

SYM = '''(kicad_symbol_lib (symbol "X" (property "Reference" "U")
  (symbol "X_1_1"
    (pin unspecified line (at 0 0 0) (length 2) (name "VIN" (effects (font (size 1 1)))) (number "1" (effects (font (size 1 1)))))
    (pin unspecified line (at 0 0 0) (length 2) (name "GND" (effects (font (size 1 1)))) (number "2" (effects (font (size 1 1)))))
    (pin unspecified line (at 0 0 0) (length 2) (name "FB" (effects (font (size 1 1)))) (number "3" (effects (font (size 1 1)))))
)))'''


def test_unspecified_pin_types_are_inferred_from_names():
    out = libsrc._infer_types(SYM)
    assert out.count("(pin power_in") == 2 and out.count("(pin passive") == 1 and "unspecified" not in out


FP = '''(footprint "F" (layer "F.Cu")
 (fp_line (start -1.95 -2.45) (end 1.95 -2.45) (layer "F.CrtYd") (width 0.05))
 (fp_line (start 1.95 2.45) (end -1.95 2.45) (layer "F.CrtYd") (width 0.05))
 (pad 1 smd rect (at -2.7 0 0) (size 1.3 0.6) (layers "F.Cu" "F.Paste" "F.Mask"))
 (pad 2 smd rect (at 2.7 0 0) (size 1.3 0.6) (layers "F.Cu" "F.Paste" "F.Mask")))'''


def test_courtyard_is_extended_to_enclose_pads():
    # vendor courtyard is the 3.9 x 4.9 body; pads reach x = +-(2.7 + 0.65) = +-3.35
    x0, y0, x1, y1 = parse_footprint(FP).courtyard
    assert (x0, x1) == (-3.35, 3.35) and (y0, y1) == (-2.45, 2.45)
