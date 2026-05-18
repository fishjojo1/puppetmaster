# Milestone 003: MCP Agent Tools

## Objective

Expose Puppetmaster's agent operations as MCP tools that Codex can call. This milestone turns the local supervisor into an agent-controllable system. A managed Codex session should be able to create child agents, inspect them, read logs, prompt them, and report completion through tools.

## Reader

This milestone is for the engineer implementing the MCP server and tool handlers. After reading it, they should be able to build the MCP surface on top of the existing supervisor services without duplicating CLI logic.

## Scope

This milestone includes:

1. MCP server entry point.
2. Tool schemas.
3. Tool handlers.
4. Caller identity inference.
5. Parent-child relationship creation.
6. Depth and child-count limits.
7. Generated Codex config updated to include Puppetmaster MCP.
8. `complete_agent` implementation.

This milestone excludes:

1. Orchestrator event continuation.
2. Advanced status reconciliation.
3. Browser UI or dashboard.
4. Non-Codex clients.

## Required Tools

V1 MCP tools:

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

The server should present concise descriptions. Tool descriptions should be written for an agent that needs to understand when to use them.

## Caller Identity

The MCP server should infer the calling managed agent from environment or connection metadata. The preferred source is:

```text
PUPPETMASTER_AGENT_ID
```

If the MCP server is shared across agents and cannot infer identity from process env alone, the generated per-agent MCP configuration must include a way to pass the agent id to the server process. Options include:

1. Starting one MCP server process per Codex session with the agent id in env.
2. Passing an agent-scoped token in MCP server args.
3. Using a local socket path per agent.

The design must not require the model to manually pass `parent_id` for normal child creation. The parent relationship should be automatic.

## Tool Behavior

`create_agent` must require caller-provided absolute cwd. If the caller omits cwd, return a clear error. Do not default to parent cwd in v1.

`complete_agent` should not require `agent_id` for managed Codex callers. It should complete the calling agent.

Tools that target another agent must validate that the caller has permission. For v1, permission can be simple:

1. Orchestrator can manage all agents under its root.
2. Parent can manage its descendants.
3. Agent can complete itself.

## Deliverables

1. MCP server command.
2. Tool schema definitions.
3. Tool handlers backed by service layer.
4. Generated Codex config that registers Puppetmaster MCP.
5. Live validation that a managed Codex session can call at least `list_agents` and `complete_agent`.
6. Live or simulated validation that `create_agent` creates a child with correct parent id.

