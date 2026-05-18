# Implementation Plan

## 1. Add Config File

If not already implemented, add a config file:

```text
.puppetmaster/config.toml
```

or user-level:

```text
~/.config/puppetmaster/config.toml
```

Recommended v1 local config:

```toml
[limits]
max_depth = 3
max_children_per_agent = 5
max_total_agents = 30
max_event_prompt_events = 5
default_log_lines = 120
max_log_read_lines = 2000

[codex]
no_alt_screen = true
bypass_approvals_and_sandbox = true
```

Environment variables may override config where useful.

## 2. Enforce Limits Everywhere

Limits must be enforced in the service layer, not only CLI or MCP handlers.

Check:

1. MCP `create_agent`.
2. CLI `agent create`.
3. Any debug raw create command.
4. Any future internal create path.

Tests should prove the service rejects limit violations regardless of caller.

## 3. Finalize Command Names

Remove or hide temporary names from normal help:

1. `create-codex` should become `agent create`.
2. `create-raw` should move to `debug create-raw` if retained.
3. Hook commands should remain under `hook`.
4. MCP command should remain under `mcp serve`.

## 4. Write README

The README should be practical:

1. What Puppetmaster is.
2. What problem it solves.
3. Safety warning.
4. Prerequisites.
5. Quickstart commands.
6. Common operations.
7. Link to spec and milestones.

Keep README shorter than the spec. It should get a user started, not replace detailed docs.

## 5. Write Troubleshooting

Add troubleshooting either in README or a separate doc:

1. Codex not found.
2. tmux not found.
3. Hook did not run.
4. Agent is dead.
5. Orchestrator did not receive event.
6. Prompt injection looked wrong.
7. Logs missing.
8. Registry mismatch.

Each entry should include a command to run.

## 6. Build Release Validation Script

Create a script or documented command sequence that runs:

1. Unit tests.
2. Formatting/linting.
3. Doctor.
4. Tmux smoke test.
5. Optional live Codex smoke test.

Live Codex smoke tests may require credentials and should be skippable.

## 7. Spec Conformance Checklist

Create a checklist matching key spec requirements:

1. Orchestrator wrapped.
2. Cwd required.
3. Codex no sandbox/no approvals.
4. Hooks installed.
5. MCP tools available.
6. Completion explicit.
7. Events queued.
8. Orchestrator Stop hook drains events.
9. Logs durable.
10. Human attach works.

## 8. Packaging

Depending on implementation language, provide:

1. Install command.
2. Development command.
3. Test command.
4. Uninstall or cleanup command.

The installed executable should be named consistently. The repo and conversation use both "Puppetmaster" and "Puppetmaster"; choose one command name and document it. Recommended command:

```text
puppet
```

because it is short for local use. If that conflicts with existing Puppet tooling on the machine, use:

```text
puppetmaster
```

The final decision should be made before release.

## 9. Final End-To-End Pass

Run the full workflow from the milestone description. Record:

1. Commands run.
2. Agent ids.
3. Evidence that hooks fired.
4. Evidence that orchestrator received events.
5. Evidence that logs survived stop/kill.

Store this evidence in release notes or validation output.

## 10. Known Limitations

Document honestly:

1. V1 is local only.
2. V1 is Codex only.
3. V1 intentionally runs Codex without sandboxing.
4. V1 does not create worktrees.
5. V1 does not guarantee perfect idle detection.
6. V1 uses hooks and tmux; both must work on the host.

