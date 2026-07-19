from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


def cchub_dir() -> Path:
    return Path(os.environ.get("CCHUB_DIR", "~/.cchub")).expanduser()


@dataclass
class ServerConfig:
    name: str
    host: str
    results: list[str] = field(default_factory=list)
    claude_dir: str = "~/.claude"


@dataclass
class Config:
    sync_interval: int = 30
    stats_interval: int = 2
    servers: dict[str, ServerConfig] = field(default_factory=dict)


def load_config(path: Path | None = None) -> Config:
    path = path or cchub_dir() / "config.toml"
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path} (cchub init으로 생성)")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: TOML 파싱 실패: {e}") from e
    general = data.get("general", {})
    servers: dict[str, ServerConfig] = {}
    for name, s in data.get("servers", {}).items():
        if "host" not in s:
            raise ConfigError(f"servers.{name}: host 항목이 없습니다")
        servers[name] = ServerConfig(
            name=name,
            host=s["host"],
            results=list(s.get("results", [])),
            claude_dir=s.get("claude_dir", "~/.claude"),
        )
    return Config(
        sync_interval=int(general.get("sync_interval", 30)),
        stats_interval=int(general.get("stats_interval", 2)),
        servers=servers,
    )
