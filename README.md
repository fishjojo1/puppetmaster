# Puppetmaster

Puppetmaster is a local supervisor for Codex agents. It starts the root orchestrator and child agents in managed tmux sessions, records durable SQLite metadata and terminal logs, exposes MCP tools for delegation, and uses Codex Stop hooks to deliver child-agent events back to the orchestrator.

## License

Puppetmaster is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE.md). Noncommercial use, modification, and distribution are permitted under that license.

Commercial use requires a separate commercial license from the repository owner. See [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).

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
- Codex authentication available in `~/.codex/auth.json` or in the configured source `CODEX_HOME`

## Setup

Install Puppetmaster once as a global local tool:

```bash
uv tool install /path/to/pupptermaster
```

If you prefer pipx:

```bash
pipx install /path/to/pupptermaster
```

Then initialize the shared state directory:

```bash
puppet init
```

For development from this repository:

```bash
uv sync --extra dev
uv run puppet doctor --deep
uv run pytest
```

If you prefer a standard virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
puppet doctor --deep
```

The executable name is `puppet`. A repository-local `./puppet` wrapper is also provided.

## Quickstart

Start root orchestrators from any directory. By default they share the global registry in `~/.puppetmaster`:

```bash
puppet orchestrator start \
  --cwd /home/kek/Projects/project-a \
  --prompt "Manage project A."

puppet orchestrator start \
  --cwd /home/kek/Projects/project-b \
  --prompt "Manage project B."
```

You can choose a predictable id for a root orchestrator:

```bash
puppet orchestrator start \
  --agent-id project-a \
  --cwd /home/kek/Projects/project-a \
  --prompt "Manage project A."
```

Custom root ids must match `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}` and must not contain `..`. Duplicate ids, existing agent directories, and existing derived tmux sessions are rejected before Puppetmaster creates the root. When `--agent-id` is omitted, Puppetmaster keeps generating `agt_...` ids.

To start a root tree from a non-default Codex home, pass `--codex-home` or set `CODEX_HOME` for the `puppet orchestrator start` command:

```bash
puppet orchestrator start \
  --cwd /home/kek/Projects/project-a \
  --codex-home /path/to/codex-home \
  --prompt "Manage project A."
```

Puppetmaster still gives every managed agent its own generated `CODEX_HOME`; the configured source home is used for Codex config/auth and is inherited by child agents spawned through the root's MCP tools.

To rotate agents across multiple Codex accounts, configure a pool in `~/.puppetmaster/config.toml`:

```toml
[codex]
home_pool = ["/path/to/codex-a", "/path/to/codex-b"]
```

When `home_pool` is set, Puppetmaster chooses the next source Codex home for each newly spawned agent, including roots and MCP-spawned children, and wraps back to the first entry after the end of the list. Explicit root startup overrides such as `puppet orchestrator start --codex-home` or shell `CODEX_HOME=...` use that one source home for the root tree instead of the pool.

When you run from the project directory, `--cwd` is optional and defaults to the current directory:

```bash
cd /home/kek/Projects/project-a
puppet orchestrator start --prompt "Manage this project."
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

Kill all live tmux sessions for one root tree while leaving other roots alone:

```bash
puppet agent kill-tree <root-agent-id>
puppet agent kill-tree <root-agent-id> --dry-run
```

Reset all registered agents, including root orchestrators, and kill all live Puppetmaster tmux sessions:

```bash
puppet agent reset --dry-run
puppet agent reset
```

`agent reset` clears agent registry state, agent events, event deliveries, wakeups, Discord channel bindings, and pending outbound human messages. It preserves logs, generated artifacts, configuration, and reusable skills.

## Discord Bot

Create or update global state:

```bash
puppet init
```

For scripted setup, pass values without prompting:

```bash
puppet init \
  --discord-token "$DISCORD_BOT_TOKEN" \
  --discord-guild-id 123456789012345678 \
  --no-input
```

`puppet init --start-discord` writes config first, then starts the same background bot used by `puppet discord serve --background`. Command output reports whether a token is configured, but never prints the token.

Discord config is stored in global state:

```toml
# ~/.puppetmaster/config.toml
[discord]
token = "your-discord-bot-token"
guild_id = "123456789012345678"
poll_interval_seconds = 1
typing_timeout_seconds = 300
chunk_size = 1900
max_chunks = 3
```

`~/.puppetmaster/config.toml` contains the bot token and must stay local.

One background bot serves the active state directory. Start it once after configuring the token and guild:

```bash
puppet discord serve --background
puppet discord status
puppet discord stop
```

For foreground debugging, use `puppet discord serve`.

Start one or more root orchestrators, then bind each Discord text channel to the root it should control:

```bash
puppet orchestrator start \
  --agent-id project-a \
  --cwd /home/kek/Projects/project-a \
  --prompt "You are the project A orchestrator. Reply to Discord prompts with send_human_message."

puppet orchestrator start \
  --agent-id project-b \
  --cwd /home/kek/Projects/project-b \
  --prompt "You are the project B orchestrator. Reply to Discord prompts with send_human_message."
```

In Discord:

```text
/puppet agents
/puppet bind agent_id:project-a
/puppet status
/puppet screenshot
```

For multi-project use, bind channel A to the project A root and channel B to the project B root. Custom root ids make these bindings stable and easy to type. A channel can have one active root binding, and a root can have one active channel binding. Rebinding a channel changes that channel only; rebinding a root moves that root from its previous channel.

Slash commands:

```text
/puppet agents
/puppet bind agent_id:<root-agent-id>
/puppet unbind
/puppet status
/puppet read lines:<optional>
/puppet tree
/puppet screenshot mode:<optional>
/puppet compact
/puppet clear
/skills skill-name:<optional> prompt:<optional> view:<optional> forget:<optional>
```

After a channel is bound, the bot sends prompts to the root orchestrator only when a message mentions the bot or replies to a bot-authored message. Plain channel chatter is ignored. Inbound user attachments are downloaded into `~/.puppetmaster/human_files/<root-agent-id>/<discord-message-id>/` and the delivered prompt includes a `FILES ATTACHED` block listing the saved local paths. Attachment-only messages are accepted.

`/puppet agents` formats each root id in its own copy-friendly code block so Discord users can copy one id at a time.

`/skills` manages reusable Discord prompts. With no arguments it lists saved skills. The `skill-name` option autocompletes from saved skills. With `skill-name` and `prompt`, it creates or updates a skill. With `skill-name` and `view:true`, it shows the stored prompt without running it. With only `skill-name`, it sends that saved prompt to the channel's bound root orchestrator. With `skill-name` and `forget:true`, it deletes the skill.

`/puppet screenshot` captures the bound root orchestrator's current tmux pane, renders the visible terminal text as a PNG, and posts it as an attachment. The renderer preserves common ANSI colors and handles wide Unicode, emoji, combining marks, variation selectors, and Nerd Font symbols. This default mode is headless and avoids capturing unrelated desktop content.

Screenshot modes:

```text
terminal        Render the tmux pane as a PNG. This is the default.
native-window   Try to capture the focused native desktop window, then fall back to terminal rendering.
native-screen   Try to capture the focused native desktop screen, then fall back to terminal rendering.
```

Native screenshot backends are best-effort and environment-dependent. Puppetmaster currently tries niri, gnome-screenshot, scrot, and ImageMagick import on X11 where available.

`/puppet compact` sends Codex `/compact` to the bound root and then, after a short delay, queues the generated orchestrator prompt with a compacted-context task. `/puppet clear` sends Codex `/clear` to the bound root and then, after a short delay, queues the generated orchestrator prompt with a cleared-context task. These recovery prompts tell the root to report readiness through `send_human_message`.

When a root orchestrator calls `send_human_message(message)`, Puppetmaster queues the reply for the bound root and the Discord bot posts it back to the bound channel. It can also include `file_path` and optional `filename` to upload one local file attachment; relative paths resolve from the agent workspace, filenames are display names only, and files above Discord's default 10 MiB attachment limit are rejected before queueing. The MCP tool does not accept Discord channel ids; routing always follows the root binding. Outbound reply text is chunked to fit Discord limits.

Bindings, reusable skills, inbound human files, and outbound messages are durable in the active state directory. On restart, existing channel bindings still work, saved skills remain available, pending outbound messages and attachments are delivered once, and delivered or failed rows are not resent. Typing indicators are best-effort in-memory state and are reset by a bot restart.

After an inbound prompt is delivered, the bot shows typing while the orchestrator is working. Typing stops when `send_human_message` is delivered, the root turn stops, or `typing_timeout_seconds` expires.

Open the terminal UI to navigate the agent tree, view stats, and preview the selected agent's live tmux pane or saved log:

```bash
puppet tui --refresh 1 --lines 120
puppet tui --root <root-agent-id>
```

Press `s` in the TUI to switch to the skills view. The skills view lists saved reusable Discord prompts, previews the selected prompt, and supports `n` to create, `e` or Enter to edit with `$VISUAL`/`$EDITOR`, and `d` to delete.

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
list_subagent_skills
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
send_human_message  # root orchestrators only
```

`list_subagent_skills` returns built-in subagent skill templates discovered from `skills/subagent-*.md`, using each file's YAML frontmatter `description`. `create_agent` accepts an optional `skill` name from that list; when provided, Puppetmaster prepends the skill instructions to the child agent's initial prompt and records the selected skill in agent metadata.

`create_agent` can start a child in goal mode with `goal: true`. `goal` is an optional boolean; when true, Puppetmaster prepends literal `/goal ` to the start of the child agent's initial `prompt`. Codex goal mode lets the child work continuously with auto-compaction until the goal reaches a terminal state, which is useful for substantial delegated goals with well-defined success criteria such as implementation, validation, audit, or research work. Avoid goal mode for vague exploration, small one-shot questions, or tasks that need frequent human steering. `create_agent` always requires an explicit absolute `cwd` and a `prompt`; v1 does not default to the caller's cwd and does not create worktrees.

`puppet orchestrator start --goal` applies the same literal `/goal ` prefix to the root orchestrator's initial prompt.

Generated root orchestrator prompts frame the root as a coordinator. Roots can handle small, low-risk work directly, but larger, multi-step, research-heavy, test-heavy, or parallelizable tasks should be delegated to child agents with `create_agent`.

When a child is complete or no longer useful, roots are instructed to inspect/read any final output they need and then call `kill_agent(agent_id)`. That force-kills the child's tmux session and Codex process so completed work does not leave hundreds of idle Codex binaries behind.

Child agents do not receive `send_human_message` in their MCP tool surface. They should report results with `complete_agent`, mark blockers with `complete_agent status:blocked`, or ask the parent/root to contact the human.

For root orchestrators, `send_human_message` accepts `message`, optional `file_path`, and optional attachment display `filename`. File attachments are preflighted for existence, regular-file type, safe display filename, and Discord's default 10 MiB upload limit before they are queued.

`wait` accepts positive seconds up to `[limits].max_wait_seconds` in `~/.puppetmaster/config.toml` (default `3600`). It does not sleep inside the MCP call.

## State And Logs

By default Puppetmaster writes state in `~/.puppetmaster/`:

```text
~/.puppetmaster/config.toml
~/.puppetmaster/registry.sqlite
~/.puppetmaster/puppetmaster.log.jsonl
~/.puppetmaster/discord-bot.pid
~/.puppetmaster/discord-bot.log
~/.puppetmaster/human_files/<root-agent-id>/<discord-message-id>/<attachment>
~/.puppetmaster/agents/<agent-id>/initial-prompt.md
~/.puppetmaster/agents/<agent-id>/terminal.log
~/.puppetmaster/agents/<agent-id>/events.jsonl
~/.puppetmaster/agents/<agent-id>/launch.sh
~/.puppetmaster/agents/<agent-id>/codex-config/
```

Each generated `codex-config/config.toml` starts from the selected source Codex home and overlays the per-agent hooks, project trust, and Puppetmaster MCP server settings. The source defaults to `~/.codex`, can be set globally with `[codex].home` in `~/.puppetmaster/config.toml`, can be rotated with `[codex].home_pool`, can be overridden for a root tree with `puppet orchestrator start --codex-home`, and honors `CODEX_HOME` when starting a root from the shell. Runtime `CODEX_HOME` is still per-agent so Puppetmaster can avoid mutating your global Codex config while giving each managed session its own hook trust and runtime state.

Override the state directory with `PUPPETMASTER_STATE_DIR` when you want an isolated registry, config, logs, and Discord PID file:

```bash
PUPPETMASTER_STATE_DIR="$(mktemp -d)" puppet init --no-input
PUPPETMASTER_STATE_DIR=/tmp/puppetmaster-test puppet discord status
```

This is useful for tests and experiments because a bot started with one state directory does not share registry rows, channel bindings, PID files, or logs with another state directory.

Existing project-local `.puppetmaster/` directories are not migrated automatically. New default commands use `~/.puppetmaster`, while old local state remains untouched. To inspect an old project-local state directory temporarily:

```bash
PUPPETMASTER_STATE_DIR=/path/to/project/.puppetmaster puppet agent list
```

Manual migration is intentionally explicit:

1. Install Puppetmaster globally.
2. Run `puppet init`.
3. Copy any needed Discord config values from the old project-local config.
4. Restart the Discord bot from the global state.
5. Recreate or rebind active orchestrators as needed.

Automatic migration is not provided because merging registries, tmux sessions, process state, and Discord bindings can create ambiguous ownership.

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

Prune completed, stopped, killed, or dead agents from the registry tree while preserving their logs and state directories:

```bash
puppet agent cleanup-completed --dry-run
puppet agent cleanup-completed
puppet agent cleanup-completed --root <root-agent-id> --kill-stale
puppet agent cleanup-dead --dry-run
puppet agent cleanup-dead --include-roots
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
uv run --extra dev pytest
```

Live Codex workflow validation requires working Codex credentials and may start real model sessions.

Build package artifacts locally:

```bash
uv run --extra dev python -m build
uv run --extra dev twine check dist/*
```

## Known Limitations

- Local only; no remote execution.
- Codex only; no runtime abstraction in v1.
- No filesystem isolation or automatic worktrees.
- Uses tmux and Codex hooks; both must work on the host.
- Native screenshots depend on local compositor or screenshot tooling and may fall back to terminal rendering.
- Idle detection is conservative and hook-based, not a perfect terminal parser.
- No browser dashboard or multi-user authentication.
