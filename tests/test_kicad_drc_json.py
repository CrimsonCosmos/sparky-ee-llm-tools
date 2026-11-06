import json

from sparky.kicad_backend.cli import parse_drc_json


def test_parse_kicad8plus_json_report():
    d = {"kicad_version": "10.0.8", "violations": [{"type": "clearance", "description": "Clearance violation", "severity": "error",
         "items": [{"description": "Track on F.Cu", "pos": {"x": 1.5, "y": 2.0}}]}, {"type": "silk_overlap", "description": "x", "severity": "warning", "items": []}],
         "unconnected_items": [{"type": "unconnected_items", "description": "Missing connection", "severity": "error", "items": []}], "schematic_parity": []}
    rep = parse_drc_json(json.dumps(d))
    assert rep.version == "10.0.8" and len(rep.errors) == 1 and len(rep.warnings) == 1 and len(rep.unconnected) == 1
    assert rep.violations[0]["items"][0] == {"x": 1.5, "y": 2.0, "desc": "Track on F.Cu"}
    assert not rep.clean and rep.by_type == {"clearance": 1, "silk_overlap": 1}


def test_empty_report_is_clean():
    assert parse_drc_json(json.dumps({"violations": [], "unconnected_items": [], "schematic_parity": []})).clean
