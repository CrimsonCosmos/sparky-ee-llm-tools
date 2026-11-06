import hashlib

import pytest

from sparky import routing


def test_last_json_parses_freerouting_summary():
    out = 'noise\nmore noise\n{\n  "clearance_violations": {"total_count": 0},\n  "through_hole_count": 3\n}\n'
    assert routing._last_json(out) == {"clearance_violations": {"total_count": 0}, "through_hole_count": 3}
    assert routing._last_json("no json here") == {}


def test_jar_hash_is_enforced(tmp_path, monkeypatch):
    bad = tmp_path / "freerouting.jar"
    bad.write_bytes(b"not the real jar")
    monkeypatch.setenv("SPARKY_FREEROUTING_JAR", str(bad))
    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def iter_content(self, n): yield b"tampered"
        def __enter__(self): return self
        def __exit__(self, *a): pass
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    with pytest.raises(routing.RoutingError, match="pinned SHA-256"):
        routing.ensure_jar()
    assert hashlib.sha256(b"tampered").hexdigest() != routing.FREEROUTING_SHA256


def test_missing_java_is_actionable(monkeypatch):
    monkeypatch.setattr(routing, "java", lambda: None)
    with pytest.raises(routing.RoutingError, match="Java"):
        routing.route_pcb("a.kicad_pcb", "b.kicad_pcb")
