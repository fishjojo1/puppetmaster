# Validation Plan

## Automated Tests

Config creation:

- `puppet init --no-input` creates the global state directory.
- `puppet init --no-input` creates config with default sections.
- Empty Discord values remain empty when no flags are provided.

Non-interactive values:

- `--discord-token` writes the token.
- `--discord-guild-id` writes the guild id.
- `--json` reports token configured without leaking the token.
- Invalid guild id fails with a clear config error.

Preservation:

- Existing token is preserved when no new token is provided.
- Existing guild id is preserved when no new guild id is provided.
- Existing limit and codex values are preserved.
- Unknown config sections are preserved if the implementation supports that.

Interactive prompting:

- Token prompt uses hidden input.
- Blank token keeps the existing token.
- Blank guild id keeps the existing guild id.
- Interactive start prompt starts the background Discord bot when accepted.

Discord startup:

- `--start-discord` calls the same background start path as `puppet discord serve --background`.
- Existing running bot is not duplicated.
- Startup output includes pid and log path but not token.

Regression:

- Existing config loading tests pass.
- Existing Discord validation tests pass.
- Existing CLI error formatting works for `puppet init`.

## Manual Checks

Fresh interactive init:

```bash
PUPPETMASTER_STATE_DIR="$(mktemp -d)" puppet init
```

Expected:

- Token input is hidden.
- Guild id is accepted.
- Config file is written.
- Output does not display the token.

Non-interactive init:

```bash
state_dir="$(mktemp -d)"
PUPPETMASTER_STATE_DIR="$state_dir" puppet init \
  --discord-token "test-token" \
  --discord-guild-id 123456789 \
  --no-input \
  --json
```

Expected:

- JSON says token is configured.
- JSON includes guild id.
- JSON does not include `test-token`.

Rerun preservation:

```bash
PUPPETMASTER_STATE_DIR="$state_dir" puppet init --no-input --json
```

Expected:

- Existing Discord token remains configured.
- Existing guild id remains present.

## Acceptance Criteria

- A fresh user can create global config with `puppet init`.
- Init can be automated without prompts.
- Secrets are never echoed in command output.
- Rerunning init is safe.
