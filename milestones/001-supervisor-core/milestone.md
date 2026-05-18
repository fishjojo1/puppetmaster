# Milestone 001: Supervisor Core

## Objective

Build the local foundation that Puppetmaster depends on: durable agent metadata, tmux session lifecycle operations, terminal log capture, and basic agent inspection. This milestone does not need Codex-specific hooks, MCP, or orchestration wakeup. It proves that Puppetmaster can create and track long-lived terminal-backed agents as first-class local resources.

## Reader

This milestone is for the engineer implementing the first vertical slice. After reading it, they should be able to build a local supervisor that can start a managed process in tmux, record it, inspect it, read its logs, and stop or kill it.

## Scope

This milestone includes:

1. Project scaffold.
2. Configuration loading.
3. State directory creation.
4. Agent id generation.
5. Registry storage.
6. Agent metadata persistence.
7. Tmux session creation.
8. Tmux session existence checks.
9. Terminal log capture.
10. Basic status updates.
11. Core CLI commands needed to exercise the supervisor.

This milestone excludes:

1. MCP server implementation.
2. Codex generated config.
3. Codex hook integration.
4. Orchestrator event wakeup.
5. Recursive subagent creation from inside Codex.

## Required Behavior

The supervisor must be able to create an agent record before launching the process, update it after launch, and preserve enough data to inspect failures if launch fails.

The first managed process may be a generic shell command for validation, but the command abstraction must be compatible with Codex launch later. The design should not bake in assumptions that only work for `sleep` or `bash`.

Each managed agent must have:

1. Stable id.
2. Optional parent id.
3. Root id.
4. Role.
5. Name.
6. Description.
7. Initial prompt path.
8. Absolute cwd.
9. Tmux session name.
10. Status.
11. Created/updated timestamps.
12. Log path.

The tmux session name must be deterministic from the agent id and safe for tmux. A recommended format is:

```text
puppet_<agent-id>
```

The registry must support at least:

1. Create agent.
2. Get agent.
3. List agents.
4. Update status.
5. Append event.
6. List recent events for agent.

## CLI Commands

Implement enough CLI to test the core manually:

```text
puppet agent create-raw --cwd <cwd> --description <text> --command <command>
puppet agent list
puppet agent inspect <agent-id>
puppet agent read <agent-id> --lines <n>
puppet agent stop <agent-id>
puppet agent kill <agent-id>
puppet doctor
```

`create-raw` is a temporary development command. It can remain as a debug command later, but production agent creation will be Codex-specific in later milestones.

## Invariants

1. No live tmux session should exist without a registry record unless it predates Puppetmaster or was manually created.
2. Agent creation should be durable before process launch.
3. Logs must be readable after the tmux session exits.
4. `inspect` must work for live, stopped, killed, failed, and dead agents.
5. `kill` must never delete metadata or logs.
6. Cwd must be absolute and existing.

## Deliverables

1. A runnable local CLI entry point.
2. A registry implementation.
3. State directory management.
4. Tmux supervisor functions.
5. Agent log capture.
6. Core docs in command help.
7. Tests or smoke scripts proving create/list/read/inspect/stop/kill.

