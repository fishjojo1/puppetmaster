# Puppetmaster

Puppetmaster is a local supervisor for Codex agents. It starts the root orchestrator and child agents in managed tmux sessions, records durable SQLite metadata and terminal logs, exposes MCP tools for delegation, and uses Codex Stop hooks to deliver child-agent events back to the orchestrator.

The v1 design is specified in [SPEC.md](SPEC.md). Implementation milestones are under [milestones](milestones).

## Safety

Managed Codex sessions intentionally launch with:

```bash
--no-alt-screen --dangerously-bypass-approvals-and-sandbox
```

This is powerful and dangerous. Use Puppetmaster only in local workspaces where full filesystem and command access is acceptable.

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
```

`create_agent` can start a child in goal mode with `goal: true`. `goal` is an optional boolean; when true, Puppetmaster prepends literal `/goal ` to the start of the child agent's initial `prompt`. It does nothing else. `create_agent` always requires an explicit absolute `cwd` and a `prompt`; v1 does not default to the caller's cwd and does not create worktrees.

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
