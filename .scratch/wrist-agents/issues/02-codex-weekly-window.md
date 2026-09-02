# Codex weekly window: which programmatic source works on this Mac

Type: research
Status: resolved
Blocked by: none

## Question

Which source reliably yields the ChatGPT/Codex subscription **weekly** rate-limit window (percent used, reset time) on this machine, headless, without an active Codex session?

Candidates to probe locally, read-only:

1. `codex app-server` JSON-RPC `account/rateLimits/read` (fields reportedly `rateLimits.primary/secondary.usedPercent/resetsAt`). Confirm which of primary/secondary is the weekly window (`windowMinutes`).
2. OAuth `GET https://chatgpt.com/backend-api/wham/usage` with the bearer token from `~/.codex/auth.json`. Confirm response shape and whether it 401s or rate-limits.
3. Session rollout JSONL under `~/.codex/sessions/2026/` carrying `rate_limits.primary.{used_percent,window_minutes,resets_at}`; confirm it is null under `codex exec` as reported.

Report: the recommended source, exact field names, auth needs, how it behaves headless under launchd, and the failure modes seen. Never write a token into any file or the report; redact values. Deliver `docs/research/codex-weekly-window.md`.

## Answer

Resolved 2026-09-03. Full note: [docs/research/codex-weekly-window.md](../../../docs/research/codex-weekly-window.md).

- Recommended source: `codex app-server` JSON-RPC over stdio: send `initialize`, then `account/rateLimits/read`, read the response, kill the process (it does not exit on its own). About 1-1.5 s per call, verified live on Codex CLI 0.152.0, works with no TTY and no active session, so launchd is fine.
- Field names are camelCase: `rateLimits.primary|secondary.usedPercent`, `.windowDurationMins`, `.resetsAt` (epoch seconds). Select the weekly window by `windowDurationMins == 10080` at read time; it is `primary` for the general bucket but `secondary` for metered sub-limits, so never hardcode which. The ticket's guessed `windowMinutes` does not exist.
- Auth: none to manage; app-server reads `~/.codex/auth.json` itself.
- Fallbacks: the `wham/usage` REST endpoint works (snake_case, `limit_window_seconds: 604800`) but needs raw bearer handling with no refresh path, so it is fallback only. Session rollout JSONL is populated even under `codex exec` (the ticket premise was wrong) but only reflects the last real activity, not an on-demand read.
