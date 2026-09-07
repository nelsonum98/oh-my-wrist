# Fork strategy and executor handoff spec

Type: grilling
Status: open
Blocked by: 06, 10, 11

## Question

Stay upstream-mergeable (Codex as a third provider, weekly-only as a config flag, contribute back) or diverge freely? Then lock the executor spec: file-level change list across `src/ohm/` and `garmin/source/`, install and launchd naming, test plan, and the acceptance gate (watch shows it). Resolving this ticket reaches the destination.

Update 2026-09-07. Most of the build already happened on main (Codex provider, weekly-only usage, session-independent fetch, Codex hooks). What remains for this ticket: the alert policy from 06 applied to code, the latency decision from 10, the second-account decision from 11, whether to keep OpenCode, and whether to send anything upstream. The acceptance gate stands: the fork's watch app sideloaded and seen on the wrist.
