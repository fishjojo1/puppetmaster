# Milestone 008: Discord Config And State

## Goal

Prepare Puppetmaster for a Discord transport by adding the configuration and durable state needed for channel bindings and outbound human messages.

## Decisions Captured

- Discord integration targets one explicit Puppetmaster state directory.
- Discord token and guild id live in `.puppetmaster/config.toml` for v1.
- Discord channel bindings are persisted in SQLite.
- Each root orchestrator may have one active Discord channel binding.
- Outbound human messages use a dedicated queue table instead of overloading agent event deliveries.
- `.puppetmaster/` remains local state and must not be committed.
