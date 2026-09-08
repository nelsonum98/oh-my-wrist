# Upstream internals: how oh-my-wrist moves events and usage to the watch

Type: research
Status: resolved
Blocked by: none

## Question

How does upstream oh-my-wrist work end to end, and what exactly must change to add a Codex provider, weekly-only usage for two providers, session-independent usage fetch, and job-level finished alerts?

Read the fork at `~/dev/oh-my-wrist` and produce an architecture note covering:

1. Event model: `src/ohm/provider_types.py` (`CanonicalEvent`), `src/ohm/protocol.py` (IPC wire format, BLE GATT UUIDs, characteristics), `src/ohm/ble_daemon.py` (what is sent over BLE, how often, size limits), `src/ohm/history_encoder.py`.
2. Providers: `src/ohm/adapters/claude_adapter.py`, `hook_relay.py`, and `opencode/plugins/oh_my_wrist_opencode.ts`. What a new provider needs to implement. Whether the watch app has provider-specific code (`garmin/source/StatsModel.mc`, `IconCatalog.mc`, `Palette.mc`).
3. Usage path: `src/ohm/statusline_relay.py` to `garmin/source/UsageModel.mc` and `OhMyWristUsageView.mc`. Where the 5h/7d numbers originate, whether usage only updates while a Claude session is running, and what the wire payload looks like.
4. Haptics: which events vibrate, patterns, and how "session done" / "agent completion" are detected.
5. Install: `src/ohm/install.py` and `src/ohm/platform/macos.py`. Exactly what it writes to `~/.claude/settings.json` (hooks, statusLine chaining) and the launchd plist it registers. Flag anything that would collide with the existing hooks in that file.
6. Build and test: `tools/build_garmin.sh`, `garmin/monkey.jungle`, `garmin/manifest.xml` (confirm `epix2pro51mm`), `tests/`. Whether the Connect IQ simulator can exercise BLE.
7. Upstream branches `bugfix/mac-os-ble-disc` and `feature/release-pypi`: anything relevant to macOS BLE stability.

Deliver `docs/research/upstream-internals.md` with the note plus a "what to touch" list per gap. Do not modify source files.

## Answer

Resolved 2026-09-03. Historical note: [docs/archive/upstream-internals.md](../../../docs/archive/upstream-internals.md).

- Providers are hardcoded two-way everywhere: `Provider` literal, one BLE characteristic per provider, `if/else` routing in `BleManager.mc`, two `StatsData` instances, a fixed four-view swipe stack. Codex needs a third slot on both sides.
- Usage is session-gated by construction: `statusline_relay.py` is the only source and runs only while Claude Code renders its statusLine. The daemon's push side already re-notifies cached values every 5-10 s, so only the fetch must become session-independent. Payload `{"s","w"}` is Claude-only and Codex usage is dropped in `ble_daemon.py`.
- Job alerts: the fork collapses every `Notification` hook to a generic idle alert and never reads `notification_type`; it never registers `SubagentStop`. Its "agent done" means a synchronous subagent returned, not a background job. MODERATE confidence on the hook-event inventory; re-verify against the live hooks doc before wiring.
- Install: hook patches are additive and do not collide with the security or herdr hooks. Real collision: `patch_claude_statusline()` overwrites the whole `statusLine` object and drops `padding` and `refreshInterval`. If a poller replaces the statusLine relay, this patch may be unnecessary.
- Build: `epix2pro51mm` is in the manifest; no Monkey C tests; the simulator cannot exercise BLE without an nRF52 board, so BLE testing is on-watch only.
- Both upstream side branches are already merged into main.
