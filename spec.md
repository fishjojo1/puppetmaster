# Puppetmaster Global Install Brief

## Purpose

Puppetmaster should work as one globally installed local supervisor instead of a tool that must be installed and configured separately inside every project checkout.

After global installation, a user should be able to run `puppet` from any working directory, start orchestrators for different project directories, and operate all of them through one shared Puppetmaster registry and one shared Discord bot.

## Reader And Action

This brief is for the engineer implementing the global install path. After reading it, they should be able to update the runtime, CLI, config, docs, and tests without needing the original design conversation.

## Desired User Experience

Install Puppetmaster once:

```bash
uv tool install /path/to/pupptermaster
```

Initialize global state:

```bash
puppet init
```

Start the Discord bot once:

```bash
puppet discord serve --background
```

Run orchestrators from anywhere:

```bash
puppet orchestrator start --cwd /home/kek/Projects/project-a --prompt "Manage project A."
puppet orchestrator start --cwd /home/kek/Projects/project-b --prompt "Manage project B."
```

Root orchestrators may also be created with exact safe ids:

```bash
puppet orchestrator start --agent-id project-a --cwd /home/kek/Projects/project-a --prompt "Manage project A."
```

Custom ids match `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`, must not contain `..`, and are rejected if the registry id, agent directory, or derived tmux session already exists.

The same global registry tracks both orchestrators. Discord channels can be bound to different root orchestrators, allowing one Discord bot to route work across multiple projects.

## Core Decisions

- The default Puppetmaster state directory is `~/.puppetmaster`.
- `PUPPETMASTER_STATE_DIR` remains the explicit override for isolated state.
- Project-local `.puppetmaster` directories are not migrated automatically.
- Existing project-local state migration is documented only.
- `puppet init` is the user-facing setup command.
- `puppet init` creates or updates the global config interactively.
- Discord bot token entry is hidden during interactive init.
- One global Discord bot process serves one global registry.
- Multiple root orchestrators in the global registry are normal.
- Discord channel bindings decide which root orchestrator receives a channel's prompts.
- The installed runtime must not assume it is being run from the Puppetmaster source checkout.

## Global State Layout

Default global state lives at:

```text
~/.puppetmaster/
  config.toml
  registry.sqlite
  puppetmaster.log.jsonl
  discord-bot.pid
  discord-bot.log
  agents/
```

The config file keeps the existing logical sections:

```toml
[limits]
max_depth = 3
max_concurrent_children_per_agent = 5
max_total_agents = 30
max_event_prompt_events = 5
max_wait_seconds = 3600
default_log_lines = 120
max_log_read_lines = 2000

[codex]
no_alt_screen = true
bypass_approvals_and_sandbox = true

[discord]
token = ""
guild_id = ""
poll_interval_seconds = 1
typing_timeout_seconds = 300
chunk_size = 1900
max_chunks = 3
```

## Initialization Command

`puppet init` creates `~/.puppetmaster`, ensures `config.toml` exists, and walks the user through Discord setup.

Interactive behavior:

```text
Puppetmaster home: /home/kek/.puppetmaster

Discord bot token [hidden, leave blank to keep existing]:
Discord guild ID [leave blank to keep existing]:
Start Discord bot in background now? [y/N]:
```

Non-interactive behavior should support automation:

```bash
puppet init --discord-token "$TOKEN" --discord-guild-id 123456789 --no-input
puppet init --discord-token "$TOKEN" --discord-guild-id 123456789 --start-discord
```

If config already exists, init preserves existing values unless the user supplies replacements.

## Runtime Model

The command execution model has three separate concepts:

- Puppetmaster install: the Python package and `puppet` executable.
- Puppetmaster state: the global registry, config, logs, and agent metadata.
- Agent workspace: the project directory passed as `--cwd`.

Helper processes must use the installed package or script entrypoint. They must not depend on a `src` directory under the current working directory.

Source-checkout development should still work when `PYTHONPATH=src` is set, but global install should not require that environment variable.

## Discord Model

One global Discord bot process uses the global config and global registry.

Expected workflow:

```bash
puppet discord serve --background
puppet discord status
puppet discord stop
```

Bindings remain per channel and per root orchestrator:

- A Discord channel can bind to one root orchestrator.
- A root orchestrator can have one active Discord channel binding.
- Different channels can bind to different root orchestrators.
- The outbound `send_human_message` tool resolves the caller's root and uses that root's binding.

This lets one Discord bot serve many project orchestrators without giving orchestrators direct access to arbitrary Discord channel ids.

## Safety Model

Puppetmaster remains a personal local tool, not a hosted multi-tenant service. The global install does not add per-user authorization.

Safety rails:

- The bot operates only in the configured guild.
- A channel must be explicitly bound before it controls an orchestrator.
- Discord prompts still require mention or reply behavior.
- The orchestrator cannot choose arbitrary outbound Discord destinations.
- `PUPPETMASTER_STATE_DIR` can isolate experiments or tests from the global registry.

This matters because managed Codex sessions can run with bypassed approvals and sandboxing. A bound Discord channel is a remote control surface for local code execution.

## Compatibility And Migration

Old project-local `.puppetmaster` directories are not migrated automatically.

Manual migration can be documented as:

1. Install Puppetmaster globally.
2. Run `puppet init`.
3. Copy any needed Discord config values from the old project-local config.
4. Restart the Discord bot from the global state.
5. Recreate or rebind active orchestrators as needed.

Automatic migration is intentionally out of scope because merging registries, tmux sessions, active process state, and Discord bindings can create ambiguous ownership.

## Milestones

- `013-global-state-foundation`: make global state the default and remove source-checkout assumptions from spawned helper processes.
- `014-init-command-onboarding`: add `puppet init` with interactive and non-interactive Discord config setup.
- `015-global-discord-operations`: document and validate one global Discord bot serving multiple orchestrators across many project directories.

## Non-Goals

- No hosted service.
- No per-user Discord authorization.
- No automatic migration of old project-local state.
- No support for multiple Discord guilds in one config.
- No support for multiple Discord bot tokens in one global state.
- No package publishing flow beyond local `uv tool install` or `pipx install` documentation.
