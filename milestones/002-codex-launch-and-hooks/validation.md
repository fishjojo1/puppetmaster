# Validation Plan

## Automated Validation

### Codex Discovery Tests

With a fake command adapter:

1. Codex found returns path and version.
2. Missing Codex returns actionable error.
3. Missing required flag returns actionable error.
4. Version output is parsed but not over-constrained.

### Prompt Generation Tests

Verify generated prompt includes:

1. Original task.
2. Description.
3. Agent id.
4. Parent id if present.
5. Cwd.
6. Instruction to call `complete_agent`.

### Hook Handler Tests

Feed representative Stop hook JSON into:

```bash
puppet hook stop --agent-id <id>
```

Verify:

1. Event is recorded.
2. Agent `last_turn_stopped_at` updates.
3. Running agent becomes idle.
4. Completed agent remains completed.
5. Invalid JSON produces hook.error.

## Live Codex Validation

Create a managed Codex agent with a trivial prompt:

```bash
puppet agent create-codex \
  --cwd /home/kek/Projects/pupptermaster \
  --description "hook smoke test" \
  --prompt "Reply with one short sentence, then stop."
```

Expected:

1. tmux session starts.
2. Codex launches with no alt screen.
3. Terminal log captures Codex output.
4. Stop hook runs when the turn stops.
5. `inspect` shows `last_turn_stopped_at`.
6. Events include `agent.turn_stopped`.

Inspect generated files:

```bash
puppet agent inspect <id>
```

Expected:

1. Config directory path exists.
2. Hook script path exists.
3. Initial prompt path exists.
4. Attach command works.

## Hook Trust Validation

Start a fresh managed Codex session in a clean test cwd.

Expected:

1. Codex does not silently skip the hook.
2. If Codex requires hook trust review, `doctor` reports the exact remediation.
3. After remediation, hook events are recorded.

## Failure Validation

Temporarily point Codex path to a missing binary.

Expected:

1. `doctor` fails clearly.
2. `create-codex` fails before creating a live tmux session.
3. If an agent record is created, it is marked failed with useful reason.

Temporarily make hook script exit non-zero.

Expected:

1. Hook failure is recorded.
2. Agent remains inspectable.
3. Logs include enough detail to debug.

## Completion Criteria

This milestone is complete when:

1. Puppetmaster can launch real Codex in tmux with required flags.
2. Generated Stop hooks execute and record events.
3. Generated config does not mutate user global config.
4. `doctor` detects Codex and hook readiness.

