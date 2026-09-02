# Claude weekly window: verify the OAuth usage endpoint and headless access

Type: research
Status: resolved
Blocked by: none

## Question

What does the Claude OAuth usage endpoint actually return today for the **weekly** window, and can it be read headless from a launchd daemon on this Mac?

Start from the fetch block in `~/.claude/statusline-command.sh` (it calls `https://api.anthropic.com/api/oauth/usage` with the token from the macOS Keychain item `Claude Code-credentials` or `~/.claude/.credentials.json`). Read-only probes:

1. Capture one live response and record field names for the weekly windows (`seven_day`, `seven_day_opus`, `seven_day_sonnet` reported) and the utilization scale (0-1 vs 0-100). Redact any token.
2. Determine whether a launchd daemon can read the Keychain item without a UI prompt, and what the statusline script does to avoid 429s (caching interval, headers).
3. Compare with upstream oh-my-wrist's `statusline_relay.py`, which gets usage from Claude Code's statusLine stdin: does that path update only during an active session?

Deliver `docs/research/claude-weekly-window.md`: recommended fetch approach for a session-independent daemon, poll interval, and caveats.

## Answer

Resolved 2026-09-03. Full note: [docs/research/claude-weekly-window.md](../../../docs/research/claude-weekly-window.md).

- Live response carries the weekly number in two shapes: `seven_day.utilization` + `seven_day.resets_at` (ISO-8601), and a `limits[]` array with `kind: "weekly_all"` and per-model `weekly_scoped` entries (`percent`, `resets_at`, `is_active`, `severity`). Scale is 0-100. The `seven_day_opus`/`seven_day_sonnet` keys were null on this account.
- The endpoint is undocumented and rate-limits hard: the single probe in this research was 429'd about 172 s after the statusline's last fetch, and two GitHub issues (30930 open, 31021 closed "not planned") report stuck 429 loops at 30-120 s intervals. There is no safe interval on record.
- Recommended: do not add a second poller against the same token. Read the statusline script's cache file (`${TMPDIR}/claude-statusline-oauth-usage-<uid>.json`) when fresh; if the daemon must fetch itself, poll no faster than every 5 minutes, treat 429 as "keep last value", and serve stale values for up to 30 minutes before marking unknown, mirroring the statusline script.
- Keychain: `security find-generic-password -s "Claude Code-credentials" -w` ran without a prompt here. MODERATE confidence a LaunchAgent (not a LaunchDaemon) inherits that silently; verify on the first real launchd run. Never widen the ACL with `-A`.
- Upstream's `statusline_relay.py` is strictly session-bound: it reads `rate_limits` from statusLine stdin only, and those values are epoch seconds with 0-100 percentages.
