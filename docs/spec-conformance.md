# Spec Conformance Checklist

This checklist maps the explicit v1 requirements from `SPEC.md` and the milestone validation plans to implementation artifacts.

## Core Supervisor

- Root orchestrator can be started and tracked: `puppet orchestrator start`, `start_orchestrator`.
- Any managed agent can create children through MCP: `create_agent` MCP tool.
- Every managed session runs in tmux: `Tmux.create_session`.
- Codex no-sandbox/no-approval mode is generated: `write_codex_files` launch flags.
- Durable registry exists: `.puppetmaster/registry.sqlite`, `Registry`.
- Durable terminal logs exist: per-agent `terminal.log`, tmux `pipe-pane`.
- Agent inspection works: `puppet agent inspect`, `inspect_agent`.
- Human attach works: `puppet agent attach`, `attach_agent`.

## Codex Hooks And MCP

- Per-agent config is generated without mutating global config: `codex-config/config.toml`.
- Stop hook is generated per agent: `codex-config/hooks/stop-hook`.
- Subagent Stop hook records `agent.turn_stopped`: `handle_stop_hook`.
- Orchestrator Stop hook drains events: `puppet hook drain-events`.
- MCP server command exists: `puppet mcp serve`.
- Required MCP tools are registered: `src/puppetmaster/mcp_server.py`.

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
- `agent create`, `agent list`, `agent tree`, `agent inspect`, `agent read`, `agent prompt`, `agent attach`, `agent stop`, `agent kill`, `agent complete` exist.
- Compatibility aliases exist for milestone commands: `agent create-raw`, `agent create-codex`, `debug create-raw`.
- JSON output exists for data-returning commands through `--json`.

## Recovery And Hardening

- Reconcile detects missing tmux for nonterminal agents and marks dead.
- Deep doctor checks tmux, Codex, state dir, registry schema, registry/tmux consistency, and generated hook executability.
- Structured supervisor log exists at `.puppetmaster/puppetmaster.log.jsonl`.
- Human `mark-status` records an audit event.
- Limits are configurable and enforced in create services.

## Validation Evidence

- Unit tests cover registry, cwd validation, completion delivery, event drain, Stop hook coalescing, and reconciliation.
- `scripts/release-validate.sh` runs tests, doctor, and a tmux-backed raw smoke test.
- Live Codex validation is intentionally separate because it consumes real Codex credentials/model sessions.
