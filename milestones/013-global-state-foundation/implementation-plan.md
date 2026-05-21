# Implementation Plan

## Config Default

Update config loading so the default state directory is:

```text
~/.puppetmaster
```

Keep this override unchanged:

```bash
PUPPETMASTER_STATE_DIR=/tmp/isolated puppet doctor
```

Implementation notes:

- Expand `~` with the standard path expansion behavior.
- Resolve the state path before writing files.
- Continue creating the state directory and default `config.toml` on first load.
- Keep the existing config table structure.
- Keep existing environment overrides for limits and tmux prefix.

## Runtime Directory Semantics

Clarify the internal meaning of runtime paths:

- State directory: global registry, config, logs, pid files, agent metadata.
- Agent cwd: project workspace where the managed Codex session runs.
- Current shell cwd: where the user happened to invoke `puppet`.
- Package location: where Python imports Puppetmaster from.

Avoid using the current shell cwd as the Puppetmaster package root.

## Helper Process Spawning

Audit spawned helper commands:

- Scheduled wakeup helpers.
- Background Discord bot.
- Any other subprocess that invokes `puppetmaster.cli`.

Required behavior:

- The child process uses the same `PUPPETMASTER_STATE_DIR` as the parent.
- The child process does not require `cwd/src` to exist.
- The child process can run from an installed `puppet` command.
- Source-checkout development continues to work when running with `PYTHONPATH=src`.

Preferred command shape:

```bash
python -m puppetmaster.cli ...
```

If using `python -m`, use the same interpreter as the parent process.

## State Compatibility

Do not attempt to merge old project-local state.

Expected old-state behavior:

- A previous `./.puppetmaster` directory remains untouched.
- New commands without `PUPPETMASTER_STATE_DIR` use `~/.puppetmaster`.
- Users can temporarily access old state by running commands with `PUPPETMASTER_STATE_DIR=/path/to/project/.puppetmaster`.

## Documentation Hooks

Update documentation in this milestone only enough to explain:

- The new default global state path.
- The override environment variable.
- The fact that project-local state is not migrated automatically.

Full onboarding docs belong to Milestone 014.

## Implementation Order

1. Update config default state path.
2. Update helper process spawning to avoid source-checkout assumptions.
3. Update tests that assumed project-local defaults.
4. Add new tests for global default and env override behavior.
5. Add minimal docs for state path compatibility.
