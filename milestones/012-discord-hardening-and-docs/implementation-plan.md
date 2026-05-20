# Implementation Plan

## Error Handling

Improve user-facing Discord errors:

- Missing channel binding:
  - `No orchestrator is bound to this channel. Use /puppet agents, then /puppet bind.`
- Missing token/guild config:
  - CLI startup error names `.puppetmaster/config.toml`.
- Missing root agent:
  - `/puppet bind` says the agent was not found.
- Child agent bind attempt:
  - `/puppet bind` says only root orchestrators can be bound.
- Dead tmux session on inbound prompt:
  - Reply with the existing service error and suggest `/puppet status` or `/puppet read`.

Keep errors short enough for Discord.

## Logging

Use existing Puppetmaster logging patterns where available.

Log:

- Bot startup.
- Slash command sync result.
- Bind/unbind operations.
- Inbound prompt deliveries.
- Outbound message delivery success/failure.
- Typing timeout.

Avoid logging full message bodies by default. Use message lengths and ids where practical.

## Recovery Behavior

On bot restart:

- Existing channel bindings still work.
- Pending outbound messages are sent.
- Delivered/failed messages are not resent.
- In-memory typing state is empty; this is acceptable.

Document that typing state is best-effort and not durable.

## Rate Limits And Backoff

For v1:

- Rely on `discord.py` built-in rate-limit handling for ordinary sends.
- Keep poll batch size bounded.
- If a send fails due to transient Discord errors, mark failed in v1 rather than implementing retry loops.

Future retry support can add:

- `attempts`
- `next_attempt_at`
- retryable/permanent failure distinction

Do not add those fields unless needed during implementation.

## Documentation

Update `README.md` with:

- Config example.
- Running the bot.
- Starting/binding an orchestrator.
- Slash command list.
- Mention/reply prompt behavior.
- `send_human_message` behavior.
- Security note that Discord controls local Codex sessions that launch with bypassed approvals/sandbox.

Update `docs/spec-conformance.md` if it tracks implemented surfaces.

Add or update `spec.md` with the overarching Discord design brief if not already done.

## Release Validation Script

Consider extending `scripts/release-validate.sh` with non-network Discord checks:

- Config loads.
- Registry schema initializes.
- `puppet discord serve` is not run because it needs a real token.
- Unit tests cover Discord helpers.

## Non-Goals

- No per-user authorization.
- No global multi-state Discord manager.
- No thread support.
- No attachment support.
- No slash command embeds.
