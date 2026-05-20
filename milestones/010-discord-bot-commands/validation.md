# Validation Plan

## Automated Tests

Unit-test helpers without connecting to Discord:

- `chunk_text` returns one chunk for short text.
- `chunk_text` splits long text under the configured chunk size.
- `chunk_text` caps output at `max_chunks`.
- Final chunk includes `[truncated]` when capped.
- Code block formatting still respects chunk limits.

Registry/service-backed command tests can use fake interaction/channel objects:

- `/puppet bind` rejects child agents.
- `/puppet bind` rejects missing agents.
- `/puppet bind` stores channel binding for root orchestrator.
- `/puppet unbind` removes binding.
- `/puppet status` requires a binding.
- `/puppet read` reads the bound root only.
- `/puppet agents` lists roots only.
- `/puppet tree` requires binding and renders descendants.

Config validation tests:

- Bot startup fails clearly when token is missing.
- Bot startup fails clearly when guild id is missing.

## Manual Checks

Configure `.puppetmaster/config.toml`:

```toml
[discord]
token = "<bot-token>"
guild_id = "<guild-id>"
poll_interval_seconds = 1
typing_timeout_seconds = 300
chunk_size = 1900
max_chunks = 3
```

Run:

```bash
PYTHONPATH=src python3 -m puppetmaster.cli discord serve
```

Expected:

- Bot logs in.
- Slash commands sync to the configured guild.
- `/puppet agents` appears in Discord.

Manual command flow:

1. Start or identify a root orchestrator.
2. Run `/puppet agents`.
3. Run `/puppet bind agent_id:<root-id>`.
4. Run `/puppet status`.
5. Run `/puppet read`.
6. Run `/puppet tree`.
7. Run `/puppet unbind`.
8. Confirm `/puppet status` now reports no binding.

## Acceptance Criteria

- Bot starts with config from `.puppetmaster/config.toml`.
- Guild slash commands are available without a separate sync command.
- Channel binding works and persists through bot restart.
- Commands avoid Discord embeds and use capped plain text output.
- Existing CLI, MCP, and tests continue to work.
