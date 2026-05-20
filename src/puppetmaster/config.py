from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


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
class Config:
    repo_dir: Path
    state_dir: Path
    tmux_session_prefix: str
    limits: Limits
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
""",
            encoding="utf-8",
        )
