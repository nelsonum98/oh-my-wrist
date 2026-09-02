# Codex weekly window: which programmatic source works on this Mac

Type: research
Status: claimed
Blocked by: none

## Question

Which source reliably yields the ChatGPT/Codex subscription **weekly** rate-limit window (percent used, reset time) on this machine, headless, without an active Codex session?

Candidates to probe locally, read-only:

1. `codex app-server` JSON-RPC `account/rateLimits/read` (fields reportedly `rateLimits.primary/secondary.usedPercent/resetsAt`). Confirm which of primary/secondary is the weekly window (`windowMinutes`).
2. OAuth `GET https://chatgpt.com/backend-api/wham/usage` with the bearer token from `~/.codex/auth.json`. Confirm response shape and whether it 401s or rate-limits.
3. Session rollout JSONL under `~/.codex/sessions/2026/` carrying `rate_limits.primary.{used_percent,window_minutes,resets_at}`; confirm it is null under `codex exec` as reported.

Report: the recommended source, exact field names, auth needs, how it behaves headless under launchd, and the failure modes seen. Never write a token into any file or the report; redact values. Deliver `docs/research/codex-weekly-window.md`.
