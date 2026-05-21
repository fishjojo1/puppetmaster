# Validation Plan

## Automated Tests

Default state tests:

- With no `PUPPETMASTER_STATE_DIR`, config uses `~/.puppetmaster`.
- The global state directory is created on first config load.
- The default config file is created in the global state directory.
- Running from two different current working directories resolves the same default state path.

Override tests:

- `PUPPETMASTER_STATE_DIR` still overrides the global default.
- Override paths are expanded and resolved.
- Existing tests using temporary state directories remain isolated.

Helper process tests:

- Scheduled wakeup helper receives the parent state dir in its environment.
- Scheduled wakeup helper does not construct `PYTHONPATH` from the user command cwd.
- Background Discord spawn receives the parent state dir in its environment.
- Background Discord spawn does not require a local `src` directory.

Regression tests:

- Existing core test suite passes.
- Existing Discord tests pass.
- Existing CLI commands still create and read registry state.

## Manual Checks

Fresh global state:

```bash
rm -rf ~/.puppetmaster-test
PUPPETMASTER_STATE_DIR=~/.puppetmaster-test puppet doctor
```

Expected:

- State directory is created.
- Config file is created.
- Doctor reports local dependency status.

Cross-directory default behavior:

```bash
cd /tmp
puppet doctor

cd /home/kek/Projects/pupptermaster
puppet doctor
```

Expected:

- Both commands use the same default global state.
- Neither command creates a new project-local `.puppetmaster`.

Source checkout behavior:

```bash
PYTHONPATH=src python -m puppetmaster.cli doctor
```

Expected:

- Source checkout development still works.

## Acceptance Criteria

- Global state is the default.
- `PUPPETMASTER_STATE_DIR` remains a reliable isolation mechanism.
- No command requires Puppetmaster to be installed inside the target project.
- Existing project-local state is left untouched.
