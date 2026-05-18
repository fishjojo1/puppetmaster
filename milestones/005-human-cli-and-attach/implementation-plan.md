# Implementation Plan

## 1. Normalize CLI Structure

Move temporary commands into final groups:

```text
orchestrator
agent
events
hook
mcp
doctor
debug
```

Keep CLI handlers thin. They should call the same services used by MCP.

## 2. Implement Orchestrator Start

Command:

```text
puppet orchestrator start --cwd <cwd> --prompt <text>
puppet orchestrator start --cwd <cwd> --prompt-file <file>
```

Behavior:

1. Validate cwd.
2. Create root agent with role orchestrator.
3. Generate Codex config with orchestrator Stop hook.
4. Launch Codex in tmux.
5. Start logs.
6. Print id and attach command.

If an orchestrator already exists and is running, return a clear error unless `--new-root` is supplied. V1 can support one active orchestrator by default.

## 3. Implement Agent Create

Human CLI `agent create` should use the same Codex creation service as MCP `create_agent`.

For human-created agents without an active orchestrator:

1. Either require `--parent <agent-id>`, or
2. Create as rootless/manual.

Recommended v1 behavior: require an orchestrator or explicit parent. This keeps the tree coherent.

## 4. Implement List And Tree

`agent list` should support filters:

```text
--status
--root
--parent
--include-dead
--json
```

`agent tree` should display parent-child relationships:

```text
agt_root orchestrator running /repo
  agt_a completed auth-audit /repo
  agt_b blocked frontend-fix /repo/ui
    agt_c running test-investigation /repo/ui
```

## 5. Implement Inspect Rendering

Build a structured inspection object first, then render human or JSON.

Human rendering should be stable and easy to scan:

```text
Agent: agt_123
Name: auth-audit
Status: blocked
Cwd: /repo
Parent: agt_root
Tmux: puppet_agt_123
Attach: tmux attach -t puppet_agt_123

Description:
Audit auth middleware for expiry bugs.

Recent events:
- blocked: Needs clarification on token refresh behavior.

Recent output:
...
```

## 6. Implement Read

`agent read` should:

1. Default to recent log lines.
2. Support `--source log|tmux|auto`.
3. Enforce max line count.
4. Work for dead sessions using stored log.

## 7. Implement Attach

`agent attach` should:

1. Validate agent exists.
2. Validate tmux exists.
3. Execute tmux attach.

Add:

```text
--print
```

to print command without executing.

## 8. Implement Prompt

`agent prompt` should support:

```text
--prompt <text>
--prompt-file <path>
```

It should call the same prompt delivery service as MCP.

After prompt delivery, update status to running if appropriate and append `agent.prompted`.

## 9. Implement Manual Completion

`agent complete` lets a human mark a session:

```text
puppet agent complete agt_123 --status blocked --summary "Needs product answer."
```

It should use the same completion service as MCP, with source `human_cli`.

## 10. Implement Events Commands

Commands:

```text
puppet events list
puppet events pending <agent-id>
puppet events ack <event-or-delivery-id>
```

`ack` may be optional in v1 if delivery auto-acknowledgement is used.

## 11. Improve Help Text

Every command should explain:

1. What it does.
2. Whether it requires a live tmux session.
3. Whether it mutates agent state.
4. JSON support.

