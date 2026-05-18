# Validation Plan

## CLI Rendering Tests

Verify:

1. `agent list` renders columns for multiple statuses.
2. `agent tree` renders nesting.
3. `agent inspect --json` returns parseable JSON.
4. Human inspect includes attach command.
5. Dead agent inspect includes log path.

## Orchestrator Start Validation

Run:

```bash
puppet orchestrator start \
  --cwd /home/kek/Projects/pupptermaster \
  --prompt "You are the Puppetmaster orchestrator. Wait for instructions."
```

Expected:

1. Root agent created.
2. Role is orchestrator.
3. tmux session exists.
4. Attach command works.
5. Stop hook is orchestrator drain hook.

## Attach Validation

Run:

```bash
puppet agent attach <orchestrator-id>
```

Expected:

1. tmux attaches to the correct session.
2. Human can detach without killing session.
3. `agent inspect` still works after detach.

## Prompt Validation

Send a multiline prompt:

```bash
puppet agent prompt <id> --prompt-file prompt.md
```

Expected:

1. Prompt arrives intact.
2. Agent responds.
3. `agent.prompted` event recorded.

## JSON Validation

For each core read command:

```bash
puppet agent list --json
puppet agent inspect <id> --json
puppet events list --json
```

Expected:

1. Output parses as JSON.
2. No human decoration appears in JSON mode.
3. Errors also use structured JSON when requested.

## Failure Validation

Attach to missing session:

1. Kill tmux session manually.
2. Run `puppet agent attach <id>`.

Expected:

1. Command fails clearly.
2. It suggests `agent read`.
3. It does not delete metadata.

Prompt dead session:

Expected:

1. Command fails with invalid state.
2. No prompt event is recorded as delivered.

## Completion Criteria

This milestone is complete when:

1. A human can operate Puppetmaster without MCP.
2. Every managed session has an easy attach path.
3. Inspect/read/list are useful for recovery.
4. CLI and MCP share the same underlying behavior.

