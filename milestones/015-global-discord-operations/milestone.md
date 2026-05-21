# Milestone 015: Global Discord Operations

## Goal

Validate and document the operational model where one global Discord bot serves multiple root orchestrators across multiple project directories.

## User Value

A user can keep one Discord bot running and use it to control many Puppetmaster orchestrators without starting a separate bot or local state directory for each project.

## Scope

- Confirm global Discord process management uses global state by default.
- Ensure duplicate background bot protection works with global state.
- Validate multiple root orchestrators can coexist in one registry.
- Validate channel bindings route Discord messages to the correct root orchestrator.
- Document global install, init, Discord startup, multi-project usage, and old local-state migration notes.

## Decisions Captured

- One global bot process is expected.
- One configured guild is supported.
- Multiple root orchestrators in the same registry are expected.
- Discord channel bindings are the routing layer.
- Old project-local state migration is documented, not automated.

## Non-Goals

- No multiple-guild routing.
- No multiple bot token support.
- No per-user Discord authorization.
- No automatic registry merge.
- No hosted daemon supervisor beyond the existing background process support.
