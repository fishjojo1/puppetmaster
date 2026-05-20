# Milestone 012: Discord Hardening And Docs

## Goal

Make the Discord integration reliable enough for personal daily use and document the operational model.

## Decisions Captured

- No full authorization model for v1.
- Guild scoping is still required as a cheap safety rail.
- `.puppetmaster/config.toml` contains the Discord token and remains ignored local state.
- The integration is personal and local-first, not a hosted multi-tenant service.
