# Validation Plan

## Automated Tests

Custom root id CLI:

- `puppet orchestrator start --agent-id project-a` creates a root with `id == "project-a"`.
- The same root has `root_id == "project-a"`.
- Omitting `--agent-id` preserves generated `agt_...` behavior.
- Human output includes the custom id.
- `--json` output includes the custom id in the existing `agent.id` and `agent.root_id` fields.
- Child agent creation still uses generated ids.

Safe id validation:

- Valid ids include letters, digits, underscore, dash, and dot after the first character.
- Empty id is rejected.
- Id with `/` is rejected.
- Id with `..` path traversal is rejected.
- Id with whitespace is rejected.
- Id with shell metacharacters is rejected.
- Id beginning with `.` is rejected.
- Overlong id is rejected.
- Validation error explains the accepted format.

Duplicate and side-effect checks:

- Starting with an id already present in the registry fails clearly.
- Duplicate-id failure does not create a new registry row.
- Duplicate-id failure does not create or modify the target agent directory.
- Existing target agent directory without a registry row fails clearly.
- Existing derived tmux session without a registry row fails clearly.
- Directory and tmux duplicate failures happen before creating a registry row.

Path and session derivation:

- Custom id determines the agent state directory path.
- Initial prompt path uses the custom id directory.
- Terminal log path uses the custom id directory.
- Events path uses the custom id directory.
- Launch script path uses the custom id directory.
- Per-agent Codex config directory uses the custom id directory.
- Tmux session name is derived from the custom id and configured prefix.

Discord screenshot command:

- `/puppet screenshot` requires a guild text channel.
- `/puppet screenshot` requires a bound root orchestrator.
- Bound channel captures the bound root's tmux pane.
- Missing tmux session returns a clear Discord error.
- Successful command sends one PNG attachment.
- Screenshot command does not prompt the orchestrator.
- Screenshot command does not create inbound prompt events.

Tmux capture and rendering:

- Captured pane text preserves visible line order.
- Empty pane renders a valid PNG.
- Multiline pane renders a valid PNG.
- Long lines are clipped or wrapped deterministically.
- Renderer emits PNG bytes with the correct PNG signature.
- Renderer handles non-ASCII terminal text without crashing.

Regression:

- Existing orchestrator startup tests pass.
- Existing child agent creation tests pass.
- Existing Discord binding tests pass.
- Existing Discord outbound message tests pass.
- Release validation script passes if updated.

## Manual End-To-End Check

Start a root with a custom id:

```bash
state_dir="$(mktemp -d)"
PUPPETMASTER_STATE_DIR="$state_dir" puppet orchestrator start \
  --agent-id project-a \
  --cwd /home/kek/Projects/pupptermaster \
  --prompt "You are the project-a orchestrator."
```

Expected:

- Human output shows `project-a`.
- `PUPPETMASTER_STATE_DIR="$state_dir" puppet agent inspect project-a` works.
- Agent files are under the `project-a` agent directory.
- The tmux session name includes `project-a`.

Duplicate check:

```bash
PUPPETMASTER_STATE_DIR="$state_dir" puppet orchestrator start \
  --agent-id project-a \
  --cwd /home/kek/Projects/pupptermaster \
  --prompt "Duplicate."
```

Expected:

- Command fails with a duplicate-id error.
- No second root is created.

Start Discord and bind the custom id:

```bash
PUPPETMASTER_STATE_DIR="$state_dir" puppet init
PUPPETMASTER_STATE_DIR="$state_dir" puppet discord serve --background
```

In Discord:

```text
/puppet bind agent_id:project-a
/puppet screenshot
```

Expected:

- Bind succeeds using the custom id.
- Screenshot command posts a PNG attachment.
- The image shows the visible terminal text for the `project-a` tmux pane.
- ANSI color fidelity is not required.

Stop Discord:

```bash
PUPPETMASTER_STATE_DIR="$state_dir" puppet discord stop
```

## Acceptance Criteria

- Users can choose a safe root orchestrator id with `puppet orchestrator start --agent-id`.
- Duplicate or unsafe ids fail before partial agent creation.
- Registry rows, paths, and tmux sessions consistently use the final id.
- Generated id behavior remains unchanged when `--agent-id` is omitted.
- `/puppet screenshot` sends a PNG rendering of the bound root orchestrator's tmux pane.
- Screenshot capture is terminal-pane based and does not rely on GUI screenshot tooling.
