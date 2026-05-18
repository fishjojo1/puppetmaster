# Implementation Plan

## 1. Implement Tmux Inventory

Add a function:

```text
list_tmux_sessions(prefix)
```

It should return:

1. Session name.
2. Creation time if available.
3. Attached/detached state if available.
4. Pane command if available.

Use this for reconcile and debug output.

## 2. Implement Registry Inventory

Add a registry query:

```text
list_nonterminal_agents()
```

Nonterminal statuses:

```text
starting
running
idle
awaiting_input
unknown
```

Terminal-ish statuses:

```text
completed
failed
blocked
stopped
killed
dead
```

Note that `blocked` may still have a live tmux session; do not assume terminal means tmux is gone.

## 3. Implement Reconciliation Rules

Create deterministic rules:

### Missing tmux for running agent

If status is `starting`, `running`, `idle`, or `awaiting_input`, and tmux session is missing:

1. Mark `dead`.
2. Record `agent.dead_detected`.
3. Queue warning event to parent/root.

### Live tmux for completed agent

If status is `completed` and tmux exists:

1. Keep completed.
2. Record or surface note `live_after_completion`.
3. Do not kill automatically.

### Stop hook after prompt

If latest `agent.turn_stopped` is newer than latest `agent.prompted` and status is running:

1. Mark idle.

### Killed/stopped state

If status is killed or stopped:

1. Do not change it unless a human explicitly reconciles.

### Unknown mismatch

If evidence conflicts:

1. Mark unknown or leave current status.
2. Record inconsistency event.
3. Show evidence in inspect.

## 4. Add Reconcile Command

Command:

```text
puppet reconcile
```

Options:

```text
--agent <agent-id>
--root <root-id>
--dry-run
--json
```

Default should reconcile all known agents and print a summary.

Dry run should show proposed changes without writing.

## 5. Add Watcher Mode

Optional but useful:

```text
puppet supervisor watch
```

This long-running process periodically reconciles and handles stale process events. If v1 does not include a daemon, reconciliation can run opportunistically at the start of CLI/MCP commands.

Recommended v1 compromise:

1. Run cheap reconciliation for target agents during read/inspect/list.
2. Provide explicit `puppet reconcile`.
3. Add daemon later if needed.

## 6. Improve Inspect Evidence

`inspect_agent` should show:

1. Registry status.
2. Reconciled status.
3. Tmux exists yes/no.
4. Last prompt time.
5. Last turn stop time.
6. Last completion time.
7. Last hook error.
8. Recent supervisor errors.

This makes status understandable.

## 7. Implement Structured Logging

Add a logger that writes JSONL:

```json
{
  "ts": "...",
  "level": "info",
  "event": "agent.create.requested",
  "agent_id": "agt_...",
  "root_id": "agt_root",
  "message": "Creating Codex agent"
}
```

Do not write full terminal output into the supervisor log. Terminal output belongs in per-agent logs.

## 8. Expand Doctor

`doctor --deep` should check:

1. tmux installed.
2. Codex installed.
3. Required Codex flags.
4. Hooks feature.
5. State dir permissions.
6. Registry schema version.
7. Registry/tmux consistency.
8. Log files writable.
9. MCP server can initialize.
10. Hook scripts are executable.

## 9. Add Human Override Commands

`agent mark-status` should:

1. Validate status.
2. Require reason.
3. Update status.
4. Append human override event.
5. Queue event if status is meaningful to parent/root.

This is important when humans intervene manually inside tmux.

## 10. Cleanup Commands

`cleanup-dead` should:

1. List dead/killed/stopped agents.
2. Optionally remove stale tmux sessions for killed/stopped agents.
3. Never delete logs unless `--delete-logs` exists and is explicit.

For v1, deletion can be omitted. Marking and reporting are enough.

