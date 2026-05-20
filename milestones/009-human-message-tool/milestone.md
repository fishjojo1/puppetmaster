# Milestone 009: Human Message Tool

## Goal

Give managed agents, especially root orchestrators, an explicit frontend-neutral way to talk back to the human operator.

## Decisions Captured

- The MCP tool is named `send_human_message`.
- The tool is not Discord-specific.
- The tool has only a `message` argument in v1.
- Routing is by caller root orchestrator binding.
- If no Discord channel is bound, the tool returns a clear error.
