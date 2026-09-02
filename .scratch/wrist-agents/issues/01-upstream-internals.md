# Upstream internals: how oh-my-wrist moves events and usage to the watch

Type: research
Status: open
Blocked by: none

## Question

How does upstream oh-my-wrist work end to end, and what exactly must change to add a Codex provider, weekly-only usage for two providers, session-independent usage fetch, and job-level finished alerts?

Read the fork at `~/dev/oh-my-wrist` and produce an architecture note covering:

1. Event model: `src/ohm/provider_types.py` (`CanonicalEvent`), `src/ohm/protocol.py` (IPC wire format, BLE GATT UUIDs, characteristics), `src/ohm/ble_daemon.py` (what is sent over BLE, how often, size limits), `src/ohm/history_encoder.py`.
2. Providers: `src/ohm/adapters/claude_adapter.py`, `opencode_adapter.py`, `hook_relay.py`, `opencode/plugins/oh_my_wrist_opencode.ts`. What a new provider needs to implement. Whether the watch app has provider-specific code (`garmin/source/StatsModel.mc`, `IconCatalog.mc`, `Palette.mc`).
3. Usage path: `src/ohm/statusline_relay.py` to `garmin/source/UsageModel.mc` and `OhMyWristUsageView.mc`. Where the 5h/7d numbers originate, whether usage only updates while a Claude session is running, and what the wire payload looks like.
4. Haptics: which events vibrate, patterns, and how "session done" / "agent completion" are detected.
5. Install: `src/ohm/install.py` and `src/ohm/platform/macos.py`. Exactly what it writes to `~/.claude/settings.json` (hooks, statusLine chaining) and the launchd plist it registers. Flag anything that would collide with the existing hooks in that file.
6. Build and test: `tools/build_garmin.sh`, `garmin/monkey.jungle`, `garmin/manifest.xml` (confirm `epix2pro51mm`), `tests/`. Whether the Connect IQ simulator can exercise BLE.
7. Upstream branches `bugfix/mac-os-ble-disc` and `feature/release-pypi`: anything relevant to macOS BLE stability.

Deliver `docs/research/upstream-internals.md` with the note plus a "what to touch" list per gap. Do not modify source files.
