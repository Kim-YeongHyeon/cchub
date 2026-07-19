from pathlib import Path

import pytest

from cchub.config import Config, ConfigError, cchub_dir, load_config


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text)
    return p


def test_load_full_config(tmp_path):
    p = write(tmp_path, """
[general]
sync_interval = 10
stats_interval = 1

[servers.srv1]
host = "user@10.0.0.11"
results = ["~/exp/**"]

[servers.srv2]
host = "srv2-alias"
""")
    cfg = load_config(p)
    assert cfg.sync_interval == 10
    assert cfg.stats_interval == 1
    assert cfg.servers["srv1"].host == "user@10.0.0.11"
    assert cfg.servers["srv1"].results == ["~/exp/**"]
    assert cfg.servers["srv2"].results == []
    assert cfg.servers["srv2"].claude_dir == "~/.claude"


def test_defaults_when_general_missing(tmp_path):
    p = write(tmp_path, '[servers.a]\nhost = "h"\n')
    cfg = load_config(p)
    assert cfg.sync_interval == 30
    assert cfg.stats_interval == 2


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_server_without_host_raises(tmp_path):
    p = write(tmp_path, "[servers.a]\nresults = []\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_cchub_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CCHUB_DIR", str(tmp_path / "x"))
    assert cchub_dir() == tmp_path / "x"
