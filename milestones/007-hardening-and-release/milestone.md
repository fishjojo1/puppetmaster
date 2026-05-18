# Milestone 007: Hardening And Release

## Objective

Prepare Puppetmaster v1 for real daily use. This milestone tightens limits, cleans up documentation, verifies complete workflows, packages the tool, and ensures the implementation matches the specification.

## Reader

This milestone is for the engineer doing the final v1 pass. After reading it, they should be able to harden the system, run full end-to-end validation, and produce a release-ready local tool.

## Scope

This milestone includes:

1. Limit enforcement.
2. Configuration file support if not already present.
3. Final command naming.
4. Full workflow documentation.
5. Packaging/install instructions.
6. End-to-end tests.
7. Regression tests for core services.
8. Spec conformance review.
9. Known limitations.
10. Release checklist.

This milestone excludes:

1. New major product features.
2. Web dashboard.
3. Non-Codex runtimes.
4. Automatic worktree management.

## Required Limits

Implement and document defaults:

```text
max_depth = 3
max_children_per_agent = 5
max_total_agents = 30
max_event_prompt_events = 5
default_log_lines = 120
max_log_read_lines = 2000
```

Limits should be configurable, but safe defaults must exist.

Limit errors should be explicit:

```text
Cannot create child agent: max_depth=3 would be exceeded by parent agt_123.
```

## Required Documentation

Release docs should include:

1. README with project purpose.
2. Installation/setup.
3. Quickstart.
4. Starting an orchestrator.
5. Creating agents.
6. Attaching to sessions.
7. How completion and events work.
8. How hooks work.
9. Safety warning about no-sandbox Codex mode.
10. Troubleshooting.
11. Known limitations.

The README should link to `SPEC.md` and `milestones/`.

## Full Workflow

The release candidate must support this workflow:

1. Human starts orchestrator with cwd and prompt.
2. Orchestrator creates two child agents with explicit cwd.
3. Child A completes successfully.
4. Child B reports blocked.
5. Orchestrator receives both events without blocking on wait.
6. Orchestrator inspects Child B.
7. Orchestrator prompts Child B with more instructions.
8. Child B completes.
9. Human lists all agents.
10. Human attaches to a session.
11. Human reads logs after stopping a session.

## Deliverables

1. Configurable limits.
2. Final docs.
3. Release validation script or documented manual script.
4. Test suite passing.
5. Spec conformance checklist.
6. Installation instructions.
7. Known limitations document.

