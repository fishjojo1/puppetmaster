# AGENTS.md

This file is for coding agents and contributors landing cold in this repository. Keep it current when a change alters the project shape, runtime model, operational commands, safety posture, or development workflow.

## Project Brief

Puppetmaster is a local supervisor for Codex agents. It starts root orchestrators and child agents in managed tmux sessions, records durable SQLite metadata and terminal logs, exposes MCP tools to managed agents, and can route human prompts/replies through one Discord bot.

The package name is `pupptermaster`, and the installed CLI command is `puppet`.

The repository is source-available under the PolyForm Noncommercial License. Do not describe it as OSI open source unless the license changes.

## Core Runtime Model

Puppetmaster has three separate concerns:

- Installed runtime: the Python package and `puppet` executable.
- State directory: config, registry, logs, generated per-agent Codex homes, Discord process metadata, and agent artifacts. The default is `~/.puppetmaster`; `PUPPETMASTER_STATE_DIR` overrides it for tests and experiments.
- Agent workspace: the project directory passed as `--cwd` when starting an orchestrator or child agent.

Managed Codex sessions intentionally run with broad local permissions and generated per-agent Codex config. Treat Discord-bound channels as a remote control surface for local command execution.

## Source Layout

- `src/puppetmaster/cli.py`: argparse CLI entrypoint for `puppet`.
- `src/puppetmaster/config.py`: default config, global state resolution, TOML loading/writing, and subprocess environment setup.
- `src/puppetmaster/registry.py`: SQLite schema and persistence for agents, events, wakeups, Discord bindings, and outbound human messages.
- `src/puppetmaster/services.py`: supervisor behavior for agent creation, prompts, lifecycle updates, wakeups, event delivery, Codex config generation, reconciliation, and doctor checks.
- `src/puppetmaster/tmux.py`: tmux session creation, pane capture, prompt injection, attach, stop, and kill helpers.
- `src/puppetmaster/mcp_server.py`: per-agent MCP server and tool authorization.
- `src/puppetmaster/discord_bot.py`: Discord slash commands, mention/reply routing, channel bindings, typing state, screenshots, and outbound message delivery.
- `src/puppetmaster/tui.py`: curses terminal UI for browsing agent trees and previewing output.
- `src/puppetmaster/terminal_image.py`: terminal text and ANSI rendering to PNG.
- `src/puppetmaster/native_screenshot.py`: optional native screenshot backends with terminal rendering fallback.
- `tests/`: pytest coverage for core state, CLI, MCP tools, Discord behavior, tmux helpers, screenshots, terminal image rendering, and TUI helpers.
- `milestones/`: implementation briefs and validation notes for completed design slices.
- `docs/spec-conformance.md`: checklist mapping v1 requirements to implementation artifacts.
- `scripts/release-validate.sh`: local release validation script.

## Public Interfaces

Important CLI groups:

- `puppet init`
- `puppet orchestrator start|inspect`
- `puppet agent create|list|tree|inspect|read|prompt|attach|stop|kill|complete|pause|resume|mark-status|cleanup-completed`
- `puppet events list|pending|ack`
- `puppet wakeup fire-due|fire|list|sleep-and-fire`
- `puppet hook stop|drain-events`
- `puppet mcp serve`
- `puppet discord serve|status|stop`
- `puppet tui`
- `puppet doctor`
- `puppet reconcile`
- `puppet debug create-raw|tmux|registry`

Managed agents receive an MCP server named `puppetmaster` with tools for creating and prompting child agents, reading and inspecting visible agents, completing work, scheduling wakeups, stopping/killing/pausing/resuming authorized agents, attaching to tmux sessions, and sending human messages through the bound root.

Generated agent prompts instruct agents to always use `send_human_message` for human-facing answers, status updates, readiness notices, blockers, and regular progress updates during longer work.

Discord slash commands are guild-scoped. Channel bindings are the routing layer: one channel binds to one root orchestrator, and one root orchestrator binds to one channel.

Discord `/puppet compact` and `/puppet clear` both send the literal Codex reset command to the bound root, then queue a regenerated Puppetmaster orchestrator prompt with a reset-specific task telling the root to notify the user that it is ready for new tasks.

## State And Events

The registry is SQLite and stores agents, events, event deliveries, scheduled wakeups, Discord channel bindings, and outbound human messages. Agent artifacts live under the active state directory and include initial prompts, terminal logs, event logs, launch scripts, and generated Codex config.

Completion is explicit. A Codex turn stopping is not the same as finishing work. Agents should call `complete_agent` with `success`, `failed`, `blocked`, or `cancelled` when their assigned task is actually terminal.

Stop hooks and event delivery are central to orchestration. Orchestrator hooks drain pending events, and high-signal state changes are queued to parent/root recipients. Low-signal turn-stop noise is coalesced.

`wait` is durable and non-blocking. It records a wakeup, returns immediately, and expects the agent to end its turn until Puppetmaster injects the wait-over prompt.

## Development Commands

Use `uv` when available:

```bash
uv sync --extra dev
uv run puppet doctor --deep
uv run pytest
```

Full local release validation:

```bash
scripts/release-validate.sh
```

The release script runs pytest, package build checks, doctor checks, non-network Discord config/schema checks, and a tmux-backed raw-agent smoke test. Live Codex and live Discord validation require local credentials and are intentionally separate.

## Safety And Operational Invariants

- Do not commit `.env`, `.puppetmaster/`, local state, logs, tokens, generated agent directories, virtualenvs, build artifacts, or caches.
- Keep `~/.puppetmaster` as the default state directory unless a deliberate design change says otherwise.
- Preserve `PUPPETMASTER_STATE_DIR` as the isolation override for tests, experiments, and alternate registries.
- Do not make spawned helper processes depend on the source checkout being the current working directory.
- Public CLI, Discord command, config, state schema, or operational-risk changes must update user-facing documentation and tests.
- Agent id validation is safety-sensitive. Custom root ids must remain path-friendly and reject traversal or duplicate registry/directory/tmux-session collisions.
- Discord routing must stay root-binding based. Managed agents should not be allowed to choose arbitrary Discord destinations.
- Avoid expanding remote control or filesystem access without explicit documentation, tests, and recovery guidance.

## Testing Expectations

For narrow changes, run the relevant pytest file or focused test. For changes touching shared supervisor behavior, state, Discord routing, Codex config generation, CLI surfaces, or packaging, run the full test suite. Before release-oriented changes, run `scripts/release-validate.sh`.

When a change modifies CLI behavior, Discord behavior, config defaults, registry schema, event semantics, generated Codex files, or safety-sensitive lifecycle handling, add or update tests in the same change.

## Keeping This File Living

Update this `AGENTS.md` in the same change when you:

- Add, remove, or rename a public command, MCP tool, Discord command, config key, state table, or major module.
- Change the global/local state model, generated Codex config model, tmux lifecycle, event delivery, wakeup behavior, or Discord routing.
- Add a new development, validation, release, or operational workflow agents should know.
- Change safety assumptions, permission boundaries, token handling, ignored local artifacts, or recovery guidance.
- Discover that any guidance here is stale while performing a task.

Do not update this file for purely internal refactors that leave the repo shape, interfaces, workflows, and invariants unchanged.
