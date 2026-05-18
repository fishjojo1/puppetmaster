#!/usr/bin/env bash
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo"

python3 -m pytest
PYTHONPATH="$repo/src" python3 -m puppetmaster.cli doctor --deep

state_dir="$(mktemp -d)"
trap 'rm -rf "$state_dir"' EXIT
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli debug create-raw \
  --cwd "$repo" \
  --description "release validation raw smoke" \
  --command "bash -lc 'echo puppetmaster-smoke; sleep 2'" \
  --json
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH="$repo/src" python3 -m puppetmaster.cli agent list

