# Research: the Claude OAuth usage endpoint and headless weekly-window reads

Ticket: `.scratch/wrist-agents/issues/03-claude-weekly-window.md`. Scope: verify what
`/api/oauth/usage` actually returns for the weekly window today, whether a launchd
daemon can read it without an active Claude Code session, and how `statusline_relay.py`
(upstream oh-my-wrist) sources usage data.

All code citations are `file:line` against the files as read on 2026-09-03. All live-data
claims are redacted of token and account-identifying fields (email, account/org id, model
id, model display name) — see the "Redaction" note in each section.

## 1. Statusline script's usage-fetch mechanism

Source: `~/.claude/statusline-command.sh` (outside this repo, read-only, not modified).

- **Endpoint**: `https://api.anthropic.com/api/oauth/usage` — `statusline-command.sh:157`.
- **Token retrieval**, in order:
  1. macOS Keychain: `security find-generic-password -s "Claude Code-credentials" -w` — `statusline-command.sh:153-154`.
  2. Fallback if empty: `jq -r '.claudeAiOauth.accessToken // empty' "$HOME/.claude/.credentials.json"` — `statusline-command.sh:155`.
  Both pull the same field, `.claudeAiOauth.accessToken`, from either the Keychain-stored JSON blob or the on-disk credentials file.
- **Headers**: `Authorization: Bearer $tok` and `anthropic-beta: oauth-2025-04-20` — `statusline-command.sh:158`.
- **Caching**:
  - Cache file: `${TMPDIR:-/tmp}/claude-statusline-oauth-usage-$(id -u).json` — `statusline-command.sh:151`.
  - Freshness check before firing a new request: cache is trusted (no re-fetch) if it exists and has an mtime within the last 60 seconds (`find "$oauth_cache" -newermt '-60 seconds'`) — `statusline-command.sh:152`.
  - The fetch only writes the cache back if the response parses and has a `.limits` key (`jq -e '.limits' >/dev/null 2>&1`) — `statusline-command.sh:159`. A failed/malformed response (e.g. a 429 body, which has no `.limits` key) is silently discarded and the *old* cache file is left in place with its original mtime.
  - Separately, the script treats the cache as usable ("ground truth") for up to **30 minutes** past its last successful write (`oauth_ok` gate, `find "$oauth_cache" -newermt '-30 minutes'`) even if every fetch since then has failed — `statusline-command.sh:163-164`. Past 30 minutes stale, `oauth_ok` is unset and the script falls back to the session-stdin `rate_limits` fields (see §3) or drops the row.
- **Parsing / what it does with the result**: `oauth_limit()` (`statusline-command.sh:165-172`) does a `jq` select over the response's `.limits[]` array, filtering by `.kind` (and optionally by `.scope.model.display_name` matching a regex) and returning `percent` + `resets_at` as a tab-separated pair. It's called three ways:
  - `oauth_limit session ""` → 5-hour window fallback when the stdin JSON lacks `rate_limits.five_hour` — `statusline-command.sh:176-177`.
  - `oauth_limit weekly_all ""` → 7-day aggregate window fallback — `statusline-command.sh:184-185`.
  - `oauth_limit weekly_scoped fable` → a weekly window scoped to the "Fable" model specifically, used for a Fable-only usage estimate — `statusline-command.sh:210`.
  The script's own comment (`statusline-command.sh:147-150`) states the rationale: the statusline **stdin** only carries aggregate `five_hour`/`seven_day`, but this endpoint (same one `/usage` reads) additionally exposes a weekly bucket scoped to a specific model/product, which is why it calls the OAuth endpoint at all rather than relying solely on stdin.

## 2. Live response: field names and utilization scale

**One live call was made** (per the ticket's "exactly once" constraint) to `https://api.anthropic.com/api/oauth/usage`, reusing the exact token-retrieval command above. It returned:

```json
{"error":{"type":"rate_limit_error","message":"Rate limited. Please try again later."}}
```
— HTTP 429, no `.limits` key, so the statusline script's own cache-write guard (`jq -e '.limits'`, `statusline-command.sh:225`) would have (and did, in normal operation) left the existing cache untouched. This 429 is itself evidence for §5 below: it fired ~172 seconds after the last successful cache write, well outside the script's own 60s minimum-refetch interval, meaning even conservative, cache-gated polling from a single consumer can trip the endpoint's rate limiter.

Because the live call was rate-limited, field names below come from the **existing on-disk cache file** written by the last successful fetch during normal statusline operation earlier the same session (`${TMPDIR:-/tmp}/claude-statusline-oauth-usage-501.json`, mtime 2026-09-03 01:17:40 local, i.e. within the 30-minute trust window) — not a second live call. This is pre-existing local state, not a new request.

**Redaction**: token, `scope.model.id`, `scope.model.display_name`, and `extra_usage.user_disabled` were redacted below; everything else is structural/numeric.

```json
{
  "five_hour": {
    "utilization": 11.0,
    "resets_at": "2026-09-03T02:40:00.401892+00:00",
    "limit_dollars": null, "used_dollars": null, "remaining_dollars": null, "locked_reason": null
  },
  "seven_day": {
    "utilization": 13.0,
    "resets_at": "2026-09-08T14:00:00.401917+00:00",
    "limit_dollars": null, "used_dollars": null, "remaining_dollars": null, "locked_reason": null
  },
  "seven_day_oauth_apps": null,
  "seven_day_opus": null,
  "seven_day_sonnet": null,
  "seven_day_cowork": null,
  "seven_day_omelette": null,
  "tangelo": null,
  "iguana_necktie": null,
  "omelette_promotional": null,
  "nimbus_quill": { "utilization": 0.0, "resets_at": null, "limit_dollars": null, "used_dollars": null, "remaining_dollars": null, "locked_reason": null },
  "cinder_cove": null,
  "amber_ladder": null,
  "juniper_tide": "[REDACTED]",
  "extra_usage": {
    "is_enabled": false, "monthly_limit": null, "used_credits": null, "utilization": null,
    "currency": null, "decimal_places": null, "disabled_reason": null,
    "user_disabled": "[REDACTED]", "spend_limit_reached": false, "credits_ever_enabled": true,
    "daily": null, "weekly": null
  },
  "limits": [
    { "kind": "session", "group": "session", "percent": 11, "severity": "normal",
      "resets_at": "2026-09-03T02:40:00.401892+00:00", "scope": null, "is_active": false },
    { "kind": "weekly_all", "group": "weekly", "percent": 13, "severity": "normal",
      "resets_at": "2026-09-08T14:00:00.401917+00:00", "scope": null, "is_active": false },
    { "kind": "weekly_scoped", "group": "weekly", "percent": 24, "severity": "normal",
      "resets_at": "2026-09-08T14:00:00.402191+00:00",
      "scope": { "model": { "id": "[REDACTED]", "display_name": "[REDACTED]" }, "surface": null },
      "is_active": true }
  ],
  "spend": {
    "used": { "amount_minor": 0, "currency": "USD", "exponent": 2 },
    "limit": null, "percent": 0, "severity": "normal", "enabled": false,
    "disabled_reason": null, "cap": null, "balance": null, "auto_reload": null,
    "disclaimer": "Usage credits cover you when you hit your plan limits. [Learn more](https://support.claude.com/articles/12429409)",
    "can_purchase_credits": false, "can_toggle": false
  },
  "member_dashboard_available": false
}
```

Findings:

- **Weekly-window field names, current shape**: there are two parallel representations of the same weekly number:
  - Top-level `seven_day.utilization` (float, e.g. `13.0`) + `seven_day.resets_at` (ISO-8601 string with offset, not Unix epoch).
  - `limits[]` entries with `kind: "weekly_all"` (aggregate weekly, matches `seven_day.utilization`/`.resets_at` exactly) and `kind: "weekly_scoped"` (a weekly window narrowed to `scope.model`, e.g. the Fable-only bucket the statusline script pulls with `oauth_limit weekly_scoped fable`) — each with `percent` (int) and `resets_at`.
  - The ticket's expected keys `seven_day_opus` / `seven_day_sonnet` / `seven_day_cowork` etc. **exist as top-level keys but are `null`** in this response — the API appears to have moved (or is moving) the "weekly windows for other coverage" concept into the generic `limits[]` array's `scope`-tagged `weekly_scoped` entries rather than one static key per model/surface. Treat the top-level per-model `seven_day_*` keys as legacy/unpopulated and read `limits[]` for the current per-scope weekly data.
  - `five_hour` mirrors `seven_day`'s shape and is paralleled by `limits[]` `kind: "session"`.
- **Utilization scale**: **0–100 percent**, not a 0–1 fraction. Both representations agree: `five_hour.utilization`/`seven_day.utilization` are floats on a 0–100 scale (`11.0`, `13.0`), and `limits[].percent` are integers on the same 0–100 scale (`11`, `13`, `24`). This matches `statusline-command.sh`'s own `oauth_limit` output, which it feeds straight into `pace_seg`/`bar_cells` as a 0–100 percentage (`statusline-command.sh:87-112` for the bar renderer, `statusline-command.sh:122-145` for `pace_seg`).
- **`resets_at` format differs from the stdin `rate_limits` fields**: here it's an ISO-8601 string with a UTC offset (`"2026-09-08T14:00:00.401917+00:00"`); the statusline stdin's `rate_limits.*.resets_at` is Unix epoch seconds (confirmed in the official docs, §6). `statusline-command.sh`'s `parse_ts()` (`statusline-command.sh:56-68`) explicitly handles both shapes for this reason.
- **Other structurally relevant fields**: `limits[].is_active` (bool — whether that window is the one currently governing throttling), `limits[].severity` (string, e.g. `"normal"`), `limits[].scope.surface` (nullable, presumably distinguishes CLI vs. web/app surface for a scoped limit), an `extra_usage` block (pay-as-you-go credit balance, currently disabled on this account) and a `spend` block (usage-credit balance/cap, also disabled). Several top-level keys (`tangelo`, `iguana_necktie`, `omelette_promotional`, `nimbus_quill`, `cinder_cove`, `amber_ladder`, `juniper_tide`) are present but null/near-empty and appear to be internal product/feature codenames not documented anywhere public — treat them as forward-compatibility placeholders, not stable API surface to build against.
- Caveat: this is one account's snapshot on one date. The presence of `null` for most `seven_day_*` and codename keys likely reflects this account's plan/feature flags, not the absence of those fields from the schema entirely.

## 3. Is `src/ohm/statusline_relay.py` session-bound, or does it poll independently?

Source: `src/ohm/statusline_relay.py` in this repo (upstream oh-my-wrist).

**It is strictly session-bound, pull-based, and event-driven — it never polls or persists usage independent of an active Claude Code session.**

Evidence:

- `statusline_relay.py:1-19` (module docstring): "Claude Code pipes a JSON blob on stdin to the configured statusLine command... We extract the `/usage`-equivalent quota percentages and forward them to the BLE daemon." This confirms the relay is *itself* the statusLine command Claude Code invokes — it has no independent entry point, timer, or scheduler of its own.
- `main()` (`statusline_relay.py:92-110`) does exactly three things per invocation: read one blob from stdin (`statusline_relay.py:93`, calling `_read_stdin()` at `statusline_relay.py:37-41`), parse+forward it (`statusline_relay.py:94-107`), and chain to the user's previous statusLine command (`statusline_relay.py:109`, `_chain_previous()` at `statusline_relay.py:70-89`). There is no loop, no `while True`, no scheduled task, no HTTP client — the process runs once per invocation and exits (`sys.exit(0)`, `statusline_relay.py:110`).
- `_parse_usage()` (`statusline_relay.py:55-67`) reads *only* `data["rate_limits"]` from the stdin JSON that Claude Code itself supplies — it never calls out to `/api/oauth/usage` or any other network endpoint. `grep -rl "oauth/usage\|claudeAiOauth\|find-generic-password" --include='*.py' .` over this repo returns zero matches — nothing in this codebase talks to the OAuth usage endpoint or reads the Keychain item at all.
- `_extract_pct()`/`_parse_usage()` pull exactly `rate_limits.five_hour.used_percentage` → `meta["s"]` and `rate_limits.seven_day.used_percentage` → `meta["w"]` (`statusline_relay.py:10-16, 44-52, 62-65`), with a `-1` sentinel when absent (`statusline_relay.py:16, 48-52`).
- Downstream, `src/ohm/ble_daemon.py` only updates its in-memory `self._usage` dict when it receives a `CanonicalIpcMessage` with `canonical_event == "usage"` over its local IPC socket (`ble_daemon.py:1763-1771`) — that message originates solely from `statusline_relay.py:96-107`'s `send_to_daemon(msg)` call. The daemon's own periodic loops (`ble_daemon.py:153` keepalive intervals, `ble_daemon.py:926-972` a 2s connection-state poller, `ble_daemon.py:1704-1728` a stats-push interval) are all about BLE connection/advertising bookkeeping — none of them re-fetch or refresh usage data; they only re-push whatever `self._usage` last held (`ble_daemon.py:1567-1587`, `_push_usage`).

**Consequence**: when no Claude Code session is open (nothing invoking the configured statusLine command), `statusline_relay.py` never runs, no usage message reaches the daemon's IPC socket, and `ble_daemon.py`'s `self._usage` simply holds whatever it last received (or the `{"s": -1, "w": -1}` startup default — `ble_daemon.py:298-301` — if the daemon itself was restarted since the last session). The watch would show stale/absent data indefinitely between sessions with the current upstream design. This directly confirms the map.md gap: "session-independent usage fetch" is a real gap upstream doesn't close — a launchd-daemon-only usage path is not something this repo currently has; it would need to be *added* (e.g. a small poller that does what `statusline-command.sh` does, gated appropriately — see §5 for why that needs care).

## 4. Can a launchd daemon read the Keychain item without a GUI/biometric prompt?

The mechanism observed is `security find-generic-password -s "Claude Code-credentials" -w` (`statusline-command.sh:217`) — a plain, non-`-A` Keychain read via the system `/usr/bin/security` CLI, no biometry-specific API involved.

**Reasoning (not independently tested against a real launchd job — this is inference from what was directly observed):**

- This exact command was run non-interactively in this research session (a background Bash tool call, with no ability to click through a GUI authorization dialog) and it returned the token silently and immediately — no prompt was shown, no hang, no timeout. If the Keychain item's ACL did not already trust the calling binary, macOS would have shown a modal "`security` wants to use your confidential information stored in 'Claude Code-credentials'..." dialog and the call would have blocked; it didn't.
- macOS Keychain ACL trust is evaluated per calling-binary (code signature/path), not per parent process or session type. `/usr/bin/security` is a fixed system binary — whatever trust decision already lets it read this item silently from an interactive shell applies identically when the same binary is invoked by a **LaunchAgent** (a job in `~/Library/LaunchAgents`, which — per this machine's own convention, `~/CLAUDE.md`: "Scheduling: launchd plists in `~/Library/LaunchAgents`" — runs *inside* the logged-in user's GUI session, not as a detached system daemon).
- The login keychain unlocks automatically at login (default macOS behavior when the login-keychain password matches the account password), independent of whether a Terminal window happens to be open. So a LaunchAgent that starts after login, in the background, with no window of its own, should be able to run this same `security find-generic-password -w` call and get the token silently too — **provided** it is a LaunchAgent, not a LaunchDaemon. A LaunchDaemon (`/Library/LaunchDaemons`, root context, no association with any logged-in user's session or keychain) would not have access to the user's login keychain at all and should be expected to fail outright, not merely prompt.
- Caveat / what would break this: if the ACL trust was established implicitly the first time `security` accessed this item under conditions that granted it via a UI dialog the user already clicked through (e.g., "Always Allow") at some point in the past, that decision persists per-binary and should carry over to a LaunchAgent invocation. But this was inferred from a single successful call, not from inspecting the item's ACL/trusted-app list directly (`security dump-keychain` reveals partition IDs but wasn't run here to avoid unnecessary broad keychain enumeration beyond what the ticket scoped). If the ACL instead trusts a narrower thing (e.g., the exact absolute path of the shell that was interactively used, or requires re-confirmation whenever the calling app's code signature changes), behavior could differ for a differently-signed or differently-invoked launchd job. **Confidence: MODERATE** that a `com.nelson.*` LaunchAgent calling the identical command would succeed silently; this should be verified once such a LaunchAgent actually exists (first real run will reveal it immediately — either the token comes back or the job hangs/errors).

**Recommendation**: build the daemon as a `~/Library/LaunchAgents` LaunchAgent (per this machine's existing convention), not a LaunchDaemon, and have it invoke the exact same `security find-generic-password -s "Claude Code-credentials" -w` command `statusline-command.sh` already uses (proven code path, already trusted) rather than any alternative Keychain API — do not switch to `-A` (which would broaden the ACL to allow *any* application, weakening the item's protection) unless the plain call is empirically shown to prompt.

## 5. Recommended poll interval and anti-429 measures

**This section is a mix of an external report (GitHub issues) and reasoning from the statusline script's own caching — labeled per point.**

- **External report — the endpoint rate-limits aggressively and can get stuck:**
  - [`anthropics/claude-code` issue #30930](https://github.com/anthropics/claude-code/issues/30930), "OAuth usage API (`/api/oauth/usage`) returns persistent 429 rate limit": reports 429s with `retry-after: 0` persisting across 30s, 60s, and 120s polling intervals, for Claude Max subscribers with valid tokens; attributes the trigger to third-party statusline tools polling every 30–60s; status **open**, no maintainer fix as of the report. It proposes (a) raising the rate limit since this is lightweight metadata not inference, (b) returning a real `retry-after` value, and (c) — the option it flags as preferred — folding usage data directly into the statusLine stdin JSON so tools stop calling this endpoint separately at all (referencing feature request `anthropics/claude-code#29604`, not independently verified here due to a fetch timeout).
  - [`anthropics/claude-code` issue #31021](https://github.com/anthropics/claude-code/issues/31021), "OAuth usage API (`/api/oauth/usage`) returns persistent 429 rate limit": similar report; **closed as "not planned"** by Anthropic, meaning there is no committed fix or documented safe polling interval from Anthropic — the endpoint is explicitly unofficial/reverse-engineered (also consistent with it being entirely absent from the official statusline docs — see §6) and callers should not assume any stable rate limit contract.
  - Neither issue found a polling interval that reliably avoids 429s once triggered — reports describe getting "stuck in a permanent 429 loop" rather than a clean backoff-and-recover pattern.
- **Reasoned from `statusline-command.sh`'s own behavior (this call site, not an external report):** the script self-imposes a **60-second minimum interval** between fetch attempts via its cache-freshness gate (`statusline-command.sh:216`), and *this research's own single live call* was rate-limited (429) after only ~172 seconds since the prior successful fetch — i.e., well outside that 60s window, from what should be a single well-behaved caller. That is direct, first-party evidence (not the GitHub reports) that even conservative single-consumer polling at intervals looser than 60s can still hit 429s, likely because the limiter accounts for calls from *all* surfaces touching this account's token (the `/usage` CLI command, any other statusline instances, this research call, etc.), not just one script's own request rate.

**Recommendation for a session-independent daemon:**

1. **Don't add a second independent poller.** The single biggest anti-429 measure is to not multiply callers against the same token. Prefer reading `statusline-command.sh`'s existing cache file (`${TMPDIR:-/tmp}/claude-statusline-oauth-usage-$(id -u).json`) when it's fresh, rather than firing a parallel fetch — this repo's daemon and the statusline script would then share one fetch budget instead of two independent ones competing for the same rate limit.
2. If the daemon must fetch on its own (e.g. to cover the case where no Claude Code session has run recently enough to keep that cache warm), use an interval **substantially looser than 60s** — this research's own evidence suggests even ~170s isn't safe from certain 429s under real-world contention, and neither GitHub report found a "safe" polling interval at 30–120s. A conservative starting point is **5 minutes**, treating any 429 as "leave the last-known value on the watch" rather than retrying immediately (no aggressive retry-after-based retry — the reports note `retry-after: 0` is misleading and doesn't actually mean immediate retry succeeds).
3. Match `statusline-command.sh`'s own resilience pattern: keep serving the last successfully cached value for a stale-tolerance window (it uses 30 minutes, `statusline-command.sh:227-228`) before showing the window as unknown/stale on the watch, rather than treating every 429 as an outage.
4. Send the same `anthropic-beta: oauth-2025-04-20` header and `Authorization: Bearer` token retrieval path already proven to work (§1, §4) — there's no evidence from either report that headers matter to the 429 behavior (both reports describe rate limiting despite correct headers/valid tokens), so this is about not deviating from a known-working request shape, not about avoiding the 429.

## 6. Web sources

- Official Claude Code statusline docs: [`https://code.claude.com/docs/en/statusline`](https://code.claude.com/docs/en/statusline) (canonical; `https://docs.claude.com/en/docs/claude-code/statusline` 301-redirects here). Confirms: `rate_limits.five_hour`/`rate_limits.seven_day`/`rate_limits.spend_limit`, each with `used_percentage` (0–100) and `resets_at` (**Unix epoch seconds**, unlike the OAuth endpoint's ISO-8601 strings); `rate_limits` "appears only for Claude.ai Pro and Max subscribers... and only after the first API response in the session," and each window is dropped once its `resets_at` passes. Script re-runs are event-driven (new assistant message, `/compact`, permission-mode change, a rate-limit window's `resets_at` passing, an optional `refreshInterval` timer) — not a continuous poll. **The page contains zero mentions of `/api/oauth/usage`, `oauth/usage`, or any equivalent endpoint** (verified by grepping the fetched page text) — confirming this endpoint is undocumented/reverse-engineered, not an official public API.
- [`anthropics/claude-code` #30930 — "OAuth usage API (`/api/oauth/usage`) returns persistent 429 rate limit"](https://github.com/anthropics/claude-code/issues/30930) — open, no fix; describes 429s at 30/60/120s polling intervals and proposes folding usage into statusline stdin instead.
- [`anthropics/claude-code` #31021 — "OAuth usage API (`/api/oauth/usage`) returns persistent 429 rate limit"](https://github.com/anthropics/claude-code/issues/31021) — closed as "not planned."
- Feature request referenced by #30930 as the preferred long-term fix: `anthropics/claude-code#29604` (fetch attempt timed out during this research; not independently confirmed — cite with that caveat if referenced further).
