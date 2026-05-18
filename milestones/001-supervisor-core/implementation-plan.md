# Implementation Plan

## 1. Choose Initial Runtime Structure

Pick a project structure that supports three future entry points:

1. CLI.
2. MCP server.
3. Hook command handler.

Even if only the CLI exists in this milestone, the internal modules should already separate concerns:

```text
config
registry
agent_model
tmux
logs
cli
errors
```

Avoid placing tmux command construction directly inside CLI handlers. CLI handlers should call service functions that later MCP handlers can reuse.

## 2. Implement Configuration

Create a configuration object with defaults:

```text
state_dir = <repo or user configured>/.puppetmaster
tmux_session_prefix = puppet_
default_log_lines = 120
max_log_read_lines = 2000
```

The configuration should be overridable through environment variables and later through a config file. For milestone 001, environment variables are enough:

```text
PUPPETMASTER_STATE_DIR
PUPPETMASTER_TMUX_PREFIX
```

## 3. Implement State Directory Creation

On first command invocation, create:

```text
.puppetmaster/
.puppetmaster/agents/
```

If using SQLite, initialize:

```text
.puppetmaster/registry.sqlite
```

If using JSON-first storage initially, keep the API shaped so SQLite can replace it without changing callers.

## 4. Implement Registry Schema

Recommended SQLite tables:

```text
agents(
  id text primary key,
  parent_id text null,
  root_id text not null,
  role text not null,
  name text null,
  description text not null,
  initial_prompt_path text null,
  cwd text not null,
  tmux_session text not null,
  codex_session_id text null,
  status text not null,
  completion_status text null,
  depth integer not null,
  created_at text not null,
  updated_at text not null,
  started_at text null,
  last_turn_stopped_at text null,
  completed_at text null,
  stopped_at text null,
  exit_code integer null,
  termination_reason text null,
  log_path text not null,
  metadata_json text not null
)

events(
  id text primary key,
  agent_id text not null,
  parent_id text null,
  root_id text not null,
  type text not null,
  severity text not null,
  status text not null,
  created_at text not null,
  delivered_at text null,
  acknowledged_at text null,
  summary text not null,
  payload_json text not null,
  source text not null
)
```

The registry module should expose typed operations rather than letting callers write SQL.

## 5. Implement Agent Ids

Use a stable unique id format:

```text
agt_<short-random-or-ulid>
```

Ids must be safe for filenames and tmux session names.

## 6. Implement Cwd Validation

Before creating any agent:

1. Reject missing cwd.
2. Reject relative cwd.
3. Reject nonexistent cwd.
4. Reject non-directory cwd.
5. Store canonical absolute path if possible.

Return errors that are useful to agents and humans.

## 7. Implement Tmux Supervisor

Required functions:

```text
create_session(agent, command)
session_exists(tmux_session)
capture_pane(tmux_session, lines)
send_prompt(tmux_session, text)
stop_session(tmux_session)
kill_session(tmux_session)
list_puppet_sessions()
```

For this milestone, `send_prompt` may be implemented but does not need heavy validation. Later milestones will harden multiline prompt delivery.

When launching, use tmux's `-c` option to set cwd. Do not rely on `cd` inside shell strings.

## 8. Implement Log Capture

Use `tmux pipe-pane` to append output to the agent log:

```text
tmux pipe-pane -t <session> -o 'cat >> <log-path>'
```

The implementation must handle paths safely. If shell quoting is too risky, create a small wrapper script or use a runtime command API that handles arguments where possible.

Also provide a fallback:

1. If pipe-pane setup fails, record an event.
2. `read_agent` can still use `tmux capture-pane` for live sessions.

## 9. Implement CLI

Implement commands in thin layers:

```text
cli -> services -> registry/tmux
```

`agent create-raw` should:

1. Validate cwd.
2. Create agent metadata.
3. Write any provided prompt/description files if needed.
4. Create tmux session with provided command.
5. Start log capture.
6. Mark status running.
7. Print JSON or human output.

`agent inspect` should aggregate registry data, recent events, tmux existence, and recent output.

## 10. Implement Doctor

`puppet doctor` should check:

1. State directory writable.
2. tmux executable exists.
3. tmux version command succeeds.
4. Registry can be opened.
5. A temporary tmux command can be created and killed, if the user permits smoke checks by flag.

## 11. Error Handling

All command failures should produce structured errors internally. CLI can render human-readable errors by default and JSON errors with `--json`.

Errors should include:

1. Code.
2. Message.
3. Recoverability hint.
4. Underlying command stderr when relevant.

## 12. Migration Notes

If SQLite is used, include a simple schema version table from the beginning:

```text
schema_version(version integer, applied_at text)
```

Even if v1 has only one migration, this prevents a messy retrofit later.

