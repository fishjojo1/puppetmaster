# Implementation Plan

## CLI Shape

Extend root orchestrator startup:

```bash
puppet orchestrator start \
  --agent-id project-a \
  --cwd /project/a \
  --prompt "Manage project A."
```

Behavior:

- `--agent-id` is optional.
- When omitted, existing generated id behavior is unchanged.
- When provided, the created root agent id is exactly the provided value.
- Human output includes the root id and attach command as it does today.
- JSON output keeps the existing agent object shape and reports the provided id in `agent.id` and `agent.root_id`.

`--agent-id` should not be added to child creation in this milestone.

## Safe ID Validation

Accept only ids that are safe for registry keys, directory names, tmux session derivation, and command output.

Recommended rule:

```text
[A-Za-z0-9][A-Za-z0-9_.-]{0,63}
```

Reject:

- Empty ids.
- Absolute or relative paths.
- Path separators.
- Whitespace.
- Shell metacharacters.
- Leading dot.
- Values longer than the accepted maximum.
- Reserved names if any existing state filenames would conflict.

Error output should explain the accepted format.

## Uniqueness And Creation Semantics

Perform duplicate checks before any side effects.

Required behavior:

- If the id already exists in the registry, fail with a clear duplicate-id error.
- If the target agent directory already exists without a registry row, fail clearly instead of reusing it.
- If the derived tmux session already exists, fail clearly instead of attaching to it.
- A failed duplicate check must not create registry rows, files, logs, or tmux sessions.
- The root agent's `id` and `root_id` are the same provided value.

If any later startup step fails after files are created, preserve the existing failure handling pattern and avoid deleting unrelated state.

## Path And Session Derivation

Thread the optional id through the service layer that creates root agents.

Derived values must use the final id consistently:

- Registry `agents.id`.
- Registry `agents.root_id`.
- Agent state directory.
- Initial prompt path.
- Terminal log path.
- Events path.
- Launch script path.
- Per-agent Codex config directory.
- Tmux session name.

Keep path construction centralized through existing helpers where possible so generated and provided ids follow one code path after validation.

## Discord Screenshot Command

Add slash command:

```text
/puppet screenshot
```

Required behavior:

- Command must run from a guild text channel.
- Channel must be bound to a root orchestrator.
- The command captures the bound root orchestrator's current tmux pane.
- Missing tmux session fails with a clear Discord reply.
- Successful command sends a PNG attachment back to the channel.
- The PNG represents a terminal pane snapshot, not a GUI screenshot.

The command should not prompt the orchestrator or create inbound events.

## Tmux Pane Capture

Use tmux capture APIs rather than reading the saved terminal log.

Expected capture:

- Capture visible pane contents for the bound root's tmux session.
- Preserve line order and spacing.
- Use a bounded width and height from the pane or a sensible default.
- Handle empty panes by rendering an explicit empty-terminal image.

If the existing tmux wrapper lacks a suitable method, add a small helper for pane text capture.

## PNG Rendering

Render captured terminal text to PNG.

MVP rendering requirements:

- Monospace font.
- Stable padding.
- Dark or light terminal-like background.
- Text wrapping or clipping that matches the captured pane dimensions closely enough for debugging.
- Output as in-memory bytes or a temporary file suitable for Discord attachment upload.

Implementation options:

- Use Pillow if it is already acceptable as a dependency.
- If adding Pillow is too heavy, use a small rendering backend already available in the project environment.

ANSI color fidelity, cursor rendering, scrollback rendering, and terminal theme matching are not required for the MVP.

## Documentation

Update user-facing docs with:

- How to start a root with `--agent-id`.
- The accepted id format.
- Duplicate-id behavior.
- How custom ids simplify `/puppet bind`.
- How `/puppet screenshot` works.
- That the screenshot is a rendered tmux pane text image.
- Known limitations around ANSI/color fidelity.

## Implementation Order

1. Add id validation helper and tests.
2. Thread optional root id through orchestrator CLI and service creation.
3. Add duplicate registry, directory, and tmux-session checks before side effects.
4. Verify generated-id behavior is unchanged.
5. Add tmux visible-pane capture helper.
6. Add terminal-text-to-PNG renderer.
7. Add `/puppet screenshot` command and Discord tests.
8. Update README and conformance docs.
9. Run automated tests and a live Discord screenshot smoke check if credentials are available.
