# Usage acquisition: session-independent polling vs statusLine relay

Type: grilling
Status: open
Blocked by: 01, 02, 03

## Question

Should the daemon poll both providers' usage endpoints itself on a timer, or keep upstream's statusLine relay (which only updates during an active Claude session) and add a Codex equivalent? Decide poll interval, where the fetchers live in the fork, caching, and what the watch shows when data is stale or the Mac is out of range.

Research 01: only the fetch is session-bound; the daemon push is not. If a poller replaces the statusLine relay, the `patch_claude_statusline()` overwrite (drops `padding`/`refreshInterval`) can be removed entirely.

Research 02 and 03 set the constraints: Codex reads cleanly on demand via `codex app-server` (about 1.5 s, headless). Claude's endpoint 429s with no safe interval, so the real decision is between (a) the daemon reading the statusline script's cache file and only fetching itself when that cache is older than N minutes, at 5-minute-or-slower cadence, and (b) accepting stale Claude usage between sessions. Also decide the stale marker on the watch and the LaunchAgent verification step for the Keychain read.
