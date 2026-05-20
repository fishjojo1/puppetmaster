# Puppetmaster

Puppetmaster is a local supervisor for Codex agents. It starts the root orchestrator and child agents in managed tmux sessions, records durable SQLite metadata and terminal logs, exposes MCP tools for delegation, and uses Codex Stop hooks to deliver child-agent events back to the orchestrator.

The v1 design is specified in [spec.md](spec.md). Implementation milestones are under [milestones](milestones).

## Safety

Managed Codex sessions intentionally launch with:

```bash
--no-alt-screen --dangerously-bypass-approvals-and-sandbox
```

This is powerful and dangerous. Use Puppetmaster only in local workspaces where full filesystem and command access is acceptable.

If you bind Discord to Puppetmaster, the bound Discord channel becomes a remote control surface for local Codex sessions running with bypassed approvals and sandbox checks. Keep the bot token in local state, bind only trusted channels, and treat anyone who can post mention/reply prompts in that channel as able to control the local orchestrator.

## Prerequisites

- Python 3.11+
- tmux
- Codex CLI on `PATH`
- Codex authentication available in `~/.codex/auth.json`

## Setup

For development from this repository:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
puppet doctor --deep
```

Without installing, run commands with:

```bash
PYTHONPATH=src python3 -m puppetmaster.cli doctor
```

The executable name is `puppet`. A repository-local `./puppet` wrapper is also provided.

## Quickstart

Start a root orchestrator:

```bash
puppet orchestrator start \
  --cwd /home/kek/Projects/pupptermaster \
  --prompt "You are the root Puppetmaster orchestrator. Create child agents only when asked."
```

List and inspect agents:

```bash
puppet agent list
puppet agent tree
puppet orchestrator inspect
puppet agent inspect <agent-id>
puppet agent read <agent-id> --lines 120
puppet tui
```

Attach to a live session:

```bash
puppet agent attach <agent-id>
```

Print the attach command without attaching:

```bash
puppet agent attach <agent-id> --print
```

## Discord Bot

Configure Discord in local state:

```toml
# .puppetmaster/config.toml
[discord]
token = "your-discord-bot-token"
guild_id = "123456789012345678"
poll_interval_seconds = 1
typing_timeout_seconds = 300
chunk_size = 1900
max_chunks = 3
```

`.puppetmaster/config.toml` contains the bot token and must stay local. `.puppetmaster/` is ignored by git.

Start the bot after configuring the token and guild:

```bash
puppet discord serve
```

Start a root orchestrator, then bind a Discord text channel to it:

```bash
puppet orchestrator start \
  --cwd /home/kek/Projects/puppetmaster \
  --prompt "You are the root orchestrator. Reply to Discord prompts with send_human_message."
```

In Discord:

```text
/puppet agents
/puppet bind agent_id:<root-agent-id>
/puppet status
```

Slash commands:

```text
/puppet agents
/puppet bind agent_id:<root-agent-id>
/puppet unbind
/puppet status
/puppet read lines:<optional>
/puppet tree
```

After a channel is bound, the bot sends prompts to the root orchestrator only when a message mentions the bot or replies to a bot-authored message. Plain channel chatter is ignored. Attachments are ignored in v1.

When an orchestrator or child calls `send_human_message(message)`, Puppetmaster queues the reply for the bound root and the Discord bot posts it back to the bound channel. The MCP tool does not accept Discord channel ids; routing always follows the root binding. Outbound replies are chunked to fit Discord limits.

Bindings and outbound messages are durable in SQLite. On restart, existing channel bindings still work, pending outbound messages are delivered once, and delivered or failed rows are not resent. Typing indicators are best-effort in-memory state and are reset by a bot restart.

After an inbound prompt is delivered, the bot shows typing while the orchestrator is working. Typing stops when `send_human_message` is delivered, the root turn stops, or `typing_timeout_seconds` expires.

Open the terminal UI to navigate the agent tree, view stats, and preview the selected agent's live tmux pane or saved log:

```bash
puppet tui --refresh 1 --lines 120
puppet tui --root <root-agent-id>
```

Create a child from the human CLI:

```bash
puppet agent create \
  --parent <orchestrator-id> \
  --cwd /home/kek/Projects/pupptermaster \
  --description "Run a focused smoke check" \
  --prompt "Run the test suite and call complete_agent when done."
```

Prompt or complete an agent manually:

```bash
puppet agent prompt <agent-id> --prompt "Continue with the next check."
puppet agent complete <agent-id> --status blocked --summary "Needs a product decision."
```

## How Completion And Events Work

Agents are not considered done just because a Codex turn stops. Real completion is explicit through the MCP tool:

```text
complete_agent(status, summary, result?, files_changed?, next_steps?)
```

When a child completes, fails, or blocks, Puppetmaster records an event and queues delivery to the parent and root. The orchestrator's generated Stop hook runs:

```bash
puppet hook drain-events --agent-id <orchestrator-id>
```

If events are pending, the hook returns a bounded continuation prompt beginning with `PUPPETMASTER EVENT` or `PUPPETMASTER EVENTS`.

Managed agents can also schedule non-blocking wakeups:

```text
wait(seconds, reason?)
```

`wait` records a durable SQLite wakeup, returns immediately, and tells the agent to end its turn. When the timer expires, Puppetmaster queues an `agent.wait_over` event to that same agent and injects a prompt beginning with `PUPPETMASTER WAIT OVER` when the tmux session is available. Due wakeups are also reconciled by `puppet hook drain-events`, so expired timers still fire after process exits or missed helper processes.

State changes from completion, Stop hooks, stop, kill, pause, resume, and status reconciliation are queued to the parent and root where applicable. Noisy nonterminal turn-stop/status events are coalesced; terminal or high-signal events such as completed, failed, blocked, killed, and stopped are not.

## MCP Tools

Managed Codex sessions receive a per-agent MCP server named `puppetmaster`. V1 tools are:

```text
create_agent
prompt_agent
read_agent
inspect_agent
list_agents
complete_agent
stop_agent
kill_agent
pause_agent
resume_agent
attach_agent
wait
send_human_message
```

`create_agent` can start a child in goal mode with `goal: true`. `goal` is an optional boolean; when true, Puppetmaster prepends literal `/goal ` to the start of the child agent's initial `prompt`. It does nothing else. `create_agent` always requires an explicit absolute `cwd` and a `prompt`; v1 does not default to the caller's cwd and does not create worktrees.

`wait` accepts positive seconds up to `[limits].max_wait_seconds` in `.puppetmaster/config.toml` (default `3600`). It does not sleep inside the MCP call.

## State And Logs

By default Puppetmaster writes state in `.puppetmaster/`:

```text
.puppetmaster/config.toml
.puppetmaster/registry.sqlite
.puppetmaster/puppetmaster.log.jsonl
.puppetmaster/agents/<agent-id>/initial-prompt.md
.puppetmaster/agents/<agent-id>/terminal.log
.puppetmaster/agents/<agent-id>/events.jsonl
.puppetmaster/agents/<agent-id>/launch.sh
.puppetmaster/agents/<agent-id>/codex-config/
```

Each generated `codex-config/config.toml` starts from `~/.codex/config.toml` and overlays the per-agent hooks, project trust, and Puppetmaster MCP server settings. `CODEX_HOME` is still per-agent so Puppetmaster can avoid mutating your global Codex config while giving each managed session its own hook trust and runtime state.

Override the state directory with `PUPPETMASTER_STATE_DIR`.

## Recovery

Reconcile registry state with tmux:

```bash
puppet reconcile
puppet reconcile --dry-run --json
```

Debug local state:

```bash
puppet doctor --deep
puppet debug tmux
puppet debug registry --json
puppet events pending <agent-id>
puppet wakeup list --json
puppet wakeup fire-due --json
```

Human override with audit trail:

```bash
puppet agent mark-status <agent-id> --status blocked --reason "Human found it waiting on input."
```

Prune completed or stopped agents from the registry tree while preserving their logs and state directories:

```bash
puppet agent cleanup-completed --dry-run
puppet agent cleanup-completed
puppet agent cleanup-completed --root <root-agent-id> --kill-stale
```

## Troubleshooting

Codex not found:

```bash
puppet doctor --deep
command -v codex
```

tmux not found or session missing:

```bash
puppet doctor --deep
puppet debug tmux
puppet agent read <agent-id>
```

Hook did not run:

```bash
puppet agent inspect <agent-id>
puppet events list --agent <agent-id>
puppet doctor --deep
```

Orchestrator did not receive an event:

```bash
puppet events pending <orchestrator-id>
puppet hook drain-events --agent-id <orchestrator-id>
```

Registry and tmux disagree:

```bash
puppet reconcile --dry-run
puppet reconcile
```

## Validation

Run automated and local smoke validation:

```bash
scripts/release-validate.sh
```

Run unit tests only:

```bash
PYTHONPATH=src python3 -m pytest
```

Live Codex workflow validation requires working Codex credentials and may start real model sessions.

## Known Limitations

- Local only; no remote execution.
- Codex only; no runtime abstraction in v1.
- No filesystem isolation or automatic worktrees.
- Uses tmux and Codex hooks; both must work on the host.
- Idle detection is conservative and hook-based, not a perfect terminal parser.
- No browser dashboard or multi-user authentication.
