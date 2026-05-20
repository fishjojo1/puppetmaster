# Milestone 010: Discord Bot Commands

## Goal

Add the Discord bot process and its slash-command operator surface.

## Decisions Captured

- `discord.py` is a default project dependency.
- The bot runs as `puppet discord serve`.
- Guild-scoped slash commands auto-sync on startup.
- Bindings are whole text channels only.
- `/puppet bind` accepts root orchestrators only.
- Slash command output is plain text/code-block formatted.
