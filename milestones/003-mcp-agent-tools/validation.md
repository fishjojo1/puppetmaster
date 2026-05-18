# Validation Plan

## Automated Validation

### Tool Schema Tests

Verify:

1. `create_agent` rejects missing cwd.
2. `create_agent` rejects relative cwd.
3. `complete_agent` rejects invalid status.
4. `read_agent` enforces line limits.
5. Unknown source values are rejected.

### Caller Context Tests

Verify:

1. MCP server started without agent id rejects mutating tools.
2. Agent can complete itself.
3. Parent can inspect child.
4. Sibling cannot manage sibling unless root policy allows it.
5. Root can list all descendants.

### Limit Tests

Verify:

1. Max depth enforced.
2. Max children per agent enforced.
3. Max total agents enforced if implemented.
4. Errors include current limit and attempted action.

## MCP Integration Validation

Start a managed Codex session with Puppetmaster MCP enabled.

Ask it to call:

```text
list_agents
```

Expected:

1. Tool appears.
2. Tool call succeeds.
3. Output includes the calling agent or root tree.

Ask it to call:

```text
complete_agent(status="blocked", summary="MCP smoke test")
```

Expected:

1. Calling agent status becomes blocked.
2. Event is recorded.
3. Parent/root queue receives event if parent exists.

## Child Creation Validation

From a managed Codex session, call `create_agent` with:

```json
{
  "description": "child MCP smoke test",
  "prompt": "Say hello and then call complete_agent with success.",
  "cwd": "/home/kek/Projects/pupptermaster"
}
```

Expected:

1. Child registry record exists.
2. Child parent id is the caller.
3. Child root id matches caller root.
4. Child tmux session exists.
5. Child log exists.
6. Child can call `complete_agent`.

## Prompt Tool Validation

Create a long-running child agent and call:

```text
prompt_agent(child, multiline prompt)
```

Expected:

1. Prompt arrives with newlines preserved.
2. Agent responds.
3. `agent.prompted` event is recorded.

## Completion Criteria

This milestone is complete when:

1. Managed Codex can see and call Puppetmaster MCP tools.
2. A managed agent can create a child with caller-provided cwd.
3. Parent-child relationships are automatic.
4. Explicit completion records durable events.
5. Tool errors are clear enough for Codex to self-correct.

