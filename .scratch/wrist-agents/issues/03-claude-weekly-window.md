# Claude weekly window: verify the OAuth usage endpoint and headless access

Type: research
Status: claimed
Blocked by: none

## Question

What does the Claude OAuth usage endpoint actually return today for the **weekly** window, and can it be read headless from a launchd daemon on this Mac?

Start from the fetch block in `~/.claude/statusline-command.sh` (it calls `https://api.anthropic.com/api/oauth/usage` with the token from the macOS Keychain item `Claude Code-credentials` or `~/.claude/.credentials.json`). Read-only probes:

1. Capture one live response and record field names for the weekly windows (`seven_day`, `seven_day_opus`, `seven_day_sonnet` reported) and the utilization scale (0-1 vs 0-100). Redact any token.
2. Determine whether a launchd daemon can read the Keychain item without a UI prompt, and what the statusline script does to avoid 429s (caching interval, headers).
3. Compare with upstream oh-my-wrist's `statusline_relay.py`, which gets usage from Claude Code's statusLine stdin: does that path update only during an active session?

Deliver `docs/research/claude-weekly-window.md`: recommended fetch approach for a session-independent daemon, poll interval, and caveats.
