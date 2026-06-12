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

Managed Codex sessions intentionally run with broad local permissions and generated per-agent Codex config. Runtime `CODEX_HOME` remains per-agent; the source Codex home for config/auth defaults to `~/.codex`, can be set with `[codex].home`, rotated per spawned agent with `[codex].home_pool`, overridden with `CODEX_HOME` at root startup, `PUPPETMASTER_CODEX_HOME`, or `puppet orchestrator start --codex-home`, and is propagated to MCP-spawned descendants through `PUPPETMASTER_CODEX_HOME` plus `PUPPETMASTER_CODEX_HOME_POOL` when pooling is active. Root orchestrators store the active Codex home pool in registry metadata, so different roots can use different pools in the same state directory. Treat Discord-bound channels as a remote control surface for local command execution.

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
- `skills/`: reusable prompt templates for Puppetmaster orchestrators and built-in `subagent-*.md` MCP skill entries. The ABBA IoT bug bounty workflow uses `abba-iot-bugbounty-orchestrator.md` plus the `subagent-abba-*` skill family.
- `docs/spec-conformance.md`: checklist mapping v1 requirements to implementation artifacts.
- `scripts/release-validate.sh`: local release validation script.

## Public Interfaces

Important CLI groups:

- `puppet init`
- `puppet orchestrator start|inspect`
- `puppet agent create|list|tree|inspect|read|prompt|attach|stop|kill|kill-tree|reset|complete|pause|resume|mark-status|cleanup-completed|cleanup-dead`
- `puppet events list|pending|ack|clear`
- `puppet wakeup fire-due|fire|list|sleep-and-fire`
- `puppet hook stop|drain-events`
- `puppet mcp serve`
- `puppet discord serve|status|stop`
- `puppet tui`
- `puppet doctor`
- `puppet reconcile`
- `puppet debug create-raw|tmux|registry`

`puppet orchestrator start --goal` and MCP `create_agent(goal=true)` both prepend literal `/goal ` to the managed agent's initial task prompt. Codex goal mode lets the managed agent work continuously with auto-compaction until terminal, so it is best for substantial tasks with well-defined success criteria and not for vague exploration or small one-shot questions. A single `puppet orchestrator start --codex-home <path>` selects one source Codex home for the root tree's config/auth and bypasses `[codex].home_pool`; multiple `--codex-home` values, or comma-delimited values, define a root-scoped account pool. Generated per-agent `CODEX_HOME` directories are still used for runtime isolation.

Managed agents receive an MCP server named `puppetmaster` with tools for listing subagent skills, creating and prompting child agents, reading and inspecting visible agents, completing work, scheduling wakeups, stopping/killing/pausing/resuming authorized agents, and attaching to tmux sessions. Root orchestrators also receive `send_human_message` for replying through the bound root channel, with optional local file attachment support via `file_path` and `filename`; child agents do not receive that tool.

Generated root orchestrator prompts instruct roots to always use `send_human_message` for human-facing answers, status updates, readiness notices, blockers, and regular progress updates during longer work. They also instruct roots to call `list_subagent_skills()` before role-specific delegation when useful, pass `create_agent(skill="subagent-...")` for matching templates, and call `kill_agent(agent_id)` after consuming final child output when a child is complete or no longer useful, so child tmux sessions and Codex processes do not accumulate. Generated child prompts instruct children to report through completion/blocker status or their parent/root instead of contacting the human directly.

Generated managed-agent prompts place Puppetmaster runtime/tool instructions before the user task. The user task is appended at the bottom under a literal `USER INSTRUCTIONS` heading so task-specific instructions have the final prompt position.

Built-in subagent skill templates are discovered from `skills/subagent-*.md` and must include YAML frontmatter `description`. Root workflow prompts in `skills/`, such as `project-orchestrator.md`, `vuln-research-orchestrator.md`, and `abba-iot-bugbounty-orchestrator.md`, are not returned by `list_subagent_skills` because they are orchestrator prompts rather than child-agent roles.

Discord slash commands are guild-scoped. Channel bindings are the routing layer: one channel binds to one root orchestrator, and one root orchestrator binds to one channel.

Discord `/skills` manages reusable prompts. With no arguments it lists saved skills, `skill-name` autocompletes from saved skills, with `skill-name` plus `prompt` it creates or updates a skill, with `skill-name` plus `view:true` it shows the stored prompt without running it, with only `skill-name` it sends the saved prompt to the channel's bound root orchestrator, with `skill-name` plus `extra-prompt` it appends one-off instructions when running the saved skill without modifying the saved prompt, and with `forget:true` it deletes the skill.

The TUI opens on the agent tree. Press `s` to switch to the reusable skills view, where `n` creates a skill, `e` or Enter edits the selected skill through `$VISUAL`/`$EDITOR`, and `d` deletes the selected skill.

Discord inbound file attachments are saved under the active state directory at `human_files/<root-agent-id>/<discord-message-id>/`. The delivered root prompt includes a literal `FILES ATTACHED` section with absolute saved paths. Attachment-only Discord messages are valid prompts.

Discord `/puppet compact` and `/puppet clear` both send the literal Codex reset command to the bound root, then queue a regenerated Puppetmaster orchestrator prompt after a short delay with a reset-specific task telling the root to notify the user that it is ready for new tasks.

## State And Events

The registry is SQLite and stores agents, events, event deliveries, scheduled wakeups, Discord channel bindings, reusable Discord skills, and outbound human messages with optional attachment metadata. Agent artifacts live under the active state directory and include initial prompts, terminal logs, event logs, launch scripts, and generated Codex config. Inbound Discord human uploads also live under the active state directory in `human_files/`.

Discord bot starts, shutdown signals, clean stops, and unhandled crashes are recorded in the supervisor JSONL log at `puppetmaster.log.jsonl`; foreground/background stdout and stderr for background bot runs go to `discord-bot.log`.

Completion is explicit. A Codex turn stopping is not the same as finishing work. Agents should call `complete_agent` with `success`, `failed`, `blocked`, or `cancelled` when their assigned task is actually terminal.

`puppet agent reset` is the global agent reset/nuke command. It kills every live tmux session using the Puppetmaster tmux prefix, clears all agent rows and agent-related events, deliveries, wakeups, Discord channel bindings, and outbound human messages, and preserves logs/artifacts plus reusable Discord skills. Use `--dry-run` to preview.

Stop hooks and event delivery are central to orchestration. Orchestrator hooks drain pending events, and high-signal state changes are queued to parent/root recipients. Low-signal turn-stop noise is coalesced.

`puppet events clear` clears the SQLite event log and event delivery records while preserving agents, scheduled wakeups, Discord bindings, outbound human messages, reusable skills, and terminal log files. Use `--dry-run` to preview row counts.

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

## Commit Workflow

Commit after every completed change unless the human explicitly asks not to commit yet. Before committing, inspect the staged diff for secrets, tokens, local state, logs, credentials, generated agent directories, virtualenvs, build artifacts, and caches; do not commit sensitive or local-only material.

## Keeping This File Living

Update this `AGENTS.md` in the same change when you:

- Add, remove, or rename a public command, MCP tool, Discord command, config key, state table, or major module.
- Change the global/local state model, generated Codex config model, tmux lifecycle, event delivery, wakeup behavior, or Discord routing.
- Add a new development, validation, release, or operational workflow agents should know.
- Change safety assumptions, permission boundaries, token handling, ignored local artifacts, or recovery guidance.
- Discover that any guidance here is stale while performing a task.

Do not update this file for purely internal refactors that leave the repo shape, interfaces, workflows, and invariants unchanged.
