# Implementation Plan

## Dependency

Add `discord.py` to default dependencies in `pyproject.toml`.

Pin broadly enough to avoid stale APIs but not too tightly. Example:

```toml
"discord.py>=2.4"
```

Update `uv.lock` or the project lockfile using the repo's normal dependency workflow.

## Module

Add `src/puppetmaster/discord_bot.py`.

Responsibilities in this milestone:

- Load `Config`, `Registry`, and `Tmux`.
- Validate Discord config:
  - `token` is required.
  - `guild_id` is required.
- Create a Discord client/bot with required intents.
- Register a command group named `puppet`.
- Auto-sync commands to the configured guild on startup.
- Implement slash commands.

Use current `discord.py` patterns:

- `discord.Intents.default()`
- `intents.message_content = True` because later milestones require mention/reply message content.
- `discord.app_commands` for slash commands.

## CLI

Add a new top-level parser group in `src/puppetmaster/cli.py`:

```bash
puppet discord serve
```

Implementation:

- Import `run_discord_bot` lazily inside the command handler.
- Return its exit code.
- Keep import failure friendly if dependency installation is broken.

## Slash Commands

Initial commands:

```text
/puppet agents
/puppet bind agent_id:<root-agent-id>
/puppet unbind
/puppet status
/puppet read lines:<optional>
/puppet tree
```

### `/puppet agents`

List root orchestrators only.

Include:

- id
- status
- name
- cwd

Do not list child agents here.

### `/puppet bind`

Accept only root orchestrators:

- Agent must exist.
- `agent["role"] == "orchestrator"`.
- Agent id must equal its root id.

Bind the current text channel to that root.

Reject:

- DMs.
- Threads.
- Non-text channels.
- Child agent ids.
- Missing agents.

Response:

```text
Bound this channel to agt_...
```

### `/puppet unbind`

Remove the current channel binding.

Response:

- If bound: `Unbound this channel from agt_...`
- If not bound: `This channel was not bound.`

### `/puppet status`

Require a channel binding.

Show:

- root id
- name
- status
- cwd
- live tmux yes/no
- child count
- last turn stopped timestamp
- completed timestamp if present

### `/puppet read`

Require a channel binding.

Read the bound root orchestrator only.

Arguments:

- `lines: int | None`, default config limit

Use existing `read_agent` service.

### `/puppet tree`

Require a channel binding.

Show the full agent tree for the bound root.

Use plain indented text:

```text
agt_root orchestrator running root /repo
  agt_child subagent completed tests /repo
```

## Formatting

Add small helpers in `discord_bot.py`:

- `chunk_text(text: str, chunk_size: int, max_chunks: int) -> list[str]`
- `code_block(text: str) -> str`
- `send_chunks(destination, text, config)`

Rules:

- Use plain text/code blocks.
- Chunk under `chunk_size`, default `1900`.
- Send at most `max_chunks`, default `3`.
- Append `[truncated]` to the final chunk when content exceeds the cap.

## Non-Goals

- No inbound prompt handling yet.
- No outbound human message dispatch yet.
- No typing indicator yet.
