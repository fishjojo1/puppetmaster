# Implementation Plan

## CLI Shape

Add:

```bash
puppet init
```

Supported flags:

```bash
puppet init --discord-token "$TOKEN"
puppet init --discord-guild-id 123456789
puppet init --start-discord
puppet init --no-input
puppet init --json
```

Behavior:

- `--no-input` disables prompts.
- `--discord-token` sets or replaces the token.
- `--discord-guild-id` sets or replaces the guild id.
- `--start-discord` starts `puppet discord serve --background` after config is written.
- `--json` returns machine-readable summary output.

## Interactive Prompting

When input is enabled:

```text
Puppetmaster home: /home/kek/.puppetmaster

Discord bot token [hidden, leave blank to keep existing]:
Discord guild ID [leave blank to keep existing]:
Start Discord bot in background now? [y/N]:
```

Rules:

- Use hidden input for the token.
- Do not print the token back to the terminal.
- Blank token keeps the existing token if one exists.
- Blank guild id keeps the existing guild id if one exists.
- If no existing token or guild id exists, blank leaves the config empty.
- Guild id validation should match existing config parsing rules.

## Config Writing

Use structured TOML writing where practical.

Requirements:

- Preserve all known existing config sections.
- Preserve unknown user config sections when feasible.
- Preserve existing values unless replacements are provided.
- Ensure required default sections exist.
- Avoid deleting comments if the implementation can reasonably preserve them, but comment preservation is not mandatory for v1.

If the standard library cannot write TOML directly, use a small deterministic writer for the known config shape and document that comments may be normalized.

## Result Output

Human output should include:

- Puppetmaster home path.
- Config path.
- Whether Discord token is configured.
- Whether Discord guild id is configured.
- Whether the bot was started.
- Next command to run.

Example:

```text
Puppetmaster home: /home/kek/.puppetmaster
Config: /home/kek/.puppetmaster/config.toml
Discord token: configured
Discord guild: 123456789

Next: puppet discord serve --background
```

JSON output should include:

```json
{
  "state_dir": "...",
  "config_path": "...",
  "discord_token_configured": true,
  "discord_guild_id": 123456789,
  "started_discord": false
}
```

Never include the token in JSON or text output.

## Discord Startup

If `--start-discord` or the interactive prompt requests startup:

- Write config first.
- Start the Discord bot in background.
- Surface the pid and log path.
- If a bot is already running, report that status instead of starting a duplicate.

## Implementation Order

1. Add config writing helper for init.
2. Add parser and command handler for `puppet init`.
3. Add interactive prompts.
4. Add non-interactive flag handling.
5. Add optional Discord startup.
6. Add docs and tests.
