# Implementation Plan

## 1. Create MCP Server Entrypoint

Add a command:

```text
puppet mcp serve
```

The command should start an MCP server over stdio. Generated Codex config will invoke this command.

The server process should receive:

```text
PUPPETMASTER_AGENT_ID
PUPPETMASTER_ROOT_AGENT_ID
PUPPETMASTER_STATE_DIR
```

These values establish caller identity and registry location.

## 2. Define Tool Schemas

Implement JSON schemas for all v1 tools. Keep schemas strict:

1. Required fields are required.
2. Unknown fields should be rejected or ignored consistently.
3. Status enums should be explicit.
4. Line limits should have min/max bounds.

Important schemas:

```text
create_agent(description, prompt, cwd, name?, metadata?)
complete_agent(status, summary, result?, files_changed?, next_steps?)
read_agent(agent_id, lines?, source?)
inspect_agent(agent_id)
list_agents(root_id?, parent_id?, status?, include_dead?)
```

## 3. Implement Caller Context

Create an internal `CallerContext`:

```text
agent_id
root_id
role
cwd
```

Every MCP handler should receive this context. If caller identity is missing:

1. Allow read-only operations only if explicitly configured, or
2. Return an error saying the MCP server is not running in a managed agent context.

For v1, prefer failing closed when identity is missing.

## 4. Implement Authorization

Implement simple tree-based authorization:

1. Root can manage any agent with same root id.
2. Parent can manage descendants.
3. Agent can read itself.
4. Agent can complete itself.

Return clear errors:

```text
not_found
not_authorized
invalid_state
limit_exceeded
```

## 5. Implement `create_agent`

Handler steps:

1. Validate caller.
2. Validate required cwd.
3. Validate cwd absolute and existing.
4. Load caller agent.
5. Compute child depth.
6. Enforce max depth.
7. Count existing children.
8. Enforce child count.
9. Call Codex runtime create service.
10. Return compact result.

The child inherits root id from caller.

## 6. Implement `complete_agent`

Handler steps:

1. Validate caller.
2. Validate status.
3. Update caller agent completion status.
4. Set overall agent status to completed, failed, blocked, or stopped-equivalent.
5. Store summary/result.
6. Append `agent.completed`, `agent.failed`, or `agent.blocked`.
7. Queue event for parent and root.
8. Return recorded state.

This is the primary completion signal. It must be reliable and easy for Codex to call.

## 7. Implement Read/Inspect/List

`read_agent` should reuse the supervisor read service.

`inspect_agent` should aggregate:

1. Agent metadata.
2. Parent summary.
3. Children summaries.
4. Recent events.
5. Recent output.
6. Attach command.

`list_agents` should support filters but default to agents in the caller's root tree.

## 8. Implement Prompt/Stop/Kill/Pause/Resume/Attach

Keep these wrappers around existing supervisor services.

`prompt_agent` must use robust multiline prompt delivery:

1. Write text into tmux buffer.
2. Paste buffer into pane.
3. Send Enter.

If this proves unreliable with Codex's input area, document the limitation and adjust prompt delivery in the next milestone.

## 9. Update Generated Codex Config

Managed Codex sessions must include Puppetmaster MCP server configuration.

The generated config should start:

```text
puppet mcp serve
```

with the managed environment variables set by the launch wrapper.

Validate that Codex can see the MCP tools.

## 10. Tool Output Style

Tool outputs should be compact but complete. Avoid dumping huge logs by default. Prefer:

1. Status.
2. Ids.
3. Attach command.
4. Short summary.
5. Instruction to call `read_agent` for more.

## 11. Error Output Style

Errors should be model-actionable:

```json
{
  "error": {
    "code": "cwd_required",
    "message": "create_agent requires an absolute cwd in v1.",
    "hint": "Pass the directory the child Codex should operate in."
  }
}
```

