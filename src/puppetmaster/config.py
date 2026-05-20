from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import PuppetError


@dataclass(frozen=True)
class Limits:
    max_depth: int = 3
    max_concurrent_children_per_agent: int = 5
    max_total_agents: int = 30
    max_event_prompt_events: int = 5
    max_wait_seconds: int = 3600
    default_log_lines: int = 120
    max_log_read_lines: int = 2000


@dataclass(frozen=True)
class DiscordConfig:
    token: str | None = None
    guild_id: int | None = None
    poll_interval_seconds: float = 1.0
    typing_timeout_seconds: float = 300.0
    chunk_size: int = 1900
    max_chunks: int = 3


@dataclass(frozen=True)
class Config:
    repo_dir: Path
    state_dir: Path
    tmux_session_prefix: str
    limits: Limits
    discord: DiscordConfig
    codex_no_alt_screen: bool = True
    codex_bypass_approvals_and_sandbox: bool = True

    @property
    def agents_dir(self) -> Path:
        return self.state_dir / "agents"

    @property
    def registry_path(self) -> Path:
        return self.state_dir / "registry.sqlite"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "puppetmaster.log.jsonl"


def _repo_dir() -> Path:
    return Path.cwd().resolve()


def _read_local_config(state_dir: Path) -> dict:
    config_path = state_dir / "config.toml"
    if not config_path.exists():
        return {}
    return tomllib.loads(config_path.read_text(encoding="utf-8"))


def _positive_float(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise PuppetError("invalid_config", f"discord.{field} must be positive")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise PuppetError("invalid_config", f"discord.{field} must be positive") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise PuppetError("invalid_config", f"discord.{field} must be positive")
    return parsed


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise PuppetError("invalid_config", f"discord.{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PuppetError("invalid_config", f"discord.{field} must be a positive integer") from exc
    if str(value).strip() != str(parsed) and not isinstance(value, int):
        raise PuppetError("invalid_config", f"discord.{field} must be a positive integer")
    if parsed <= 0:
        raise PuppetError("invalid_config", f"discord.{field} must be a positive integer")
    return parsed


def _parse_guild_id(value: object) -> int | None:
    if value == "" or value is None:
        return None
    if isinstance(value, bool):
        raise PuppetError("invalid_config", "discord.guild_id must be an integer or numeric string")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            return int(stripped)
    raise PuppetError("invalid_config", "discord.guild_id must be an integer or numeric string")


def _parse_discord_config(raw: dict) -> DiscordConfig:
    discord_raw = raw.get("discord", {})
    if not isinstance(discord_raw, dict):
        raise PuppetError("invalid_config", "discord config must be a table")
    token_raw = discord_raw.get("token", "")
    token = None if token_raw == "" else str(token_raw)
    chunk_size = _positive_int(discord_raw.get("chunk_size", 1900), "chunk_size")
    if chunk_size > 1900:
        raise PuppetError("invalid_config", "discord.chunk_size must be no greater than 1900")
    return DiscordConfig(
        token=token,
        guild_id=_parse_guild_id(discord_raw.get("guild_id", "")),
        poll_interval_seconds=_positive_float(discord_raw.get("poll_interval_seconds", 1.0), "poll_interval_seconds"),
        typing_timeout_seconds=_positive_float(
            discord_raw.get("typing_timeout_seconds", 300.0),
            "typing_timeout_seconds",
        ),
        chunk_size=chunk_size,
        max_chunks=_positive_int(discord_raw.get("max_chunks", 3), "max_chunks"),
    )


def load_config() -> Config:
    repo = _repo_dir()
    state_dir = Path(os.environ.get("PUPPETMASTER_STATE_DIR", repo / ".puppetmaster")).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    raw = _read_local_config(state_dir)
    limit_raw = raw.get("limits", {})
    codex_raw = raw.get("codex", {})
    max_concurrent_children = (
        os.environ.get("PUPPETMASTER_MAX_CONCURRENT_CHILDREN")
        or os.environ.get("PUPPETMASTER_MAX_CHILDREN")
        or limit_raw.get("max_concurrent_children_per_agent")
        or limit_raw.get("max_children_per_agent", 5)
    )
    limits = Limits(
        max_depth=int(os.environ.get("PUPPETMASTER_MAX_DEPTH", limit_raw.get("max_depth", 3))),
        max_concurrent_children_per_agent=int(max_concurrent_children),
        max_total_agents=int(os.environ.get("PUPPETMASTER_MAX_TOTAL_AGENTS", limit_raw.get("max_total_agents", 30))),
        max_event_prompt_events=int(limit_raw.get("max_event_prompt_events", 5)),
        max_wait_seconds=int(limit_raw.get("max_wait_seconds", 3600)),
        default_log_lines=int(limit_raw.get("default_log_lines", 120)),
        max_log_read_lines=int(limit_raw.get("max_log_read_lines", 2000)),
    )
    cfg = Config(
        repo_dir=repo,
        state_dir=state_dir,
        tmux_session_prefix=os.environ.get("PUPPETMASTER_TMUX_PREFIX", raw.get("tmux_session_prefix", "puppet_")),
        limits=limits,
        discord=_parse_discord_config(raw),
        codex_no_alt_screen=bool(codex_raw.get("no_alt_screen", True)),
        codex_bypass_approvals_and_sandbox=bool(codex_raw.get("bypass_approvals_and_sandbox", True)),
    )
    ensure_state(cfg)
    return cfg


def ensure_state(config: Config) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.agents_dir.mkdir(parents=True, exist_ok=True)
    default_config = config.state_dir / "config.toml"
    if not default_config.exists():
        default_config.write_text(
            """[limits]
max_depth = 3
max_concurrent_children_per_agent = 5
max_total_agents = 30
max_event_prompt_events = 5
max_wait_seconds = 3600
default_log_lines = 120
max_log_read_lines = 2000

[codex]
no_alt_screen = true
bypass_approvals_and_sandbox = true

[discord]
token = ""
guild_id = ""
poll_interval_seconds = 1
typing_timeout_seconds = 300
chunk_size = 1900
max_chunks = 3
""",
            encoding="utf-8",
        )
    else:
        raw = tomllib.loads(default_config.read_text(encoding="utf-8"))
        if "discord" not in raw:
            with default_config.open("a", encoding="utf-8") as fh:
                fh.write(
                    """
[discord]
token = ""
guild_id = ""
poll_interval_seconds = 1
typing_timeout_seconds = 300
chunk_size = 1900
max_chunks = 3
"""
                )
