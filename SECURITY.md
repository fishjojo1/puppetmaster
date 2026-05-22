# Security

## Supported Versions

Puppetmaster is currently pre-1.0. Security fixes are made on the default branch until a stable release policy exists.

## Reporting A Vulnerability

Please do not publish exploitable details before maintainers have had time to respond. Open a private security advisory on GitHub if available, or contact the repository owner directly.

Include:

- Affected version or commit.
- Reproduction steps.
- Expected and observed behavior.
- Impact, including whether a Discord-bound user or managed Codex session is required.

## Threat Model Notes

Puppetmaster is a local automation supervisor, not a sandbox. Managed Codex sessions are intentionally launched with bypassed approvals and sandbox checks. A Discord-bound channel can control the bound root orchestrator, and that orchestrator can create local child agents with the same broad local permissions.

Keep Discord bot tokens and `~/.puppetmaster/config.toml` private. Bind only trusted Discord channels. Treat anyone who can mention or reply to the bot in a bound channel as able to operate local Codex sessions.
