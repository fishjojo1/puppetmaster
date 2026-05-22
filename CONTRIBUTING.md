# Contributing

Thanks for helping improve Puppetmaster.

## Development Setup

```bash
uv sync --extra dev
uv run puppet doctor --deep
uv run pytest
```

If you are not using `uv`, create a virtual environment and install the package in editable mode with the `dev` extra:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Validation

Before opening a pull request, run:

```bash
scripts/release-validate.sh
```

The release validation script runs the test suite, local doctor checks, Discord schema/config smoke checks, and a tmux-backed raw-agent smoke check. Live Codex workflow validation still requires a local Codex installation and credentials.

## Safety

Puppetmaster intentionally runs managed Codex sessions with broad local permissions. Avoid adding behavior that expands remote control or filesystem access without explicit documentation, tests, and recovery guidance.

Do not commit local state, logs, tokens, or generated agent directories. The repository `.gitignore` excludes `.env`, `.puppetmaster/`, virtual environments, build artifacts, and cache files.

## Pull Request Checklist

- Tests or validation cover the changed behavior.
- Public CLI or Discord command changes are reflected in `README.md`.
- New state files, config keys, or operational risks are documented.
- User-facing errors include actionable hints where possible.
