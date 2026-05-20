# Validation Plan

## Automated Tests

Regression tests:

- Existing test suite passes.
- Discord config defaults do not break non-Discord CLI use.
- Registry initialization works on a fresh state dir.
- Registry initialization works on an existing state dir.

Error tests:

- Missing Discord token produces a clear startup error.
- Missing Discord guild id produces a clear startup error.
- Unbound channel prompt returns setup help.
- `/puppet bind` rejects non-root agents.
- `/puppet read` reports missing binding.
- Outbound delivery failure is persisted and does not crash poll loop.

Recovery tests:

- Pending outbound message created before bot start is delivered after dispatcher starts.
- Delivered outbound message is not sent twice after dispatcher restart.
- Failed outbound message is not retried in v1.
- Existing binding remains available after creating a new `Registry` instance.

Documentation checks:

- README mentions `.puppetmaster/config.toml` and token storage.
- README mentions `puppet discord serve`.
- README mentions mention/reply-only prompt behavior.
- README mentions `send_human_message`.
- README includes the safety warning.

## Manual End-To-End Release Check

1. Create a fresh temporary state directory.

```bash
state_dir="$(mktemp -d)"
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH=src python3 -m puppetmaster.cli doctor --deep
```

2. Add Discord config to `$state_dir/config.toml`.

3. Start root orchestrator.

```bash
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH=src python3 -m puppetmaster.cli orchestrator start \
  --cwd /home/kek/Projects/puppetmaster \
  --prompt "You are the root orchestrator. Reply to Discord prompts with send_human_message."
```

4. Start Discord bot.

```bash
PUPPETMASTER_STATE_DIR="$state_dir" PYTHONPATH=src python3 -m puppetmaster.cli discord serve
```

5. In Discord:

- Run `/puppet agents`.
- Run `/puppet bind agent_id:<root-id>`.
- Run `/puppet status`.
- Mention the bot with a request.
- Confirm checkmark reaction.
- Confirm typing starts.
- Confirm reply appears when orchestrator calls `send_human_message`.
- Confirm typing stops.
- Run `/puppet read`.
- Run `/puppet tree`.
- Run `/puppet unbind`.

6. Restart the bot.

Expected:

- Slash commands still work.
- Existing binding persists if not unbound.
- Pending outbound messages are handled once.

## Acceptance Criteria

- The integration is documented enough to operate without reading source code.
- Common failure modes produce short actionable errors.
- Restart behavior is predictable.
- The safety model is explicit: personal, guild-scoped, local state, no per-user auth.
