# Implementation Plan

## Global Discord Process Management

Ensure these commands operate against the default global state:

```bash
puppet discord serve --background
puppet discord status
puppet discord stop
```

Required behavior:

- PID file lives in the active state directory.
- Log file lives in the active state directory.
- Starting a second background bot against the same state directory fails clearly.
- Stale PID files are cleaned up or reported clearly.
- `PUPPETMASTER_STATE_DIR` can still run an isolated bot for tests or experiments.

## Multiple Orchestrator Workflow

Support this as the normal workflow:

```bash
puppet orchestrator start --cwd /project/a --prompt "Manage project A."
puppet orchestrator start --cwd /project/b --prompt "Manage project B."
```

Expected:

- Both roots are stored in the same registry.
- Root ids remain distinct.
- Agent logs remain under the shared state directory.
- Each root keeps its own agent tree.
- Commands like list, inspect, tree, read, and reconcile continue to work.

## Discord Routing

Confirm existing binding rules are sufficient for global operation:

- `/puppet agents` lists root orchestrators.
- `/puppet bind` binds the current channel to one root.
- A root can have one active channel binding.
- A channel can have one active root binding.
- Mention or reply prompts in a bound channel go only to that channel's root.
- `send_human_message` from an orchestrator replies only to that root's bound channel.

If gaps appear, update registry or bot behavior only as needed to preserve these invariants.

## Documentation

Update user-facing docs with:

- Global install command using `uv tool install`.
- Alternative install using `pipx install`.
- `puppet init` setup flow.
- Where global state lives.
- How to start and stop the global Discord bot.
- How to start orchestrators for multiple projects.
- How Discord channel binding routes messages.
- How to use `PUPPETMASTER_STATE_DIR` for isolated state.
- Old project-local `.puppetmaster` migration notes.

Migration note should say:

- Project-local state remains untouched.
- New default commands use global state.
- Users can temporarily access old local state with `PUPPETMASTER_STATE_DIR`.
- Automatic migration is not provided.

## Release Validation Script

Consider extending release validation with non-network checks:

- Global default config can load.
- `puppet init --no-input --json` works against a temp state dir.
- Discord background status works against a temp state dir.
- Registry can hold two root orchestrators with different cwd values.

Do not run a live Discord bot in automated release validation because it requires a real token and network access.

## Implementation Order

1. Add or adjust process-management tests for global state.
2. Add multi-root registry tests if gaps exist.
3. Add Discord routing tests focused on bindings and outbound queue behavior.
4. Update README and any conformance docs.
5. Add release validation checks where practical.
6. Run full automated suite.
