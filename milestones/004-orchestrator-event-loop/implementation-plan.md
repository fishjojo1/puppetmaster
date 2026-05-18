# Implementation Plan

## 1. Define Event Addressing

Add delivery records or fields that allow one event to be addressed to multiple agents. There are two viable approaches:

1. Duplicate event rows per recipient.
2. Keep one event row and add an `event_deliveries` table.

Prefer an `event_deliveries` table if using SQLite:

```text
event_deliveries(
  id text primary key,
  event_id text not null,
  recipient_agent_id text not null,
  status text not null,
  created_at text not null,
  delivered_at text null,
  acknowledged_at text null
)
```

This avoids duplicating payloads and supports parent/root delivery cleanly.

## 2. Queue Completion Events

Update `complete_agent` service:

1. Create event with type based on completion status.
2. Create delivery for parent if present.
3. Create delivery for root if root differs from parent.
4. Mark event severity based on status:
   - success: info
   - blocked: warning
   - failed: error
   - cancelled: warning

## 3. Queue Turn-Stopped Events

Update hook stop handler:

1. Record `agent.turn_stopped`.
2. Decide whether to queue delivery.
3. Coalesce repeated turn-stopped events from the same agent if no prompt/completion happened since the last one.

Coalescing can be simple:

1. If a pending `agent.turn_stopped` delivery already exists for the same agent and recipient, update its timestamp/payload instead of creating a new one.
2. Completion/failure/blocked events should never be coalesced away.

## 4. Implement Drain Events Service

Implement:

```text
drain_events(recipient_agent_id, limit)
```

It should:

1. Load pending deliveries for recipient.
2. Order by severity and creation time, or strictly by creation time if simpler.
3. Limit to configured max.
4. Mark selected deliveries delivered.
5. Return event payloads and remaining count.

Decide whether delivered events are also acknowledged in v1. If not, add a later acknowledgement command.

## 5. Implement Event Prompt Formatter

The formatter should produce compact model-facing text.

Rules:

1. Include at most configured number of events.
2. Use one section per event.
3. Keep summaries short.
4. Include tool-call suggestions.
5. Include "More events remain" if applicable.
6. Never include huge terminal output.

For multiple events:

```text
PUPPETMASTER EVENTS

1. Agent agt_a completed.
...

2. Agent agt_b blocked.
...

2 more events remain queued. Call list_agents or inspect_agent if needed.
```

## 6. Implement Hook Drain Command

Add:

```text
puppet hook drain-events --agent-id <agent-id>
```

This command reads optional Stop hook JSON from stdin, records the orchestrator turn stop, drains events, and writes Codex Stop-hook JSON.

If no events:

1. Exit 0 with no output, or
2. Output `{"continue": true}` if required by validation.

If events exist:

```json
{
  "decision": "block",
  "reason": "<formatted event prompt>"
}
```

Validate which output Codex accepts for `Stop`.

## 7. Generate Orchestrator-Specific Stop Hook

The generated Stop hook should differ by role:

For subagents:

```text
puppet hook stop --agent-id <id>
```

For orchestrator:

```text
puppet hook drain-events --agent-id <id>
```

If the root orchestrator should also record normal turn stops, `drain-events` can call the same internal stop handler before draining.

## 8. Optional Tmux Injection

Implement only after hook continuation works.

Add a service:

```text
maybe_inject_event_prompt(root_agent_id)
```

It should be conservative. For v1, acceptable behavior is to leave it disabled by default and provide the internal API for later. If enabled:

1. Check root tmux session exists.
2. Check root status is idle.
3. Check no recent injection is pending.
4. Paste event prompt.

Do not make validation depend on this path.

## 9. Add Event Inspection

Update `inspect_agent` to show:

1. Recent produced events.
2. Pending deliveries addressed to the agent.
3. Pending deliveries produced by descendants if caller is root.

This helps debug queue behavior.

