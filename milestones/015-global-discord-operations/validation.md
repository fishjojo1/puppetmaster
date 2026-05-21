# Validation Plan

## Automated Tests

Process management:

- `discord serve --background` writes PID file to active state dir.
- `discord serve --background` writes log file to active state dir.
- Duplicate start against the same state dir fails.
- Stale PID file does not block startup.
- `discord status --json` reports running, pid, pid file, and log path.
- `discord stop` removes the active PID file after process exit.

Multiple root registry:

- Two root orchestrators can be created with different cwd values.
- Listing agents shows both roots.
- Inspecting each root returns the correct cwd and root id.
- Child agents remain attached to the correct root tree.

Discord routing:

- Binding channel A to root A and channel B to root B stores distinct bindings.
- Rebinding a channel updates only the intended binding.
- Rebinding a root removes its previous channel binding.
- Inbound prompt for channel A is delivered to root A only.
- Inbound prompt for channel B is delivered to root B only.
- `send_human_message` from root A queues outbound Discord work for channel A only.
- `send_human_message` from root B queues outbound Discord work for channel B only.

Documentation checks:

- README mentions global state path.
- README mentions `puppet init`.
- README mentions `puppet discord serve --background`.
- README explains `PUPPETMASTER_STATE_DIR`.
- README includes old project-local migration note.
- README warns that Discord controls local Codex sessions.

Regression:

- Full test suite passes.
- Release validation script passes if updated.

## Manual End-To-End Check

Install globally from a checkout:

```bash
uv tool install /home/kek/Projects/pupptermaster
```

Initialize a temporary global-like state:

```bash
state_dir="$(mktemp -d)"
PUPPETMASTER_STATE_DIR="$state_dir" puppet init
```

Start two orchestrators:

```bash
PUPPETMASTER_STATE_DIR="$state_dir" puppet orchestrator start \
  --cwd /home/kek/Projects/project-a \
  --prompt "You are the project A orchestrator."

PUPPETMASTER_STATE_DIR="$state_dir" puppet orchestrator start \
  --cwd /home/kek/Projects/project-b \
  --prompt "You are the project B orchestrator."
```

Start Discord:

```bash
PUPPETMASTER_STATE_DIR="$state_dir" puppet discord serve --background
PUPPETMASTER_STATE_DIR="$state_dir" puppet discord status
```

In Discord:

- Bind channel A to root A.
- Bind channel B to root B.
- Mention the bot in channel A.
- Confirm only root A receives the prompt.
- Mention the bot in channel B.
- Confirm only root B receives the prompt.
- Confirm each root's `send_human_message` output appears in its own bound channel.

Stop Discord:

```bash
PUPPETMASTER_STATE_DIR="$state_dir" puppet discord stop
```

Expected:

- Status reports stopped.
- No duplicate bot process remains.

## Acceptance Criteria

- One global Discord bot can serve multiple orchestrators.
- Channel bindings route inbound and outbound messages correctly.
- Duplicate bot startup is prevented.
- Docs explain the global workflow and old local-state behavior clearly.
