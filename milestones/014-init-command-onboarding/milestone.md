# Milestone 014: Init Command Onboarding

## Goal

Add `puppet init` as the global setup command for creating and updating `~/.puppetmaster/config.toml`, including interactive Discord bot token and guild setup.

## User Value

A user can install Puppetmaster globally, run one setup command, and get a usable global config without manually creating TOML files.

## Scope

- Add `puppet init`.
- Prompt for Discord bot token using hidden terminal input.
- Prompt for Discord guild id.
- Preserve existing config values when the user leaves prompts blank.
- Add non-interactive flags for automation.
- Optionally start the Discord bot after setup.
- Print clear next steps.

## Decisions Captured

- The command is `puppet init`, not `puppet init-global`.
- Init targets the global state directory by default.
- Existing config values are preserved unless replaced.
- Token prompts must not echo the token.
- Non-interactive setup is required for scripted install flows.

## Non-Goals

- No package manager installation inside `puppet init`.
- No Discord API validation of the token during init.
- No automatic migration from project-local state.
- No support for multiple bot tokens or guilds.
