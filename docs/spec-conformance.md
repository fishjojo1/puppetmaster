# Spec Conformance Checklist

This checklist maps the explicit v1 requirements from `spec.md` and the milestone validation plans to implementation artifacts.

## Core Supervisor

- Root orchestrator can be started and tracked: `puppet orchestrator start`, `start_orchestrator`.
- Any managed agent can create children through MCP: `create_agent` MCP tool.
- Every managed session runs in tmux: `Tmux.create_session`.
- Codex no-sandbox/no-approval mode is generated: `write_codex_files` launch flags.
- Configurable source Codex homes are supported for root trees: `puppet orchestrator start --codex-home`, repeated or comma-delimited `--codex-home` root pools, `[codex].home`, `[codex].home_pool`, `CODEX_HOME`, `PUPPETMASTER_CODEX_HOME`, and `PUPPETMASTER_CODEX_HOME_POOL`; runtime `CODEX_HOME` remains per-agent.
- Durable registry exists: `~/.puppetmaster/registry.sqlite`, `Registry`.
- Durable terminal logs exist: per-agent `terminal.log`, tmux `pipe-pane`.
- Agent inspection works: `puppet agent inspect`, `inspect_agent`.
- Human attach works: `puppet agent attach`, `attach_agent`.

## Codex Hooks And MCP

- Per-agent config is generated without mutating global config: `codex-config/config.toml`.
- Stop hook is generated per agent: `codex-config/hooks/stop-hook`.
- Subagent Stop hook records `agent.turn_stopped`: `handle_stop_hook`.
- Orchestrator Stop hook drains events: `puppet hook drain-events`.
- MCP server command exists: `puppet mcp serve`.
- Required MCP tools are registered: `src/puppetmaster/mcp_server.py`. `list_subagent_skills` exposes built-in `skills/subagent-*.md` templates for `create_agent(skill=...)`. `send_human_message` is root-orchestrator only and is removed from child-agent MCP tool listings. Orchestrator prompts and event prompts surface `kill_agent` as the cleanup action after final child output has been consumed.

## Completion And Events

- Completion is explicit: `complete_agent`.
- Completion statuses map to terminal agent status: success/completed, failed/failed, blocked/blocked, cancelled/stopped.
- Completion queues parent/root deliveries: `Registry.queue_delivery`.
- Event prompt starts with `PUPPETMASTER EVENT(S)`: `format_event_prompt`.
- Delivered events are marked delivered: `Registry.mark_delivered`.
- Low-signal turn stops coalesce: `queue_delivery(..., coalesce=True)`.
- Child completion can wake an idle root through conservative tmux prompt injection: `maybe_inject_event_prompt`.

## Human CLI

- Required final command groups exist: `orchestrator`, `agent`, `events`, `hook`, `mcp`, `doctor`, `reconcile`, `debug`.
- Global agent reset exists: `puppet agent reset` kills all Puppetmaster tmux sessions and clears registered agents, including root orchestrators, while preserving logs/artifacts and reusable skills.
- Event log cleanup exists: `puppet events clear` clears SQLite event and delivery records while preserving agents and terminal logs, and supports `--dry-run`.
- `puppet init` creates or updates global config, supports interactive hidden token entry, non-interactive Discord flags, JSON output, and optional background Discord startup.
- `puppet orchestrator start` can create multiple root orchestrators in one global registry; roots remain distinct by `id`, `root_id`, and `cwd`.
- `puppet orchestrator start --agent-id <id>` creates a root with an exact safe id; unsafe ids, duplicate registry ids, existing agent directories, and existing derived tmux sessions fail before root creation.
- `puppet orchestrator start --codex-home <path>` uses that Codex home as the root tree's source config/auth home and bypasses account pooling; repeated or comma-delimited `--codex-home` values define a root-scoped account pool, and `[codex].home_pool` provides the default pool for new roots.
- `puppet orchestrator start --goal` prepends literal `/goal ` to the root orchestrator's initial prompt.
- `agent create`, `agent list`, `agent tree`, `agent inspect`, `agent read`, `agent prompt`, `agent attach`, `agent stop`, `agent kill`, `agent kill-tree`, `agent complete` exist.
- Compatibility aliases exist for milestone commands: `agent create-raw`, `agent create-codex`, `debug create-raw`.
- JSON output exists for data-returning commands through `--json`.

## Discord Integration

- Discord config exists in global `~/.puppetmaster/config.toml`: `[discord]` in `Config`.
- Discord state is durable in the active state directory: SQLite stores `discord_channel_bindings`, `discord_skills`, and `outbound_human_messages`, including optional outbound attachment metadata; inbound human uploads are stored under `human_files/`.
- The bot entrypoint exists: `puppet discord serve`, `run_discord_bot`.
- Background Discord process management stores `discord-bot.pid` and `discord-bot.log` in the active state directory, rejects duplicate starts for the same state, clears stale PID files on start, logs start/stop/signal/crash events to `puppetmaster.log.jsonl`, and honors `PUPPETMASTER_STATE_DIR` isolation.
- Guild-scoped slash commands exist: `/puppet agents`, `/puppet bind`, `/puppet unbind`, `/puppet status`, `/puppet read`, `/puppet tree`, `/puppet screenshot`, `/puppet compact`, `/puppet clear`, and `/skills`.
- `/puppet bind` accepts root orchestrators, rejects killed or dead root records, and can rebind completed roots while their tmux session is still available: `handle_bind_command`.
- `/puppet screenshot` captures the bound root orchestrator's visible tmux pane and sends a rendered terminal-text PNG attachment.
- Channel binding is the global routing layer: one channel maps to one root, one root maps to one channel, and two channels can drive two different roots in one registry.
- Mention/reply-only inbound prompt routing exists: `DiscordRuntime.handle_message`.
- Inbound Discord prompts use `DISCORD MESSAGE RECEIVED:\n<message>` and include a `FILES ATTACHED` block with saved local paths when the human attached files.
- Reusable Discord skills persist prompt text, `/skills skill-name:<name> view:true` shows the saved prompt, and `/skills skill-name:<name>` sends the saved prompt to the channel's bound root.
- Outbound human replies use frontend-neutral `send_human_message` from root orchestrators and can include one local file attachment within Discord's default upload limit.
- Outbound Discord dispatch is durable and status-based: pending rows deliver, delivered/failed rows are not selected again.
- Typing indicators start after inbound prompt delivery and stop on outbound delivery, root turn stop, or timeout.
- Discord runtime errors are short and actionable, with setup hints for unbound channels and status/read hints for dead sessions.
- Discord logging avoids full message bodies and records startup, slash sync, bind/unbind, inbound delivery, outbound delivery/failure, and typing timeout events.
- Restart behavior is covered by persisted bindings and pending outbound message recovery; typing state is intentionally best-effort in memory.

## Recovery And Hardening

- Reconcile detects missing tmux for nonterminal agents and marks dead.
- Deep doctor checks tmux, Codex, state dir, registry schema, registry/tmux consistency, and generated hook executability.
- Structured supervisor log exists at `~/.puppetmaster/puppetmaster.log.jsonl`.
- Human `mark-status` records an audit event.
- Limits are configurable and enforced in create services.

## Validation Evidence

- Unit tests cover registry, cwd validation, multiple root trees, completion delivery, event drain, Stop hook coalescing, and reconciliation.
- Unit tests cover Discord process management, config/schema, command helpers, two-channel routing, inbound mention/reply routing, outbound queue dispatch, failure persistence, restart/idempotency behavior, and README documentation checks.
- `scripts/release-validate.sh` runs tests, doctor, non-network init/status/Discord config/schema checks, a two-root registry check, and a tmux-backed raw smoke test.
- Live Codex validation is intentionally separate because it consumes real Codex credentials/model sessions.
- Live Discord validation is intentionally separate because it needs a real Discord token, guild, and network connection.
