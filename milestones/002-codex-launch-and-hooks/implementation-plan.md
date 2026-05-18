# Implementation Plan

## 1. Add Codex Runtime Module

Create a Codex runtime service responsible for:

1. Locating Codex.
2. Checking version.
3. Building launch environment.
4. Writing generated config.
5. Writing hooks.
6. Writing launch wrapper.
7. Starting the tmux session through the supervisor core.

Do not make the registry or CLI know Codex command details directly.

## 2. Discover Codex

Implement:

```text
codex_runtime.discover()
```

It should:

1. Search `PATH` for `codex`.
2. Run `codex --version`.
3. Run `codex --help`.
4. Check that `--no-alt-screen` appears supported.
5. Check that `--dangerously-bypass-approvals-and-sandbox` appears supported.
6. Record the version string in `doctor` output.

If a user config later requests `--yolo`, check for it separately. Do not assume it exists.

## 3. Generate Managed Prompt

Given the caller's prompt, generate a managed prompt that includes:

1. The user's task.
2. The agent's description.
3. The agent id.
4. The parent id.
5. The cwd.
6. Instructions to call `complete_agent` when done, failed, or blocked.
7. Reminder that the human may attach to the session.

The generated prompt must be stored in `initial-prompt.md`.

Do not make the prompt excessively ceremonial. It should be explicit and operational.

## 4. Generate Hook Scripts

Generate executable hook scripts under the agent directory.

The Stop hook should:

1. Read stdin fully.
2. Write raw input to a temporary file or pass it to CLI stdin.
3. Execute:

```bash
puppet hook stop --agent-id "$PUPPETMASTER_AGENT_ID"
```

4. Forward the hook JSON through stdin.
5. Exit with the CLI exit code unless doing so would make Codex unusable.

The hook script should be short and deterministic. Business logic belongs in the CLI handler.

## 5. Implement Hook CLI Handler

Implement:

```text
puppet hook stop --agent-id <agent-id>
```

It should:

1. Read JSON from stdin.
2. Validate agent id exists.
3. Normalize known fields.
4. Store raw input if useful.
5. Append `agent.turn_stopped`.
6. Update `last_turn_stopped_at`.
7. If agent is not completed/failed/blocked/stopped/killed, mark it `idle`.
8. Return valid JSON or empty success depending on Codex expectations.

For a basic Stop hook that does not continue the turn, an empty successful response is acceptable if Codex accepts it. If Codex requires JSON, return:

```json
{
  "continue": true
}
```

Validate actual behavior with real Codex.

## 6. Generate Codex Config

Write a config file and hooks file in the agent config directory.

The config strategy must avoid corrupting user global config. Options:

1. Launch Codex with config overrides that point to generated hooks.
2. Set a per-agent `CODEX_HOME` if Codex supports it and if auth still works.
3. Use a generated project `.codex` layer if the cwd can contain one.

Prefer the least invasive option that works in live validation. If the config approach has limitations, document them immediately in the milestone notes.

## 7. Generate Launch Wrapper

Write `launch.sh` with:

1. Exported Puppetmaster env vars.
2. Codex command.
3. Safe prompt passing.
4. Optional debug logging.

The wrapper should run from the agent cwd but use absolute paths for files.

## 8. Add Codex Create CLI

Implement:

```text
puppet agent create-codex --cwd <cwd> --description <text> --prompt <text-or-file>
```

This command should:

1. Create an agent record.
2. Generate files.
3. Start tmux with the launch wrapper.
4. Start log capture.
5. Mark status running.
6. Print attach command.

Later this command becomes the implementation behind MCP `create_agent`.

## 9. Update Doctor

`puppet doctor` should now check:

1. Codex exists.
2. Codex version detected.
3. Required flags supported.
4. Hook feature not globally disabled if detectable.
5. Generated hook scripts are executable.

## 10. Handle Hook Trust

Run a live Codex session and check whether Codex blocks hooks pending trust review.

If trust review is required, implement a bootstrap instruction or command. The milestone is not complete until a fresh managed session can run the generated Stop hook in the expected environment.

