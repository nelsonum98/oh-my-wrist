# Hook relay latency on every tool call

Type: grilling
Status: open
Blocked by: none

## Question

The relay registered on `PreToolUse` and `PostToolUse` with an empty matcher runs on every tool call in every Claude Code session and takes 0.7-2.2 s per invocation (measured 2026-09-07 with a trivial payload; the suite's 0.5 s timing test also fails). PreToolUse blocks the tool. Decide: accept the latency, narrow the matchers (for example drop PreToolUse entirely if destructive alerts go, keep PostToolUse for the history view), or make the relay a stdlib-only fast path (no pydantic import) that hands off to the daemon. Same question applies to the Codex hooks (timeout 2 s there).
