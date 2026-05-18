# Validation Plan

## Automated Validation

### Registry Tests

Verify:

1. Creating an agent stores all required fields.
2. Fetching by id returns the same data.
3. Listing agents returns created agents.
4. Updating status changes `updated_at`.
5. Appending events preserves order.
6. Recent events can be listed by agent id.
7. Invalid statuses are rejected if using typed validation.

### Cwd Tests

Verify:

1. Absolute existing directory passes.
2. Relative path fails.
3. Missing path fails.
4. File path fails.
5. Error messages include the bad cwd.

### Tmux Unit Or Adapter Tests

If using a command adapter, test command construction without running tmux:

1. Session names are escaped or validated.
2. Cwd is passed through tmux `-c`.
3. Kill command targets the expected session.
4. Capture command includes line limit.

## Live Smoke Validation

Run:

```bash
puppet doctor
```

Expected:

1. tmux detected.
2. State directory writable.
3. Registry initialized.

Create a raw agent:

```bash
puppet agent create-raw \
  --cwd /home/kek/Projects/pupptermaster \
  --description "raw smoke test" \
  --command "bash -lc 'echo hello from puppet; sleep 30'"
```

Expected:

1. Command returns an agent id.
2. `tmux ls` shows the session.
3. Agent appears in `puppet agent list`.
4. `puppet agent read <id>` shows `hello from puppet`.
5. `puppet agent inspect <id>` shows cwd, status, tmux session, and log path.

Stop the agent:

```bash
puppet agent stop <id>
```

Expected:

1. Status becomes stopped or dead with intentional stop reason.
2. Logs remain readable.
3. Metadata remains present.

Kill validation:

```bash
puppet agent create-raw --cwd <repo> --description "kill test" --command "sleep 300"
puppet agent kill <id>
```

Expected:

1. tmux session is gone.
2. Status becomes killed.
3. Inspect still works.

## Failure Validation

Try invalid cwd:

```bash
puppet agent create-raw --cwd relative/path --description bad --command "echo bad"
```

Expected:

1. Non-zero exit.
2. Clear error.
3. No partial live tmux session.

Try invalid command:

```bash
puppet agent create-raw --cwd <repo> --description bad --command "definitely-not-a-command"
```

Expected:

1. Agent record exists.
2. Status becomes failed or dead after process exit.
3. Logs show failure.

## Completion Criteria

This milestone is complete when:

1. A raw tmux-backed agent can be created, listed, inspected, read, stopped, and killed.
2. Logs survive tmux session exit.
3. Registry state remains coherent after failures.
4. `doctor` gives actionable output.

