# Milestone 006: Recovery, Status, And Observability

## Objective

Make Puppetmaster resilient when real local processes behave badly. Agents may crash, tmux sessions may disappear, hooks may fail, Codex may stop without completing, and the orchestrator may lose context. This milestone adds reconciliation, diagnostics, structured events, and recovery tools so the system is debuggable instead of mysterious.

## Reader

This milestone is for the engineer hardening lifecycle state and operational visibility. After reading it, they should be able to implement status reconciliation, process watching, better diagnostics, and recovery commands.

## Scope

This milestone includes:

1. Status reconciliation.
2. Process/tmux watcher.
3. Dead-session detection.
4. Hook failure visibility.
5. Log health checks.
6. Registry consistency checks.
7. Doctor expansion.
8. Recovery commands.
9. Structured internal logging.
10. Stale session cleanup.

This milestone excludes:

1. Distributed monitoring.
2. External metrics backends.
3. Web UI.
4. Automatic registry reconstruction from every possible corruption mode.

## Required Behavior

Puppetmaster must be able to answer:

1. Which agents are alive?
2. Which tmux sessions exist without registry records?
3. Which registry agents point to missing tmux sessions?
4. Which hooks have failed recently?
5. Which agents have pending events?
6. Which agents are completed but still have live sessions?
7. Which agents are running but have not emitted events recently?

The system should prefer conservative status over false confidence. If state is inconsistent, report `unknown` with evidence.

## Status Reconciliation

Implement a reconciliation pass that compares:

1. Registry status.
2. Tmux session existence.
3. Process existence if available.
4. Last hook event.
5. Last prompt event.
6. Completion status.

Reconciliation should update statuses only when evidence is strong.

Examples:

1. Agent marked running, tmux missing, not intentionally stopped: mark dead.
2. Agent marked completed, tmux still exists: keep completed but note live session.
3. Agent marked running, Stop hook fired after last prompt: mark idle.
4. Agent marked killed, tmux missing: keep killed.

## Observability Events

Add or refine events:

```text
supervisor.reconciled
supervisor.inconsistency_detected
agent.dead_detected
agent.live_after_completion
hook.failed
hook.skipped
log.capture_failed
registry.repair_suggested
```

Each event should include enough payload for a future engineer to debug without reproducing immediately.

## Recovery Commands

Required commands:

```text
puppet doctor --deep
puppet reconcile
puppet agent mark-status <agent-id> --status <status> --reason <text>
puppet agent cleanup-dead
puppet debug tmux
puppet debug registry
```

`mark-status` is a human override. It must record an event and should not be used silently by automation.

`cleanup-dead` should never delete logs by default. It may mark stale sessions or optionally kill known-dead tmux sessions with explicit flags.

## Structured Logs

Puppetmaster itself should write structured logs separate from agent terminal logs.

Recommended:

```text
.puppetmaster/puppetmaster.log.jsonl
```

Log important operations:

1. Agent create requested.
2. Tmux launch command started.
3. Tmux launch failed.
4. Hook event received.
5. MCP tool called.
6. Event delivered.
7. Reconciliation changed status.
8. Errors.

Avoid logging secrets or full prompts by default. Store full prompts in the per-agent prompt file that the user already expects to exist.

## Deliverables

1. Reconciliation service.
2. Process/tmux watcher or manual reconcile command.
3. Expanded `doctor`.
4. Recovery CLI commands.
5. Structured Puppetmaster log.
6. Better inspect output for inconsistent state.
7. Validation for dead sessions, hook failures, and registry/tmux mismatch.

