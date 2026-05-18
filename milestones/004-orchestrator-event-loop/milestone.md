# Milestone 004: Orchestrator Event Loop

## Objective

Implement the event-driven coordination model. Subagents should be able to finish, fail, block, or stop a turn, and Puppetmaster should queue those events for the orchestrator. The orchestrator should receive pending events through its own Codex `Stop` hook continuation, avoiding long blocking waits.

## Reader

This milestone is for the engineer implementing queue semantics and orchestrator wakeup. After reading it, they should be able to build the mechanism that tells the root Codex session, "Agent X is done," without requiring the root to sit inside a blocking tool call.

## Scope

This milestone includes:

1. Event addressing.
2. Pending event queue.
3. Event delivery state.
4. Orchestrator Stop hook drain behavior.
5. Event prompt formatting.
6. Explicit completion event flow.
7. Fallback turn-stopped event flow.
8. Optional idle tmux injection behind a conservative gate.

This milestone excludes:

1. Perfect terminal-based idle detection.
2. Browser dashboard notifications.
3. Distributed pub/sub.
4. Long-term event analytics.

## Required Behavior

When a subagent calls `complete_agent`, Puppetmaster must:

1. Update the subagent status.
2. Record a completion event.
3. Queue an event for the parent agent.
4. Queue an event for the root orchestrator if different from the parent.
5. Make that event visible through `inspect_agent` and `list_agents`.

When a subagent's Stop hook fires without explicit completion, Puppetmaster must:

1. Record `agent.turn_stopped`.
2. Mark the agent idle or awaiting input if appropriate.
3. Queue a lower-priority event if the parent should know the agent is awaiting instructions.

When the orchestrator Stop hook fires, Puppetmaster must:

1. Drain a bounded number of pending events addressed to the orchestrator.
2. Format a concise continuation prompt.
3. Return Codex Stop-hook JSON that continues the orchestrator turn.
4. Mark drained events delivered.

## Event Priority

V1 should distinguish high-signal events from low-signal events.

High-signal events:

```text
agent.completed
agent.failed
agent.blocked
agent.process_exited unexpectedly
```

Low-signal events:

```text
agent.turn_stopped
agent.awaiting_input
agent.prompted
```

The orchestrator should always receive high-signal events. Low-signal events should be delivered only when useful and not spammy. For example, an agent turning idle after its first response may be worth reporting, but repeated idle stops without new state should be coalesced.

## Event Prompt Requirements

Event prompts must:

1. Start with `PUPPETMASTER EVENT`.
2. Name the agent id.
3. Include status.
4. Include cwd.
5. Include summary.
6. Include available actions.
7. Avoid dumping long logs.
8. Mention if more events remain queued.

Example:

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

## Deliverables

1. Addressed event queue.
2. `hook drain-events` command.
3. Orchestrator Stop hook continuation.
4. Event prompt formatter.
5. Event coalescing for repeated low-signal turn stops.
6. End-to-end validation with root orchestrator and child agent.

