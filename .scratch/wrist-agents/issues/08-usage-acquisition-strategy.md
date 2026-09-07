# Usage acquisition: session-independent polling vs statusLine relay

Type: grilling
Status: resolved
Blocked by: 01, 02, 03

## Question

Should the daemon poll both providers' usage endpoints itself on a timer, or keep upstream's statusLine relay (which only updates during an active Claude session) and add a Codex equivalent? Decide poll interval, where the fetchers live in the fork, caching, and what the watch shows when data is stale or the Mac is out of range.

Research 01: only the fetch is session-bound; the daemon push is not. If a poller replaces the statusLine relay, the `patch_claude_statusline()` overwrite (drops `padding`/`refreshInterval`) can be removed entirely.

Research 02 and 03 set the constraints: Codex reads cleanly on demand via `codex app-server` (about 1.5 s, headless). Claude's endpoint 429s with no safe interval, so the real decision is between (a) the daemon reading the statusline script's cache file and only fetching itself when that cache is older than N minutes, at 5-minute-or-slower cadence, and (b) accepting stale Claude usage between sessions. Also decide the stale marker on the watch and the LaunchAgent verification step for the Keychain read.

## Answer

Resolved 2026-09-07 by construction (`src/ohm/usage_fetchers.py`, `ble_daemon._periodic_usage_task`). The daemon polls every 5 minutes independent of any session. Claude: reads the statusline script's cache file and only fetches the OAuth endpoint itself when the cache is older than 5 minutes, with a 30-minute stale tolerance, so there is one fetch budget shared with the statusline. Codex: `codex app-server` `account/rateLimits/read`, weekly bucket by `windowDurationMins == 10080`. Fix on 2026-09-07 (ce5d659): a failed Codex read now keeps the last good value for 30 minutes instead of blanking the bar; today's log showed the bar dropping to unknown on about half of the refreshes. The Keychain read works from the LaunchAgent (Claude values refresh in the log). Stale marker on the watch: none yet.
