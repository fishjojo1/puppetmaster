# Milestone 011: Discord Bidirectional Runtime

## Goal

Complete the bidirectional Discord experience: messages that mention or reply to the bot reach the orchestrator, and `send_human_message` replies are posted back to Discord.

## Decisions Captured

- Inbound prompts require a bot mention or a reply to the bot.
- Plain channel chatter is ignored.
- Inbound prompt format is exactly:

```text
DISCORD MESSAGE RECEIVED:
<message>
```

- Attachments are ignored in v1.
- Successful prompt delivery uses a low-noise reaction.
- Delivery failures get visible replies.
- Multiple prompts are delivered immediately; Codex/tmux handles queueing.
- The bot polls SQLite rather than receiving hook callbacks.
- Typing is shown while work is pending and stopped by reply, root turn stop, or timeout.
