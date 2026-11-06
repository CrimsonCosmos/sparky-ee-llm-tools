import sys

from sparky import toolchain as tc


def _clear():
    for f in (tc.kicad_cli, tc.kicad_version, tc.kicad_python):
        f.cache_clear()


def test_env_overrides_win(monkeypatch, tmp_path):
    exe = tmp_path / "kicad-cli"
    exe.write_text("")
    monkeypatch.setenv("SPARKY_KICAD_CLI", str(exe))
    monkeypatch.setenv("KICAD_FOOTPRINT_DIR", str(tmp_path))
    monkeypatch.setenv("SPARKY_JAVA", "/x/java")
    _clear()
    assert tc.kicad_cli() == str(exe)
    assert tc.kicad_library_dir("footprints") == str(tmp_path)
    assert tc.java() == "/x/java"
    _clear()


def test_missing_tools_return_none_not_raise(monkeypatch):
    monkeypatch.delenv("SPARKY_KICAD_CLI", raising=False)
    monkeypatch.setattr(tc.shutil, "which", lambda n: None)
    monkeypatch.setattr(tc, "_kicad_roots", lambda: [])
    _clear()
    assert tc.kicad_cli() is None and tc.kicad_version() is None and tc.kicad_has("pcb_drc") is False
    _clear()


def test_feature_gates_follow_version(monkeypatch):
    monkeypatch.setattr(tc, "kicad_version", lambda: (7, 0, 11))
    assert not tc.kicad_has("pcb_drc") and not tc.kicad_has("sch_erc")
    monkeypatch.setattr(tc, "kicad_version", lambda: (10, 0, 8))
    assert tc.kicad_has("pcb_drc") and tc.kicad_has("sch_erc") and tc.kicad_has("ipc_api") and tc.kicad_has("ipc2581")


def test_windows_and_mac_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "_WIN", True)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    (tmp_path / "KiCad" / "10.0").mkdir(parents=True)
    assert tc._kicad_roots() == [str(tmp_path / "KiCad" / "10.0")]
    monkeypatch.setattr(tc, "_WIN", False)
    monkeypatch.setattr(tc, "_MAC", True)
    assert tc._kicad_roots()[0].startswith("/Applications/KiCad")


def test_report_has_all_keys():
    assert {"kicad-cli", "java", "ngspice", "platform"} <= set(tc.report())
    assert tc.report()["platform"] == sys.platform
