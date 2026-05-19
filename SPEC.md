# Puppetmaster Specification

## 1. Purpose

Puppetmaster is a local orchestration system for Codex agents. It lets a Codex-based orchestrator create, inspect, prompt, pause, stop, and kill other Codex sessions that run as persistent tmux sessions. Those managed Codex sessions are called agents. Agents may create child agents, and humans may attach to any managed session at any time.

The system is designed around one practical goal: an orchestrator should be able to delegate work to multiple persistent Codex subagents without losing control, context, or observability. If the orchestrator forgets why an agent exists, it can inspect the agent. If a human wants to intervene, they can attach to the tmux session. If a subagent finishes while the orchestrator is doing something else, Puppetmaster records the event and wakes the orchestrator when it is safe to do so.

This document is the implementation specification for v1. It is written for an engineer who has not seen the planning conversation and needs enough detail to build the system.

## 2. Vocabulary

**Agent** means one managed Codex session owned by Puppetmaster.

**Orchestrator** means the root Codex agent that coordinates work. Puppetmaster controls this session too.

**Subagent** means any non-root agent created by another agent.

**Parent agent** means the agent that requested creation of a child agent.

**Root agent** means the orchestrator.

**tmux session** means the terminal session used to host a live Codex process.

**Codex session id** means the session or thread identifier known to Codex. Puppetmaster should record it when it can discover it, but tmux session identity is the source of truth for process control.

**Puppetmaster registry** means durable local storage for agent metadata, relationships, status, prompts, events, and log paths.

**Event** means a durable notification about an agent lifecycle change, such as completion, blocked status, turn stopped, process exit, or human intervention.

**Completion** means an explicit claim from an agent that its assigned work is finished, failed, or blocked. Completion is not the same as a Codex turn stopping.

## 3. Goals

Puppetmaster v1 must:

1. Start and manage a root orchestrator Codex session.
2. Let any managed Codex agent create child Codex agents with caller-provided absolute working directories.
3. Run every managed Codex agent inside tmux so humans can attach, observe, and intervene.
4. Launch Codex in no-sandbox/no-approval mode for managed sessions.
5. Provide an MCP tool surface for agents to manage other agents.
6. Provide a CLI surface for humans and hooks to manage agents.
7. Record durable metadata and logs so agent state survives orchestrator context loss and tmux session death.
8. Use Codex hooks, especially `Stop`, to observe agent turn stops and deliver queued events to the orchestrator.
9. Support explicit completion through a `complete_agent` tool.
10. Wake the orchestrator when child agents finish, fail, or need instruction, without requiring the orchestrator to block on a wait tool.

## 4. Non-Goals For V1

Puppetmaster v1 does not need to:

1. Create git worktrees automatically.
2. Provide filesystem isolation between agents.
3. Provide remote execution across machines.
4. Provide a browser dashboard.
5. Guarantee perfect Codex idle detection through terminal scraping.
6. Interpret arbitrary natural-language terminal output as task completion.
7. Support non-Codex agent runtimes.
8. Provide multi-user authentication.
9. Provide a production distributed queue.
10. Replace Codex's own session storage or transcript model.

The design should leave room for these later, but v1 should stay local, direct, and operationally simple.

## 5. High-Level Architecture

Puppetmaster is a local supervisor with four major surfaces:

1. A tmux supervisor that creates and controls live terminal sessions.
2. A registry that stores durable agent metadata, events, and log locations.
3. An MCP server that exposes agent-management tools to Codex.
4. A CLI used by humans, Codex hooks, and internal launch scripts.

The runtime shape is:

```text
human terminal
  -> puppet CLI
    -> registry + tmux supervisor

root Codex orchestrator
  -> Puppetmaster MCP tools
    -> registry + tmux supervisor
      -> child tmux sessions
        -> child Codex processes

Codex hooks
  -> puppet CLI hook commands
    -> registry event queue
      -> orchestrator wakeup
```

Puppetmaster owns the root orchestrator session and every subagent session. The root is not a special untracked process. It has an agent id, metadata, hooks, MCP config, logs, and events just like child agents.

## 6. Core Decisions

### 6.1 Codex Is The First Runtime

The first supported agent runtime is Codex CLI. Runtime abstraction can be introduced later, but v1 should optimize for Codex's actual behavior: tmux sessions, Codex config files, Codex MCP configuration, Codex hooks, and Codex CLI flags.

### 6.2 Puppetmaster Wraps The Orchestrator

The orchestrator must be launched through Puppetmaster. This is necessary because Puppetmaster needs to:

1. Know the orchestrator's tmux session.
2. Install hooks and MCP configuration consistently.
3. Queue and deliver child-agent events into the orchestrator.
4. Let a human attach to the orchestrator the same way they attach to child agents.
5. Recover from orchestrator context loss using the same inspection tools.

### 6.3 Subagent Cwd Is Caller-Provided

Every `create_agent` call requires an absolute `cwd`. Puppetmaster does not choose a worktree or isolation directory in v1.

Validation rules:

1. `cwd` must be present.
2. `cwd` must be an absolute path.
3. `cwd` must exist.
4. `cwd` must be a directory.
5. Puppetmaster records the exact `cwd` in metadata.
6. Codex is launched with both the tmux working directory and Codex `--cd` set to the same `cwd`.

Future versions may add cwd allowlists, automatic worktrees, or sandboxed directories. V1 should not.

### 6.4 Codex Runs Without Sandbox Or Approvals

Managed Codex sessions should launch with:

```bash
codex --no-alt-screen --dangerously-bypass-approvals-and-sandbox --cd <cwd> <prompt>
```

If a local Codex build supports `--yolo`, Puppetmaster may accept a config alias named `yolo`, but the canonical v1 flag is `--dangerously-bypass-approvals-and-sandbox` because it is visible in the checked Codex CLI surface.

The lack of sandboxing is intentional for the requested local workflow. The risk must be explicit in documentation and launch output.

### 6.5 Completion Is Explicit

An agent is not done merely because a Codex turn stops. A turn stop means Codex reached a response boundary. The agent may still need more instructions.

Task completion is recorded through:

```text
complete_agent(status, summary, result, files_changed, next_steps)
```

The `Stop` hook is still important, but it is a fallback signal for "this agent's turn stopped" or "this agent may be awaiting input," not proof that the assigned task is complete.

### 6.6 Event Delivery Is Queue-Based

Puppetmaster does not rely on MCP push notifications. Instead:

1. Subagents report events to the registry.
2. Events are queued for their parent agent and usually for the root orchestrator.
3. The orchestrator receives events through its own Codex `Stop` hook continuation.
4. Puppetmaster may additionally inject a prompt into the orchestrator tmux session when it can safely determine the orchestrator is idle.

The hook continuation path is the correctness path. Tmux prompt injection is opportunistic.

## 7. Agent Data Model

The registry should store enough information for inspection, recovery, and lifecycle control.

Recommended agent fields:

```text
id: stable Puppetmaster id
parent_id: nullable agent id
root_id: root orchestrator id
role: orchestrator | subagent
name: short optional label
description: human-readable purpose
initial_prompt_path: path to stored initial prompt
cwd: absolute working directory
tmux_session: tmux session name
tmux_window: optional tmux window identifier
codex_session_id: optional Codex session/thread id
status: starting | running | idle | awaiting_input | completed | failed | blocked | stopped | killed | dead | unknown
completion_status: null | success | failed | blocked | cancelled
depth: integer
created_at: timestamp
updated_at: timestamp
started_at: timestamp nullable
last_turn_stopped_at: timestamp nullable
completed_at: timestamp nullable
stopped_at: timestamp nullable
exit_code: integer nullable
termination_reason: nullable string
log_path: path to durable terminal log
events_path: optional per-agent JSONL event path
metadata_json: extension field
```

Status meanings:

**starting** means Puppetmaster created metadata and is launching tmux/Codex.

**running** means the tmux session exists and the Codex process appears active.

**idle** means the last known Codex turn stopped without explicit completion.

**awaiting_input** means the agent likely needs more instructions. This may come from a hook, explicit agent completion status, or human mark.

**completed** means the agent explicitly called `complete_agent` with success.

**failed** means the agent explicitly called `complete_agent` with failed, or the process died unexpectedly during startup or execution.

**blocked** means the agent explicitly reported it cannot continue without help.

**stopped** means Puppetmaster gracefully stopped the session.

**killed** means Puppetmaster force-killed the tmux session or process.

**dead** means the tmux session/process is gone unexpectedly.

**unknown** means registry and tmux state disagree and Puppetmaster cannot classify the agent.

## 8. Event Data Model

Events are durable records. They are the backbone of recovery and orchestrator wakeup.

Recommended event fields:

```text
id: stable event id
agent_id: agent that produced the event
parent_id: parent agent at event time
root_id: root orchestrator id
type: event type
severity: info | warning | error
status: pending | delivered | acknowledged | superseded
created_at: timestamp
delivered_at: nullable timestamp
acknowledged_at: nullable timestamp
summary: short human-readable summary
payload_json: structured event payload
source: mcp_tool | codex_hook | process_watcher | human_cli | supervisor
```

Important event types:

```text
agent.created
agent.started
agent.turn_stopped
agent.completed
agent.failed
agent.blocked
agent.awaiting_input
agent.prompted
agent.stopped
agent.killed
agent.process_exited
agent.log_rotated
orchestrator.event_delivered
supervisor.error
hook.error
```

Events should be queryable by agent, parent, root, status, type, and creation time.

## 9. Storage Layout

V1 may use SQLite, JSON files, or a hybrid. SQLite is recommended because parent-child queries, event delivery state, and status updates are easier to make atomic.

Recommended project-local state directory:

```text
.puppetmaster/
  registry.sqlite
  agents/
    <agent-id>/
      meta.json
      initial-prompt.md
      terminal.log
      events.jsonl
      codex-config/
        config.toml
        hooks.json
        hooks/
          stop-hook
          user-prompt-submit-hook
```

The registry is the source of truth. Per-agent files are used for human readability, hook scripts, logs, and recovery if the database is damaged.

Terminal logs must remain readable after tmux exits. Puppetmaster should use `tmux pipe-pane` or equivalent capture to append terminal output to the per-agent log file.

Prompts should be stored as files, not only in SQLite, because they may be long and are useful during manual inspection.

## 10. Codex Launch Model

### 10.1 Environment

Each managed Codex process should receive:

```text
PUPPETMASTER_AGENT_ID=<agent-id>
PUPPETMASTER_PARENT_AGENT_ID=<parent-id-or-empty>
PUPPETMASTER_ROOT_AGENT_ID=<root-agent-id>
PUPPETMASTER_STATE_DIR=<absolute-state-dir>
PUPPETMASTER_CONFIG_DIR=<absolute-generated-codex-config-dir>
PUPPETMASTER_ROLE=orchestrator|subagent
```

These environment variables let hooks and MCP server calls identify the calling agent without relying on the model to pass identity honestly.

### 10.2 Command Shape

The supervisor should start Codex inside tmux with the tmux working directory set to the agent `cwd`:

```bash
tmux new-session -d -s <tmux-session> -c <cwd> -- <launch-command>
```

The launch command should run Codex with:

```bash
codex \
  --no-alt-screen \
  --dangerously-bypass-approvals-and-sandbox \
  --cd <cwd> \
  --config <generated config overrides as needed> \
  <initial prompt>
```

Long prompts should not be passed through shell interpolation. The supervisor should avoid brittle quoting. Acceptable approaches:

1. Use a temporary prompt file and feed it through stdin if Codex supports it for the selected mode.
2. Use a wrapper script generated per agent that reads the prompt file and executes Codex safely.
3. Use an argument array in a runtime process API rather than shell string concatenation when launching the tmux command.

The implementation must treat prompt content as data, not shell syntax.

### 10.3 Generated Codex Config

Each managed session needs a Codex config that includes:

1. Puppetmaster MCP server configuration.
2. Hook configuration.
3. Any desired model/profile settings inherited from Puppetmaster defaults.
4. Hook feature enabled.

The generated config should be per-agent or per-root to avoid mutating the user's global Codex config.

### 10.4 Hook Trust

Codex hooks may require trust review depending on where they are loaded from. Puppetmaster should prefer a generated config path and hook path that can be trusted as part of the managed launch. If Codex requires interactive trust approval, v1 must document the setup step or provide a bootstrap command that performs it.

The implementation should verify during validation that hooks actually run in a managed Codex session.

## 11. Codex Hook Protocol

Codex supports lifecycle hooks through `hooks.json` or inline `[hooks]` tables. V1 depends on `Stop` and may use `SessionStart` and `UserPromptSubmit`.

### 11.1 Subagent Stop Hook

Every subagent should have a `Stop` hook. On each stopped turn, the hook calls Puppetmaster CLI:

```bash
puppet hook stop \
  --agent-id "$PUPPETMASTER_AGENT_ID"
```

The hook receives JSON on stdin from Codex. Puppetmaster should store the raw hook input or a normalized subset that includes:

```text
session_id
turn_id
cwd
model
permission_mode
transcript_path
last_assistant_message
stop_hook_active
```

The subagent Stop hook should usually return an empty success response so the subagent remains idle. It should not automatically continue the subagent unless Puppetmaster has queued a prompt specifically for that subagent.

### 11.2 Orchestrator Stop Hook

The orchestrator Stop hook is the reliable event delivery mechanism.

On each orchestrator turn stop:

1. The hook calls Puppetmaster CLI with the orchestrator agent id.
2. Puppetmaster checks for pending child-agent events addressed to the orchestrator.
3. If there are no events, the hook exits successfully with no continuation.
4. If there are events, Puppetmaster returns Codex Stop-hook JSON that asks Codex to continue with a synthesized event prompt.

The continuation response should use Codex's supported `Stop` hook behavior:

```json
{
  "decision": "block",
  "reason": "PUPPETMASTER EVENT\n\nAgent abc123 completed.\n..."
}
```

Codex treats this as a continuation prompt. This avoids needing an out-of-band push channel.

### 11.3 Explicit Completion

Agents are instructed to call `complete_agent` when they finish, fail, or block. The Stop hook is fallback only.

The generated initial prompt for every subagent should include:

```text
When your assigned work is complete, blocked, or failed, call complete_agent.
Do not rely on a final chat message alone.
If you are blocked waiting for instructions, call complete_agent with status "blocked" and explain exactly what is needed.
```

### 11.4 Hook Failure Handling

Hook failures should not silently disappear. Puppetmaster should record:

1. Hook event name.
2. Agent id.
3. Exit code.
4. stderr.
5. Raw input path if stored.
6. Whether Codex continued or aborted.

For v1, hook failure should not kill the agent. It should create a `hook.error` event and leave the agent inspectable.

## 12. MCP Tool Surface

The MCP API is the main agent-facing control surface. Tool names should be boring, explicit, and stable.

### 12.1 `create_agent`

Creates a child Codex agent.

Input:

```json
{
  "name": "optional short name",
  "goal": "optional boolean; true prepends /goal to the prompt",
  "description": "what this agent is for",
  "prompt": "full initial prompt",
  "cwd": "/absolute/path",
  "metadata": {}
}
```

Required fields:

1. `cwd`
2. `prompt`

`goal` is an optional boolean. When true, Puppetmaster starts the child in goal mode by prepending literal `/goal ` to the start of `prompt`. It does nothing else. `description` is an optional short human-readable label; if omitted, Puppetmaster may derive it from `prompt`.

Behavior:

1. Identify caller from environment/session metadata when possible.
2. Validate cwd.
3. Enforce max depth and child count.
4. Create registry record.
5. Write prompt and generated config.
6. Create tmux session.
7. Start Codex.
8. Start terminal logging.
9. Record `agent.created` and `agent.started`.
10. Return agent id, tmux session, attach command, cwd, and status.

Output:

```json
{
  "agent_id": "agt_...",
  "tmux_session": "puppet_agt_...",
  "attach_command": "tmux attach -t puppet_agt_...",
  "cwd": "/absolute/path",
  "status": "starting"
}
```

### 12.2 `prompt_agent`

Sends a prompt to an existing agent.

Input:

```json
{
  "agent_id": "agt_...",
  "prompt": "next instruction"
}
```

Behavior:

1. Validate agent exists.
2. Validate tmux session exists.
3. Queue or send prompt.
4. Use tmux paste buffer for multiline content.
5. Record `agent.prompted`.

Prompt delivery must preserve newlines. It should not use naive shell quoting.

Output:

```json
{
  "accepted": true,
  "agent_id": "agt_...",
  "status": "running"
}
```

### 12.3 `read_agent`

Reads recent terminal output and/or stored logs.

Input:

```json
{
  "agent_id": "agt_...",
  "lines": 120,
  "source": "log"
}
```

`source` may be `log`, `tmux`, or `auto`. `auto` should prefer tmux for live sessions and log for dead sessions.

Output:

```json
{
  "agent_id": "agt_...",
  "status": "idle",
  "output": "...",
  "truncated": false
}
```

### 12.4 `inspect_agent`

Returns a complete operational summary.

Input:

```json
{
  "agent_id": "agt_..."
}
```

Output should include:

1. Agent metadata.
2. Parent and children.
3. Cwd.
4. Purpose and initial prompt summary/path.
5. Current status.
6. Completion state.
7. Tmux session.
8. Attach command.
9. Recent events.
10. Recent output.
11. Log path.
12. Known failure state.

`inspect_agent` is the primary recovery tool when the orchestrator forgets what a subagent was supposed to do.

### 12.5 `list_agents`

Lists known agents.

Input:

```json
{
  "root_id": "optional",
  "parent_id": "optional",
  "status": "optional",
  "include_dead": true
}
```

Output should include compact summaries: id, name, description, cwd, status, parent id, age, last event, and attach command.

### 12.6 `complete_agent`

Records explicit completion, failure, or blocked status.

Input:

```json
{
  "status": "success",
  "summary": "what happened",
  "result": "optional detailed result",
  "files_changed": ["optional"],
  "next_steps": ["optional"]
}
```

Allowed statuses:

```text
success
failed
blocked
cancelled
```

Behavior:

1. Identify calling agent.
2. Update status.
3. Record completion event.
4. Queue event for parent and root orchestrator.
5. Return recorded state.

This tool should normally not require the caller to pass `agent_id`. The server should infer it from the managed Codex environment. A human CLI equivalent may accept `--agent-id`.

### 12.7 `stop_agent`

Gracefully stops an agent.

Behavior should try increasingly forceful actions:

1. Send a graceful prompt or interrupt if supported.
2. Send Ctrl-C to the tmux pane.
3. Send an exit command if appropriate.
4. Mark stopped if the process exits.

`stop_agent` should not delete logs or metadata.

### 12.8 `kill_agent`

Force-kills a tmux session or process.

This is destructive to live state but must preserve registry metadata and logs.

### 12.9 `pause_agent` And `resume_agent`

V1 pause can be process-level rather than semantic:

1. `pause_agent` may send SIGSTOP to the process group or detach/mark paused if SIGSTOP is not reliable.
2. `resume_agent` may send SIGCONT.

If process-level pause is too brittle, v1 may implement pause as "do not deliver queued prompts and mark paused" while leaving Codex idle. The chosen behavior must be explicit in validation.

### 12.10 `attach_agent`

Returns human attach instructions.

Output:

```json
{
  "agent_id": "agt_...",
  "tmux_session": "puppet_agt_...",
  "attach_command": "tmux attach -t puppet_agt_..."
}
```

## 13. Human CLI Surface

The CLI should mirror the MCP API and add operational commands.

Required v1 commands:

```text
puppet orchestrator start --cwd <cwd> --prompt <prompt-or-file>
puppet agent create --cwd <cwd> --description <text> --prompt <text-or-file>
puppet agent list
puppet agent inspect <agent-id>
puppet agent read <agent-id> --lines 120
puppet agent prompt <agent-id> --prompt <text-or-file>
puppet agent attach <agent-id>
puppet agent stop <agent-id>
puppet agent kill <agent-id>
puppet agent complete <agent-id> --status success --summary <text>
puppet hook stop --agent-id <agent-id>
puppet hook drain-events --agent-id <agent-id>
puppet doctor
```

The CLI is used by:

1. Humans.
2. Codex hooks.
3. Wrapper scripts generated by Puppetmaster.
4. Validation tests.

The CLI should support machine-readable JSON output for automation:

```bash
puppet agent inspect agt_123 --json
```

## 14. Orchestrator Wakeup

The orchestrator should be informed about child-agent events without blocking on long waits.

### 14.1 Event Queue

When a child agent completes, fails, blocks, exits, or stops a turn, Puppetmaster creates an event. Important events are addressed to:

1. The direct parent.
2. The root orchestrator.

Events remain pending until delivered through the orchestrator Stop hook, prompt injection, or explicit poll/read.

### 14.2 Stop Hook Continuation

The orchestrator Stop hook drains pending events. If events exist, it returns a continuation prompt.

The event prompt should be concise:

```text
PUPPETMASTER EVENT

Agent agt_123 completed.

Name: auth-audit
Status: success
Cwd: /repo
Summary: Patched JWT expiry handling and added regression tests.

Available actions:
- inspect_agent({"agent_id":"agt_123"})
- read_agent({"agent_id":"agt_123","lines":120})
- prompt_agent({"agent_id":"agt_123","prompt":"..."})
- stop_agent({"agent_id":"agt_123"})
```

If multiple events are pending, Puppetmaster should batch a small number into one prompt and mention that more are available if the queue is long. The goal is to avoid flooding the orchestrator context.

### 14.3 Opportunistic Tmux Injection

If Puppetmaster can determine the orchestrator is idle, it may paste an event prompt into the orchestrator tmux session immediately. This improves responsiveness but must not be required for correctness.

Prompt injection must be queued or suppressed if:

1. The orchestrator is generating.
2. The orchestrator tmux session is missing.
3. The orchestrator is paused.
4. A previous event prompt is still pending acknowledgement.

### 14.4 Event Acknowledgement

Event delivery and acknowledgement are separate.

An event is **delivered** when Puppetmaster sends it to the orchestrator through hook continuation or tmux injection.

An event is **acknowledged** when the orchestrator calls a tool that consumes or marks it, or when Puppetmaster decides a continuation prompt has been accepted.

V1 may treat delivered events as acknowledged after successful hook continuation if that is simpler, but the registry schema should leave room for separate acknowledgement.

## 15. Status Detection

V1 should avoid overpromising status. Reliable signals:

1. Registry state from explicit tools.
2. Tmux session exists or does not exist.
3. Process exit detected by supervisor or tmux.
4. Codex Stop hook fired.
5. `complete_agent` was called.

Unreliable signals:

1. Terminal text that looks like "done."
2. Prompt-like screen shapes.
3. Timing-based idle guesses.

Status should be conservative. If Puppetmaster cannot classify an agent, it should report `unknown` with evidence.

## 16. Safety And Limits

Even though v1 intentionally runs Codex without sandboxing, Puppetmaster should prevent orchestration runaway.

Recommended defaults:

```text
max_depth = 3
max_concurrent_children_per_agent = 5
max_total_agents = 30
max_event_prompt_events = 5
max_log_read_lines_default = 120
max_log_read_lines_hard = 2000
```

`max_concurrent_children_per_agent` counts only nonterminal child agents. Completed, failed, blocked, stopped, killed, and dead children do not consume child concurrency.

The system should refuse new agent creation when limits are exceeded and return a clear error with the current counts.

Puppetmaster should also record who created each agent and from which parent, so runaway trees can be inspected and stopped.

## 17. Failure Modes

### 17.1 Codex Launch Fails

Expected behavior:

1. Mark agent `failed`.
2. Record exit code and stderr if available.
3. Create `agent.failed` event.
4. Queue event to parent/root.
5. Preserve prompt and config files.

### 17.2 Tmux Session Dies

Expected behavior:

1. Mark agent `dead` unless it was intentionally stopped or killed.
2. Record `agent.process_exited`.
3. Preserve logs.
4. Let `inspect_agent` show the last known output and termination reason.

### 17.3 Hook Does Not Run

Expected behavior:

1. `puppet doctor` should detect this during setup.
2. Process watcher and explicit completion still work.
3. Orchestrator event delivery may degrade to polling or tmux injection.

### 17.4 Orchestrator Dies

Expected behavior:

1. Subagents continue running unless explicitly configured otherwise.
2. Events continue to be recorded.
3. Human can inspect and restart/resume orchestrator.
4. New orchestrator can drain existing pending events.

### 17.5 Registry Corruption

Expected behavior:

1. CLI reports corruption clearly.
2. Per-agent metadata files and logs allow manual recovery.
3. Future repair command can reconstruct partial registry from `.puppetmaster/agents`.

### 17.6 Prompt Injection Happens While Busy

This should be avoided. If it occurs, it may corrupt the active user input area. The design should minimize risk by relying on Stop-hook continuation as the correctness path.

## 18. Validation Strategy

Validation must cover:

1. Unit tests for registry, event queue, cwd validation, and limits.
2. Integration tests with fake tmux/Codex where possible.
3. Live smoke tests with real tmux and Codex when credentials/environment allow.
4. Hook tests that prove `Stop` hooks call Puppetmaster.
5. End-to-end orchestration tests with a root and at least one child.
6. Recovery tests where tmux session dies.
7. Human attach/read tests.

The system should include a `doctor` command that checks:

1. `tmux` exists.
2. `codex` exists.
3. Codex version is detectable.
4. Required Codex flags appear supported.
5. Hooks are enabled.
6. State directory is writable.
7. MCP server can start.
8. Generated hook scripts can call the CLI.

## 19. V1 Milestones

The implementation is split into seven milestones:

1. `001-supervisor-core`: registry, agent model, tmux lifecycle, logs.
2. `002-codex-launch-and-hooks`: Codex launch wrapper, generated config, hooks.
3. `003-mcp-agent-tools`: MCP server and agent-facing tools.
4. `004-orchestrator-event-loop`: event queue, completion flow, orchestrator wakeup.
5. `005-human-cli-and-attach`: human CLI, attach/read/inspect operations.
6. `006-recovery-status-and-observability`: status reconciliation, process watching, diagnostics.
7. `007-hardening-and-release`: limits, docs, packaging, final end-to-end validation.

Each milestone has a `milestone.md`, `implementation-plan.md`, and `validation.md`.

## 20. Open Questions

These are intentionally left open until implementation begins:

1. Implementation language and package structure.
2. SQLite versus JSON-first storage.
3. Exact generated Codex config strategy.
4. Whether pause/resume uses process signals or logical prompt-delivery pause.
5. Whether v1 supports multiple root orchestrators concurrently.
6. Whether event acknowledgement is explicit or inferred from delivery.
7. How much Codex session id discovery is possible without depending on unstable transcript formats.

None of these block the v1 architecture.
