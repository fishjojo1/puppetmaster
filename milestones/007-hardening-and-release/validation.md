# Validation Plan

## Unit And Integration Validation

Run all automated tests.

Required passing areas:

1. Registry.
2. Tmux supervisor adapter.
3. Codex launch generation.
4. Hook handlers.
5. MCP schemas and handlers.
6. Event queue.
7. Reconciliation.
8. CLI rendering.
9. Limit enforcement.

## Config Validation

Set low limits:

```toml
[limits]
max_depth = 1
max_children_per_agent = 1
max_total_agents = 2
```

Expected:

1. First child creation succeeds.
2. Second child creation fails.
3. Grandchild creation fails.
4. Error messages cite the configured limits.

## Full Manual Workflow Validation

Start orchestrator:

```bash
puppet orchestrator start \
  --cwd /home/kek/Projects/pupptermaster \
  --prompt "You are the root orchestrator. Create child agents only when asked."
```

Create two child agents from orchestrator or CLI.

Child A prompt:

```text
Report success using complete_agent with a one-sentence summary.
```

Child B prompt:

```text
Report blocked using complete_agent and ask for one missing instruction.
```

Expected:

1. Both child sessions start.
2. Child A status completed.
3. Child B status blocked.
4. Orchestrator receives events through Stop hook continuation.
5. Orchestrator can inspect Child B.
6. Prompting Child B again works.
7. Child B can complete successfully later.

## Human Operator Validation

Run:

```bash
puppet agent list
puppet agent tree
puppet agent inspect <child-id>
puppet agent read <child-id>
puppet agent attach <child-id>
```

Expected:

1. List and tree are readable.
2. Inspect explains why the agent exists.
3. Read shows terminal output.
4. Attach enters the correct tmux session.
5. Detach preserves session.

## Recovery Validation

Kill a child tmux session manually.

Run:

```bash
puppet reconcile
puppet agent inspect <child-id>
```

Expected:

1. Agent marked dead.
2. Logs still readable.
3. Root receives warning event.

## Documentation Validation

Cold-read README and SPEC.

A fresh reader should be able to answer:

1. What is Puppetmaster?
2. How do I start the orchestrator?
3. How does a subagent report done?
4. How does the orchestrator learn about completion?
5. How do I attach to an agent?
6. What are the safety risks?
7. What does v1 not support?

## Release Completion Criteria

V1 is release-ready when:

1. Full manual workflow passes.
2. Automated tests pass.
3. `doctor --deep` passes on the release machine.
4. README quickstart works.
5. Spec conformance checklist is complete.
6. Known limitations are documented.

