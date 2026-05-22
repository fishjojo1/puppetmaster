#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"
discord_state_dir=""
doctor_state_dir=""
state_dir=""
cleanup() {
  if [[ -n "$discord_state_dir" ]]; then
    rm -rf "$discord_state_dir"
  fi
  if [[ -n "$doctor_state_dir" ]]; then
    rm -rf "$doctor_state_dir"
  fi
  if [[ -n "$state_dir" ]]; then
    rm -rf "$state_dir"
  fi
}
trap cleanup EXIT

if command -v uv >/dev/null 2>&1; then
  uv run --extra dev pytest
  rm -rf dist
  uv run --extra dev python -m build
  uv run --extra dev twine check dist/*
elif python3 -c 'import pytest' >/dev/null 2>&1; then
  python3 -m pytest
else
  echo "pytest is not installed and uv is unavailable." >&2
  exit 1
fi

doctor_state_dir="$(mktemp -d)"
PUPPETMASTER_STATE_DIR="$doctor_state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli doctor --deep

discord_state_dir="$(mktemp -d)"
echo "Running non-network Discord config and registry checks."
PUPPETMASTER_STATE_DIR="$discord_state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli init --no-input --json
PUPPETMASTER_STATE_DIR="$discord_state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli discord status --json
PUPPETMASTER_STATE_DIR="$discord_state_dir" PYTHONPATH="$repo/src" python3 - <<'PY'
from pathlib import Path
import tempfile

from puppetmaster.config import load_config
from puppetmaster.discord_bot import validate_discord_config
from puppetmaster.errors import PuppetError
from puppetmaster.registry import Registry
from puppetmaster.services import create_agent_record

cfg = load_config()
reg = Registry(cfg)
with reg.connect() as conn:
    tables = {
        row["name"]
        for row in conn.execute(
            "select name from sqlite_master where type='table' and name in (?, ?)",
            ("discord_channel_bindings", "outbound_human_messages"),
        )
    }
assert tables == {"discord_channel_bindings", "outbound_human_messages"}
try:
    validate_discord_config(cfg)
except PuppetError as exc:
    assert exc.code == "discord_token_required"
    assert ".puppetmaster/config.toml" in (exc.hint or "")
else:
    raise AssertionError("default Discord config unexpectedly validated without a token")
with tempfile.TemporaryDirectory() as tmp:
    project_a = Path(tmp) / "project-a"
    project_b = Path(tmp) / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    root_a = create_agent_record(cfg, reg, cwd=str(project_a), description="root A", role="orchestrator")
    root_b = create_agent_record(cfg, reg, cwd=str(project_b), description="root B", role="orchestrator")
    assert root_a["id"] != root_b["id"]
    assert root_a["root_id"] == root_a["id"]
    assert root_b["root_id"] == root_b["id"]
    assert root_a["cwd"] == str(project_a)
    assert root_b["cwd"] == str(project_b)
print("Discord config/schema and two-root registry checks passed; live puppet discord serve is intentionally skipped.")
PY

state_dir="$(mktemp -d)"
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli debug create-raw \
  --cwd "$repo" \
  --description "release validation raw smoke" \
  --command "bash -lc 'echo puppetmaster-smoke; sleep 2'" \
  --json
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli agent list
