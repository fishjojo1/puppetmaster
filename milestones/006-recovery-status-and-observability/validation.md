# Validation Plan

## Automated Validation

### Reconciliation Rule Tests

Verify:

1. Running + missing tmux becomes dead.
2. Killed + missing tmux remains killed.
3. Completed + live tmux remains completed with note.
4. Running + newer Stop hook becomes idle.
5. Conflicting evidence records inconsistency.

### Structured Log Tests

Verify:

1. JSONL lines parse.
2. Required fields exist.
3. Full prompts are not logged by default.
4. Error records include error code and message.

### Doctor Tests

With fake dependencies:

1. Missing tmux fails.
2. Missing Codex fails.
3. Unwritable state dir fails.
4. Bad registry schema fails.
5. Non-executable hook warns or fails.

## Live Failure Validation

Create a long-running agent:

```bash
puppet agent create --cwd <repo> --description "death test" --prompt "Wait for instructions."
```

Kill its tmux session manually:

```bash
tmux kill-session -t <session>
```

Run:

```bash
puppet reconcile
```

Expected:

1. Agent marked dead.
2. Event recorded.
3. Parent/root receives warning delivery.
4. Inspect shows missing tmux evidence.

## Hook Failure Validation

Modify a generated hook in a test agent to exit non-zero.

Expected:

1. Hook failure event recorded.
2. `doctor --deep` catches failed or non-executable hook state if applicable.
3. Agent remains inspectable.

## Human Override Validation

Run:

```bash
puppet agent mark-status <id> --status blocked --reason "Human found it waiting on product decision."
```

Expected:

1. Status changes.
2. Human override event recorded.
3. Reason visible in inspect.

## Deep Doctor Validation

Run:

```bash
puppet doctor --deep
```

Expected:

1. Reports dependency status.
2. Reports registry/tmux consistency.
3. Reports hook readiness.
4. Exits non-zero if critical checks fail.

## Completion Criteria

This milestone is complete when:

1. Puppetmaster can detect dead sessions.
2. Inconsistent state is visible and actionable.
3. Hook failures are recorded.
4. `doctor --deep` gives a useful operational picture.
5. Humans can override status with an audit trail.

