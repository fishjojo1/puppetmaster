# Milestone 016: Orchestrator ID And Discord Screenshot

## Goal

Allow users to choose a root orchestrator id at creation time and add a Discord slash command that sends a rendered snapshot of the bound root orchestrator's tmux pane.

## User Value

A user can start a root orchestrator with a predictable id that is easy to bind from Discord, then ask Discord for a terminal screenshot without attaching to tmux locally.

## Scope

- Add `puppet orchestrator start --agent-id <id>` for root orchestrator creation.
- Validate caller-provided ids with a safe, path-friendly format.
- Reject duplicate ids before creating registry rows, agent directories, or tmux sessions.
- Derive registry id, root id, agent directory, log paths, and tmux session from the provided id.
- Preserve generated ids when `--agent-id` is omitted.
- Preserve JSON output and make human output clear for provided and generated ids.
- Add Discord slash command `/puppet screenshot`.
- Capture the currently bound root orchestrator's tmux pane text.
- Render the terminal text snapshot to a PNG image.
- Send the PNG back to the invoking Discord channel as an attachment.

## Decisions Captured

- `--agent-id` applies to root orchestrator creation only.
- Provided ids are exact ids, not display names or aliases.
- Id validation prevents path traversal, shell-hostile characters, and empty values.
- Duplicate ids fail clearly and do not reuse existing agents.
- Discord screenshots target the root orchestrator bound to the current channel.
- The MVP screenshot is a rendered terminal text snapshot from tmux pane contents.
- ANSI and full color fidelity are follow-up work, not required for the MVP.

## Non-Goals

- No custom ids for child agents.
- No renaming an existing agent id.
- No alias system for Discord binding.
- No migration of existing generated ids.
- No GUI, browser, or desktop screenshot capture.
- No user-upload image attachment intake.
- No OCR or image analysis.
- No guaranteed pixel-perfect terminal color reproduction.
