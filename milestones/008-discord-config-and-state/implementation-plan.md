# Implementation Plan

## Scope

This milestone adds configuration and registry support only. It does not connect to Discord, expose MCP tools, or dispatch messages.

## Configuration

Add a `DiscordConfig` dataclass in `src/puppetmaster/config.py` and attach it to `Config`.

Fields:

- `token: str | None`
- `guild_id: int | None`
- `poll_interval_seconds: float = 1.0`
- `typing_timeout_seconds: float = 300.0`
- `chunk_size: int = 1900`
- `max_chunks: int = 3`

Read values from `.puppetmaster/config.toml`:

```toml
[discord]
token = ""
guild_id = ""
poll_interval_seconds = 1
typing_timeout_seconds = 300
chunk_size = 1900
max_chunks = 3
```

Parsing rules:

- Empty `token` becomes `None`.
- Empty `guild_id` becomes `None`.
- `guild_id` accepts either integer TOML values or numeric strings.
- Poll interval must be positive.
- Typing timeout must be positive.
- `chunk_size` must be positive and no greater than `1900`.
- `max_chunks` must be positive.

Update the default config created by `ensure_state` to include the `[discord]` section with empty token and guild id.

Do not introduce environment-variable lookup for Discord token in this milestone. Keeping secrets in local config is an explicit v1 decision.

## Registry Schema

Extend `SCHEMA` in `src/puppetmaster/registry.py` with two tables.

### `discord_channel_bindings`

Columns:

- `channel_id text primary key`
- `root_agent_id text not null`
- `guild_id text`
- `created_at text not null`
- `updated_at text not null`

Indexes:

- Unique index on `root_agent_id` so one root has one active channel binding.

Behavior:

- Binding a channel to a root replaces any existing binding for the same channel.
- Binding a root to a new channel removes or replaces the old root binding so the one-root-one-channel rule holds.
- Unbinding by channel deletes that channel row.
- Looking up by channel returns the root agent id or `None`.
- Looking up by root returns the channel id or `None`.

### `outbound_human_messages`

Columns:

- `id text primary key`
- `root_agent_id text not null`
- `agent_id text not null`
- `transport text not null`
- `channel_id text not null`
- `status text not null`
- `message text not null`
- `created_at text not null`
- `delivered_at text`
- `failed_at text`
- `error text`

Allowed values:

- `transport = "discord"` for v1.
- `status in ("pending", "delivered", "failed")`.

Indexes:

- `(transport, status, created_at)`
- `(root_agent_id, status, created_at)`

## Registry Methods

Add methods to `Registry`:

- `bind_discord_channel(channel_id: str, root_agent_id: str, guild_id: str | None = None) -> dict`
- `unbind_discord_channel(channel_id: str) -> bool`
- `discord_binding_for_channel(channel_id: str) -> dict | None`
- `discord_binding_for_root(root_agent_id: str) -> dict | None`
- `list_discord_bindings() -> list[dict]`
- `enqueue_outbound_human_message(root_agent_id: str, agent_id: str, transport: str, channel_id: str, message: str) -> dict`
- `pending_outbound_human_messages(transport: str, limit: int = 20) -> list[dict]`
- `mark_outbound_human_message_delivered(message_id: str) -> dict`
- `mark_outbound_human_message_failed(message_id: str, error: str) -> dict`

Implementation details:

- Use existing `new_id("msg")` style id generation.
- Use existing `now()` for timestamps.
- Return dictionaries shaped consistently with agent/event registry methods.
- Keep SQL parameterized.
- Do not add foreign key enforcement for this milestone unless the current registry already enforces it globally. The service layer will validate roots.

## Migration Behavior

The registry currently initializes schema with `create table if not exists`. Use the same style for these new tables.

If a future migration system is introduced, these tables should become a named migration, but this milestone should stay consistent with the current simple schema bootstrap.

## Non-Goals

- No Discord client.
- No slash command registration.
- No MCP `send_human_message` tool.
- No inbound prompt handling.
- No outbound dispatch loop.
