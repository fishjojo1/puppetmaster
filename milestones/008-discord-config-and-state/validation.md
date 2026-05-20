# Validation Plan

## Automated Tests

Add tests in `tests/test_core.py` or a new focused test module.

Config tests:

- Default config creation writes a `[discord]` section.
- Empty token and guild id load as `None`.
- Numeric string guild id loads successfully.
- Integer guild id loads successfully.
- Invalid poll interval fails with a clear `PuppetError` or validation exception.
- Invalid chunk size above `1900` fails.
- Invalid `max_chunks <= 0` fails.

Registry binding tests:

- Binding a channel creates a retrievable row.
- Rebinding the same channel updates the root.
- Binding the same root to another channel leaves only one binding for that root.
- Unbinding removes the channel binding.
- Lookup by channel and lookup by root both work.
- Listing bindings returns all current bindings ordered predictably.

Outbound queue tests:

- Enqueue creates a `pending` Discord outbound message.
- Pending query returns only pending messages for the requested transport.
- Mark delivered sets `status = delivered` and `delivered_at`.
- Mark failed sets `status = failed`, `failed_at`, and `error`.
- Failed and delivered messages do not appear in pending results.

## Manual Checks

From the repo root:

```bash
PYTHONPATH=src python3 -m puppetmaster.cli doctor --json
```

Then inspect local config:

```bash
sed -n '1,120p' .puppetmaster/config.toml
```

Expected:

- Existing limit and Codex sections still exist.
- `[discord]` exists.
- Token and guild id are empty by default.

Check ignored state:

```bash
git check-ignore .puppetmaster/config.toml
```

Expected:

- The file is ignored by git.

## Acceptance Criteria

- The new config loads without changing existing CLI behavior.
- Existing tests still pass.
- Binding and outbound queue operations are covered by tests.
- No Discord dependency or runtime behavior is required yet.
