# Milestone 005: Human CLI And Attach Workflow

## Objective

Make Puppetmaster comfortable for a human operator. The human must be able to start the orchestrator, list all agents, attach to any session, read logs, inspect purpose and state, prompt an agent manually, and intervene when orchestration gets confused.

## Reader

This milestone is for the engineer polishing the CLI and operator workflow. After reading it, they should be able to make Puppetmaster usable from a terminal without relying on MCP or internal implementation knowledge.

## Scope

This milestone includes:

1. Stable human CLI command names.
2. Orchestrator start command.
3. Agent attach workflow.
4. Log reading ergonomics.
5. Inspection output.
6. Manual prompting.
7. Manual completion marking.
8. Tree/list views.
9. JSON output for automation.
10. Help text.

This milestone excludes:

1. Web dashboard.
2. Terminal UI beyond tmux attach.
3. Remote multi-user access.

## Required CLI Commands

```text
puppet orchestrator start --cwd <cwd> --prompt <prompt-or-file>
puppet orchestrator inspect
puppet agent create --cwd <cwd> --description <text> --prompt <text-or-file>
puppet agent list
puppet agent tree
puppet agent inspect <agent-id>
puppet agent read <agent-id> --lines <n>
puppet agent prompt <agent-id> --prompt <text-or-file>
puppet agent attach <agent-id>
puppet agent stop <agent-id>
puppet agent kill <agent-id>
puppet agent complete <agent-id> --status <status> --summary <text>
puppet events list
puppet events pending <agent-id>
puppet doctor
```

Temporary milestone commands such as `create-raw` may remain under a debug namespace, but the main CLI should use production naming.

## Human Output Principles

Default output should be readable and compact.

For machine use, every command that returns data should support:

```text
--json
```

Human `inspect` should include:

1. Agent id.
2. Name and description.
3. Role.
4. Status.
5. Cwd.
6. Parent.
7. Children.
8. Tmux session.
9. Attach command.
10. Log path.
11. Initial prompt path.
12. Recent events.
13. Recent terminal output.

`list` should show enough information to identify agents quickly:

```text
ID        STATUS      ROLE          NAME        CWD        LAST EVENT
agt_123   running     orchestrator  root        /repo      child completed
agt_456   blocked     subagent      auth-audit  /repo      blocked
```

## Attach Workflow

`puppet agent attach <agent-id>` should either:

1. Execute `tmux attach -t <session>`, or
2. Print the exact command and require an explicit flag to execute.

Recommended default: execute attach for human CLI, print command in JSON mode.

If the tmux session no longer exists, show:

1. Last known status.
2. Log path.
3. Suggested read command.

## Manual Prompting

Humans should be able to send prompts without attaching:

```bash
puppet agent prompt agt_123 --prompt "Please run the tests now."
```

And from file:

```bash
puppet agent prompt agt_123 --prompt-file prompt.md
```

Prompt delivery must preserve multiline text.

## Deliverables

1. Stable CLI command structure.
2. Orchestrator start command.
3. Human-readable list/tree/inspect/read.
4. Attach command.
5. Prompt command.
6. Events commands.
7. JSON output mode.
8. Updated command help.

