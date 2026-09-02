# Usage acquisition: session-independent polling vs statusLine relay

Type: grilling
Status: open
Blocked by: 01, 02, 03

## Question

Should the daemon poll both providers' usage endpoints itself on a timer, or keep upstream's statusLine relay (which only updates during an active Claude session) and add a Codex equivalent? Decide poll interval, where the fetchers live in the fork, caching, and what the watch shows when data is stale or the Mac is out of range.

Research 01: only the fetch is session-bound; the daemon push is not. If a poller replaces the statusLine relay, the `patch_claude_statusline()` overwrite (drops `padding`/`refreshInterval`) can be removed entirely.
