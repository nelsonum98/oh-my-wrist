# Codex weekly rate-limit window: which local source to use

Machine: Nelson's Mac (`darwin`, arm64). `codex` CLI: `codex-cli 0.152.0` (binary at
`/Users/nelson/.local/bin/codex`, confirmed via `which codex; codex --version`).
Date of testing: 2026-09-03.

## Question

Which local, read-only source reliably yields the ChatGPT/Codex subscription's **weekly**
rate-limit window (percent used, reset time), headless, with no active interactive Codex
session and no TTY (i.e. runnable from a launchd job)?

## Recommendation (short answer)

**Use `codex app-server`'s `account/rateLimits/read` JSON-RPC method over stdio.** Start
the process, send one `initialize` request followed by `account/rateLimits/read`, read the
response, then kill the process. It is fast (~1s wall time including process spawn),
requires no manual token handling (the app-server reads `~/.codex/auth.json` itself), needs
no TTY, and returned a fully populated result with zero setup beyond having `codex` on
`PATH` (or invoked by absolute path) and `HOME` set — both true under a normal user
launchd agent.

The weekly window is identified by **`windowDurationMins: 10080`** (10080 minutes = 7
days) on whichever of `primary`/`secondary` carries that value. On this account, for the
default `codex` limit bucket, the weekly window is the `primary` window itself (see below);
`secondary` was `null`. For a metered sub-limit (`codex_bengalfox`, a "GPT-5.3-Codex-Spark"
allowance), the weekly window instead showed up as `secondary` (with `primary` = the 5-hour
window, `windowDurationMins: 300`). **Do not hardcode "weekly = primary"; select the window
object whose `windowDurationMins` equals `10080` at read time.**

## Candidate 1: `codex app-server` JSON-RPC — WORKS, recommended

### What I did

1. Confirmed the RPC surface by generating the app-server's own JSON Schema (this is
   Codex's authoritative, versioned protocol definition, not a guess):

   ```
   mkdir -p /tmp/codex_schema
   codex app-server generate-json-schema --out /tmp/codex_schema --experimental
   ```

   This produced `ClientRequest.json` (all request methods) and, under `v2/`,
   `GetAccountRateLimitsResponse.json` and `AccountRateLimitsUpdatedNotification.json`.

2. From `ClientRequest.json`, found the exact method name and params shape:
   - Method: `"account/rateLimits/read"` (params: `null`).
   - Also present: `"account/rateLimitResetCredit/consume"`, `"account/logout"`.
   - `initialize` requires `params.clientInfo.{name, version}` (both `string`, required);
     `title` optional; `capabilities` optional/nullable.

3. Sent a real two-request JSON-RPC session over stdio and captured the response
   (`initialize` then `account/rateLimits/read`, one process, ~1.4s wall time total
   including the process itself):

   ```
   printf '%s\n%s\n' \
     '{"id":1,"method":"initialize","params":{"clientInfo":{"name":"wrist-research","version":"0.0.1"}}}' \
     '{"id":2,"method":"account/rateLimits/read","params":null}' \
     | codex app-server --stdio
   ```
   (In practice I piped this into a background-then-kill invocation so the process didn't
   hang waiting for more stdin; `codex app-server --stdio` does not exit on its own after
   answering, so the caller must close stdin and/or kill the process once the response for
   id `2` has been read.)

### Exact response shape observed (accountId redacted)

```json
{"id":2,"result":{
  "rateLimits": {
    "limitId": "codex",
    "limitName": null,
    "primary": {"usedPercent": 33, "windowDurationMins": 10080, "resetsAt": 1788748170},
    "secondary": null,
    "credits": {"hasCredits": false, "unlimited": false, "balance": "0"},
    "individualLimit": null,
    "spendControlReached": false,
    "planType": "pro",
    "rateLimitReachedType": null
  },
  "rateLimitsByLimitId": {
    "codex_bengalfox": {
      "limitId": "codex_bengalfox",
      "limitName": "GPT-5.3-Codex-Spark",
      "primary":   {"usedPercent": 0, "windowDurationMins": 300,   "resetsAt": 1788409251},
      "secondary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 1788996051},
      ...
    },
    "codex": { "...same as top-level rateLimits.codex..." }
  },
  "rateLimitResetCredits": {"availableCount": 1, "credits": [...]},
  "accountId": "<redacted-account-id>",
  "rateLimitUpsell": null
}}
```

**Exact field names/casing** (camelCase — this is the app-server's own convention, distinct
from the other two sources, see "Field name inconsistency" below):

- `rateLimits.primary.usedPercent` (int) — percent of window used.
- `rateLimits.primary.windowDurationMins` (int, minutes) — **not** `windowMinutes` as the
  ticket assumed; the ticket's guessed field name is wrong for this source. Confirmed from
  the generated schema (`RateLimitWindow` definition in
  `v2/GetAccountRateLimitsResponse.json`) and from the live response.
- `rateLimits.primary.resetsAt` (int64, Unix seconds, UTC) — e.g. `1788748170` decodes to
  `2026-09-07T02:29:30Z`, consistent with "resets ~4 days from today (2026-09-03)" for a
  weekly window.
- `rateLimits.secondary` — same shape, `null` when the account only has one window on that
  limit bucket.
- `rateLimits.limitId` — `"codex"` for the primary ChatGPT/Codex subscription allowance.
- `rateLimitsByLimitId` — an object keyed by `limitId` (e.g. `"codex"`,
  `"codex_bengalfox"`) giving the same `{primary, secondary, ...}` shape per metered
  feature. **This is the field to watch if the wrist app ever needs to distinguish the
  general Codex allowance from a specific model's separate allowance** — on this account,
  the general `codex` bucket's weekly window is in `primary`, but the `codex_bengalfox`
  bucket's weekly window is in `secondary` (its `primary` is a 5-hour/300-minute window).
- `accountId` — present in the response; **redact before writing anywhere** per the
  ticket's constraints (not needed for the weekly-window number itself).

### Selecting the weekly window (concrete algorithm)

For each `{limitId: {primary, secondary}}` entry of interest, pick whichever of `primary`/
`secondary` has `windowDurationMins == 10080`. Don't assume it's always `primary` — this
account's data shows it can be either slot depending on the limit bucket.

### Auth requirements

None to manage manually. `codex app-server` reads `~/.codex/auth.json` itself (same file
the interactive CLI uses); the caller does not pass a token. The `initialize` response
included `"codexHome":"/Users/nelson/.codex"`, confirming it located the auth store from
the default `CODEX_HOME`/`HOME` resolution, with no extra environment variables required
beyond what a normal user launchd agent already has (`HOME`).

### Headless / launchd viability

**Good.** Verified:

- Runs from a non-interactive shell with stdin/stdout pipes only, no TTY needed (I ran it
  exactly that way).
- No active interactive Codex session was running at the time — this spawns and tears down
  its own process instance and does not depend on, or interfere with, any other running
  Codex session.
- Round-trip (process spawn → `initialize` → `account/rateLimits/read` → response) took
  well under 1.5 seconds; `time` on a second run showed ~1.4s total wall clock including a
  deliberate 0.5s sleep before I closed stdin, i.e. actual work was closer to ~0.9s. Cheap
  enough to poll every few minutes from launchd without meaningful resource cost.
- No stderr output was produced during a clean run in either trial — nothing to filter out.
- The one operational wrinkle: `codex app-server --stdio` does **not** exit on its own after
  answering a request; the launchd wrapper script must close stdin after sending the two
  requests and/or kill the process (by PID) once it has read the `id:2` response line. A
  simple pattern: write both JSON-RPC lines to a temp fifo/file, pipe with a short sleep
  after, background the process, sleep briefly, then `kill` it — which is exactly what I
  did for testing. A production wrapper should read line-by-line and kill as soon as it
  sees `"id":2` in the output, rather than relying on a fixed sleep.
- This machine's Codex auth token was auth_mode `"chatgpt"` (OAuth), last refreshed
  `2026-08-29T22:54:01Z` per `~/.codex/auth.json`'s `last_refresh` field (5 days before this
  test) — i.e. token refresh happens as a side effect of normal `codex` usage and does not
  require an interactive login for each call. Whether `codex app-server` itself
  transparently refreshes a token that is close to/past expiry (there is a documented
  `account/chatgptAuthTokens/refresh`-shaped RPC in the schema, i.e. explicit refresh is
  possible) is **not verified here** — I did not have an expired token to test against. If
  the wrist app's launchd job goes unrun for a long stretch (e.g. laptop asleep for
  weeks), it's worth doctoring/logging in once to confirm behavior, but this is a
  theoretical risk, not an observed failure.

### Failure modes observed

None on this machine. The call succeeded cleanly every time (2 separate live trials). No
401s, no timeouts, no hangs beyond the expected "process doesn't self-exit" behavior noted
above (which is a "must kill it" requirement, not a failure).

## Candidate 2: OAuth REST endpoint (`GET https://chatgpt.com/backend-api/wham/usage`) — WORKS but weaker choice

### What I did

Read the access token into a shell variable only, made exactly one `curl` call, and
inspected the status code and JSON keys (never echoed the token; token discarded via
`unset` immediately after):

```
TOKEN=$(jq -r '.tokens.access_token' ~/.codex/auth.json)
curl -s -o /tmp/wham_usage_response.json -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  "https://chatgpt.com/backend-api/wham/usage"
unset TOKEN
```

(The response file was deleted immediately after inspection since it contained the
account's email and `user_id` — see "no secrets" note below.)

### Result

**HTTP 200**, no other headers needed. Response JSON keys (account identifiers redacted):

```json
{
  "user_id": "<redacted-user-id>",
  "account_id": "",
  "email": "<redacted-email>",
  "plan_type": "pro",
  "rate_limit": {
    "allowed": true,
    "limit_reached": false,
    "primary_window": {
      "used_percent": 33,
      "limit_window_seconds": 604800,
      "reset_after_seconds": 356895,
      "reset_at": 1788748170
    },
    "secondary_window": null
  },
  "additional_rate_limits": [
    {
      "limit_name": "GPT-5.3-Codex-Spark",
      "metered_feature": "codex_bengalfox",
      "rate_limit": {
        "primary_window":   {"used_percent": 0, "limit_window_seconds": 18000,  "reset_after_seconds": 18000,  "reset_at": 1788409275},
        "secondary_window": {"used_percent": 0, "limit_window_seconds": 604800, "reset_after_seconds": 604800, "reset_at": 1788996075}
      }
    }
  ],
  "credits": {"has_credits": false, "unlimited": false, "balance": "0"},
  "spend_control": {"reached": false, "individual_limit": null},
  "rate_limit_reached_type": null,
  "rate_limit_reset_credits": {"available_count": 1, "applicable_available_count": 0}
}
```

This is numerically **consistent with the app-server result** captured minutes earlier in
the same session: `used_percent: 33` matches `usedPercent: 33`; `reset_at: 1788748170`
matches `resetsAt: 1788748170` exactly; `limit_window_seconds: 604800` = `604800 / 60 =
10080` minutes, matching `windowDurationMins: 10080`. Same underlying backend data, just a
different (snake_case, seconds-based) field convention.

**Exact field names** for the weekly window here: `rate_limit.primary_window.used_percent`,
`rate_limit.primary_window.limit_window_seconds` (== `604800` for weekly — divide by 60 to
get minutes if you want to compare against the app-server's `windowDurationMins`),
`rate_limit.primary_window.reset_at` (Unix seconds). Note this top-level `rate_limit` object
appears to correspond only to the account's general Codex allowance; per-model breakdowns
live under `additional_rate_limits[]`, each with its own `rate_limit.{primary_window,
secondary_window}`.

### Auth requirements

Bearer token from `~/.codex/auth.json` → `.tokens.access_token`. No other headers were
needed — a plain `Authorization: Bearer <token>` got a 200. This is the same OAuth access
token the app-server itself uses internally, so it is subject to the same expiry/refresh
question noted above, **except** that a bare `curl` call has no refresh mechanism at all —
if the stored token has expired, this path 401s and stays broken until something else
(e.g. running the interactive `codex` CLI, or `codex login`) refreshes `auth.json`. This is
a meaningful downside versus the app-server, which at minimum has an in-process refresh RPC
available to it, and is a normal Codex code path (i.e. more likely to be kept working by
Codex's own maintainers than an undocumented backend endpoint).

### Headless / launchd viability

Technically simpler (one `curl`, no process lifecycle to manage, no JSON-RPC handshake),
and clearly the fastest option per call. But: this is an **undocumented internal backend
endpoint** (`chatgpt.com/backend-api/wham/...`) — not part of any published Codex CLI or
OpenAI API surface I could find via `codex --help` or the generated app-server schema. It
is not covered by any compatibility guarantee, and its exact reset trigger for token expiry
was not tested (no expired token available on this machine). Because it also puts the raw
OAuth bearer token into a shell command, it is more exposure-prone in a scheduled script
(has to read the token into an env var on every run, vs. never touching it directly when
using app-server).

### Failure modes observed

None on this machine — single call, one 200. No 401/429 seen. (Not tested: behavior on an
expired/near-expiry token, since the current token was valid.)

## Candidate 3: Session rollout JSONL (`~/.codex/sessions/2026/**/*.jsonl`) — populated in practice, NOT viable as a standalone headless source

### What I did

```
cd ~/.codex/sessions/2026
find . -name "*.jsonl" | wc -l                              # 3814 total session files
grep -l '"rate_limits"' -r . | wc -l                         # 3761 files contain the key at all
grep -l '"rate_limits":null' -r . | wc -l                    # 13 files where it's literally null
grep -l '"rate_limits":{"limit_id"' -r . | wc -l             # 3761 files where it's a populated object
```

To directly test the ticket's claim that this is "reportedly null under `codex exec`", I
isolated sessions by their recorded `originator` field and cross-checked:

```
grep -l '"originator":"codex_exec"' -r . > /tmp/exec_files.txt   # 1737 codex_exec session files
comm -12 <(sort /tmp/exec_files.txt) <(grep -l '"rate_limits":null' -r . | sort) | wc -l
  # -> 0   (zero codex_exec sessions have an explicit null)
comm -12 <(sort /tmp/exec_files.txt) <(grep -l '"rate_limits":{"limit_id"' -r . | sort) | wc -l
  # -> 1695  (97.6% of codex_exec sessions have it fully populated)
```

The remaining 42 `codex_exec` files (1737 − 1695) were checked individually: they are very
short sessions (as few as 2 JSONL lines) that ended before any `token_count` event was ever
emitted — the `rate_limits` key is simply **absent** from those files, not present-and-null.
So on this machine, **the ticket's premise is refuted**: `codex exec` sessions do carry
populated `rate_limits`, in the large majority of cases; the only sessions missing it are
ones too short to have produced a single token-usage event.

The 13 files with a literal `"rate_limits":null` were all much older `"Codex
Desktop"`-originated sessions from `cli_version` `0.144.x` (July 2026) — an old client
version/originator, not `codex_exec` and not representative of the current CLI (0.152.0)
this machine now runs. This appears to be a client-version-specific quirk from ~1.5 months
before this test, not a `codex exec`-specific one.

### Exact shape observed (from a live, current-CLI, `codex_exec`-originated file,
`~/.codex/sessions/2026/09/01/rollout-2026-09-01T22-27-19-01a05ede-9121-7c90-b983-f52e859458a1.jsonl`)

```json
{
  "type": "event_msg",
  "payload": {
    "type": "token_count",
    "info": {
      "total_token_usage": {...}, "last_token_usage": {...}, "model_context_window": 258400,
      "rate_limits": {
        "limit_id": "codex",
        "limit_name": null,
        "primary":   {"used_percent": 27.0, "window_minutes": 10080, "resets_at": 1788748171},
        "secondary": null,
        "credits": {"has_credits": false, "unlimited": false, "balance": "0"},
        "individual_limit": null,
        "spend_control_reached": null,
        "plan_type": "pro",
        "rate_limit_reached_type": null
      }
    }
  }
}
```

**Exact field names**: `payload.info.rate_limits.primary.{used_percent, window_minutes,
resets_at}` (snake_case — a third distinct naming convention from the same underlying data;
see below). `window_minutes: 10080` again identifies the weekly window.

### Why this is not the recommended source, despite being populated

- **It requires a `rate_limits`-carrying event to already exist**, i.e. the number is a
  byproduct of some *other* Codex invocation (interactive or `codex exec`) having recently
  made an API call — it is not obtainable on demand. A launchd job reading only this source
  has no way to force a fresh reading; it can only report whatever the last real Codex
  usage happened to record, which could be stale by hours or days if Codex hasn't been used.
- It requires finding "the most recent JSONL file, most recent matching line" logic (glob +
  sort by mtime/filename timestamp + reverse-scan for the last `token_count` event), which
  is more fragile than a direct RPC/HTTP call and depends on stable session-file naming and
  layout that isn't a documented external contract.
- It is a strictly worse version of candidate 1's data — same numbers, same `windowMinutes`
  concept, harder to obtain reliably and staler.

### Auth requirements

None — purely local file reads. No secrets involved in the `rate_limits` payload itself
(no token, no account id in the sampled lines, though other lines/fields in these rollout
files can contain prompt/response content, which is out of scope here).

### Headless / launchd viability

Read-only file access works fine headless (no session/TTY dependency for reading), but per
above, it cannot produce a *fresh* weekly-window reading on its own — only whatever was last
recorded by an actual Codex turn. Not recommended as the primary source; could be a passive
fallback/cross-check only.

### Failure modes observed

- 13/3814 files (0.34%) had a literal `null` — all from an old `Codex Desktop` client
  version (0.144.x), not from `codex exec` or the current CLI version.
- 42/1737 (2.4%) `codex_exec` files had no `rate_limits` key at all (too-short sessions,
  never reached a `token_count` event) — an absence, not a null.
- 97.6% of `codex_exec` files, and effectively all files from the current CLI version
  (0.152.0), had it fully populated. The ticket's stated concern ("reportedly null under
  `codex exec`") did not reproduce on this machine's actual session history.

## Field-name inconsistency across the three sources (same underlying numbers)

All three sources agree numerically (`used_percent`/`usedPercent` = 33 or 27–30 depending
on exact capture time; `resetsAt`/`reset_at`/`resets_at` = `1788748170`/`1788748171`,
i.e. the same reset instant to within a second of drift between captures) but use three
different field-naming conventions for the same weekly-window concept:

| Source | percent-used field | window-length field | window value for weekly | reset field |
|---|---|---|---|---|
| app-server RPC (`account/rateLimits/read`) | `usedPercent` | `windowDurationMins` | `10080` (minutes) | `resetsAt` (Unix s) |
| REST (`/backend-api/wham/usage`) | `used_percent` | `limit_window_seconds` | `604800` (seconds) | `reset_at` (Unix s) |
| Session JSONL (`rate_limits.primary`) | `used_percent` | `window_minutes` | `10080` (minutes) | `resets_at` (Unix s) |

Note the ticket's assumed app-server field name, `windowMinutes`, does not exist in this
version of the protocol (`0.152.0`) — the real field is `windowDurationMins`. The session
JSONL source does use `window_minutes` (snake_case), which is close to what the ticket
guessed, but that's a different source than the one the ticket attributed it to.

## Overall recommendation for the wrist app

Use **`codex app-server`'s `account/rateLimits/read`** as the primary/only source for the
weekly window:

- Field to read: whichever of `rateLimits.primary` / `rateLimits.secondary` (or, per
  metered sub-limit, `rateLimitsByLimitId["<limitId>"].primary/.secondary`) has
  `windowDurationMins == 10080`. Report its `usedPercent` and convert its `resetsAt` (Unix
  seconds) to local time for display.
- No manual token handling — the app-server reads `~/.codex/auth.json` on its own.
- Fast (sub-second to ~1.5s including process spawn/teardown) and clean (no stderr, no
  hangs) in two live trials on this machine.
- The launchd wrapper must (a) send `initialize` then `account/rateLimits/read` over
  stdin, (b) read stdout line-by-line until it sees the response with matching request
  `id`, then (c) kill the process — it will not exit on its own after answering.

Treat the REST endpoint as a fallback only if app-server becomes unavailable for some
reason (e.g. a future Codex CLI removes/changes the RPC) — it is undocumented, and every
call means handling the raw bearer token directly in the calling script. Do not rely on the
session-JSONL source as primary; it can only reflect the last real Codex activity, not a
fresh on-demand reading, though it is useful as a passive cross-check since it's already
sitting on disk from ordinary Codex use.

## Secrets handling note

No bearer token, full account ID, or user ID was written to any file as part of this
research. The one REST response captured to `/tmp/wham_usage_response.json` (containing an
`email` and `user_id` field) and the app-server JSON-RPC outputs (containing an
`accountId` field) were deleted immediately after their shapes were transcribed, redacted,
into this document. Temporary JSON-RPC input/output files and the generated protocol-schema
directory used for exploration were also cleaned up from `/tmp` after this document was
written.
