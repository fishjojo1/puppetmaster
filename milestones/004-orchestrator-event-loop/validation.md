# Validation Plan

## Automated Validation

### Delivery Tests

Verify:

1. Completion creates delivery for parent.
2. Completion creates delivery for root when root differs.
3. Completion does not duplicate root delivery when parent is root.
4. Failed and blocked events are marked warning/error.
5. Delivered events are not returned again unless acknowledgement semantics require it.

### Coalescing Tests

Verify:

1. Repeated turn-stopped events coalesce.
2. Completion event is not coalesced.
3. Prompting an agent allows a new turn-stopped event to be queued.

### Formatter Tests

Verify:

1. Single event prompt starts with `PUPPETMASTER EVENT`.
2. Multiple event prompt starts with `PUPPETMASTER EVENTS`.
3. Long summaries are truncated or bounded.
4. More-events message appears when queue exceeds limit.
5. Tool suggestions include valid MCP tool names.

### Drain Tests

Feed pending events into:

```bash
puppet hook drain-events --agent-id <orchestrator-id>
```

Expected:

1. JSON contains `decision: block`.
2. Reason contains event prompt.
3. Deliveries are marked delivered.
4. No events produces empty or continue response.

## End-To-End Validation

Start orchestrator through Puppetmaster.

From orchestrator, create a child agent with a prompt that calls `complete_agent(success, ...)`.

Expected:

1. Child starts.
2. Child completes.
3. Completion event is queued to orchestrator.
4. Orchestrator Stop hook drains event.
5. Orchestrator receives continuation prompt containing child id and summary.

## Manual Validation

Inspect queue state:

```bash
puppet agent inspect <orchestrator-id>
```

Expected:

1. Pending/delivered events visible.
2. Child completion visible.
3. Event timestamps coherent.

Check subagent:

```bash
puppet agent inspect <child-id>
```

Expected:

1. Status completed.
2. Completion summary visible.
3. Parent/root ids correct.

## Failure Validation

Make a child call `complete_agent(status="blocked")`.

Expected:

1. Child status blocked.
2. Orchestrator event prompt says blocked.
3. Suggested actions include `prompt_agent`.

Kill a child tmux session unexpectedly.

Expected:

1. Process watcher or reconciliation marks it dead in a later milestone.
2. If detection exists already, event is queued.
3. If not, `inspect_agent` shows tmux missing.

## Completion Criteria

This milestone is complete when:

1. Subagent completion wakes the orchestrator through Stop hook continuation.
2. No blocking wait tool is required.
3. Repeated low-signal events do not spam the orchestrator.
4. Event state is inspectable and durable.

