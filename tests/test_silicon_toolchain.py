"""Finding the silicon tools: configured directories first, missing tools reported as missing, versions read from the tools themselves."""
import json
import os
import stat

import pytest

from silicon import toolchain as st


@pytest.fixture
def fake(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    yosys = bin_dir / "yosys"
    yosys.write_text("#!/bin/sh\necho 'Yosys 9.99 (fake)'\n")
    yosys.chmod(yosys.stat().st_mode | stat.S_IEXEC)
    cfg = tmp_path / "env.json"
    cfg.write_text(json.dumps({"PATH": [str(bin_dir)], "PDK_ROOT": str(tmp_path / "pdk")}))
    (tmp_path / "pdk").mkdir()
    monkeypatch.setenv("SPARKY_SILICON_ENV", str(cfg))
    st.clear_cache()
    yield tmp_path
    st.clear_cache()


def test_configured_directories_come_first_and_versions_are_read_from_the_tool(fake):
    assert st.env()["PATH"].split(os.pathsep)[0] == str(fake / "bin")
    assert st.which("yosys") == str(fake / "bin" / "yosys")
    assert st.version("yosys") == "Yosys 9.99 (fake)"
    assert st.pdk_root() == fake / "pdk"


def test_missing_tools_are_missing_not_skipped(fake, monkeypatch):
    monkeypatch.setenv("PATH", str(fake / "bin"))                       # nothing else on PATH
    st.clear_cache()
    r = st.report()
    assert r["yosys"] and r["openroad"] is None and r["klayout"] is None
    assert st.available() is False                                         # the flow needs every tool: one missing means not available


def test_no_config_file_means_plain_path_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKY_SILICON_ENV", str(tmp_path / "absent.json"))
    monkeypatch.delenv("PDK_ROOT", raising=False)
    st.clear_cache()
    assert st.env()["PATH"] == os.environ["PATH"]
    assert st.pdk_root() is None
    st.clear_cache()
