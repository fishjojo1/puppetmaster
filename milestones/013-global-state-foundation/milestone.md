# Milestone 013: Global State Foundation

## Goal

Make `~/.puppetmaster` the default Puppetmaster state directory and separate installed runtime concerns from agent workspace concerns.

## User Value

A user can install Puppetmaster once and run `puppet` from any project directory while all commands share the same default registry, config, logs, Discord process metadata, and agent records.

## Scope

- Change the default state directory to `~/.puppetmaster`.
- Keep `PUPPETMASTER_STATE_DIR` as an override.
- Preserve existing config schema and default values.
- Ensure helper processes do not assume the current working directory is the Puppetmaster source checkout.
- Preserve source-checkout development behavior.
- Add tests proving commands from different directories use the same global state by default.

## Decisions Captured

- Global state is the default for new runs.
- Project-local state is not automatically migrated.
- The installed package is separate from the target project workspace.
- Agent `--cwd` remains the source of truth for the agent's working directory.
- Spawned helper commands should invoke Puppetmaster through the installed module or executable, not through a guessed local `src` path.

## Non-Goals

- No interactive setup command in this milestone.
- No Discord token prompting in this milestone.
- No automatic migration from project-local `.puppetmaster`.
- No package manager installer script.
