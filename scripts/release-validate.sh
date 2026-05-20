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

if python3 -c 'import pytest' >/dev/null 2>&1; then
  python3 -m pytest
else
  uv run --with pytest python -m pytest
fi

doctor_state_dir="$(mktemp -d)"
PUPPETMASTER_STATE_DIR="$doctor_state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli doctor --deep

discord_state_dir="$(mktemp -d)"
echo "Running non-network Discord config and registry checks."
PUPPETMASTER_STATE_DIR="$discord_state_dir" PYTHONPATH="$repo/src" python3 - <<'PY'
from puppetmaster.config import load_config
from puppetmaster.discord_bot import validate_discord_config
from puppetmaster.errors import PuppetError
from puppetmaster.registry import Registry

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
print("Discord config loads and schema initializes; live puppet discord serve is intentionally skipped.")
PY

state_dir="$(mktemp -d)"
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli debug create-raw \
  --cwd "$repo" \
  --description "release validation raw smoke" \
  --command "bash -lc 'echo puppetmaster-smoke; sleep 2'" \
  --json
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli agent list
