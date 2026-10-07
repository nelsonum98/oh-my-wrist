# Map: wrist-agents

Label: wayfinder:map
Repo: ~/dev/personal/oh-my-wrist (fork of yazon/oh-my-wrist, upstream remote `upstream`)

## Destination

A locked spec plus a proven data path, handed to a Codex executor to build: this fork of oh-my-wrist running on Nelson's epix Pro (Gen 2) 51 mm, showing (a) the **weekly** usage window for Claude Max and for the ChatGPT/Codex subscription, and (b) **job-finished alerts** carrying job name and outcome (done / needs input / failed) from Claude Code background jobs and Codex jobs. The map is done when nothing is left to decide before an executor builds it.

## Notes

- Hardware: Garmin epix Pro (Gen 2) 51 mm (`epix2pro51mm`), AMOLED 454x454, Connect IQ Generic BLE supported. Phone: iPhone. Transport: direct BLE Mac-to-watch as upstream does; no phone, no HTTP, no tunnel.
- Upstream already ships: Claude Code hook adapter, OpenCode plugin, a Claude usage screen (5h + 7d bars fed by a statusLine relay), haptics, launchd service. The gaps are a Codex provider, weekly-only usage, session-independent usage fetch, and job-level alerts.
- Existing assets on this Mac: `~/.claude/statusline-command.sh` already fetches the Claude OAuth usage endpoint; `~/.codex/config.toml` `notify` is occupied by the Computer Use client; `~/.codex/hooks.json` exists; `herdr` tracks agent state across panes. Both usage endpoints are unofficial and may change.
- Build: `eval "$(mise env -s zsh)"` then `monkeyc -d epix2pro51mm -f garmin/monkey.jungle -o build/garmin/oh-my-wrist-epix2pro51mm.prg -y ~/.Garmin/developer_key.der -r` with the SDK 9.2.0 `bin` on PATH. Tests: `uv sync --extra dev && uv run pytest`.
- Skills every session should consult: `codex-orchestrator` (build handoff), `tool-onboarding` (before any new dependency), `grilling` + `domain-modeling` for grilling tickets. Glossary in `CONTEXT.md`.
- Standing preferences: `uv` for Python, launchd label `com.nelson.<name>`, never hardcode secrets, back up `~/.claude/settings.json` before any tool patches it. Acceptance gate for build tickets later: watch shows it, not just tests pass.
- Ticket status/claim/blocking conventions: `docs/agents/issue-tracker.md`.

## Decisions so far

<!-- one line per resolved ticket: [title](issues/NN-slug.md) - gist -->

- [Upstream internals: how oh-my-wrist moves events and usage to the watch](issues/01-upstream-internals.md) - providers hardcoded two-way on daemon and watch; usage fetch is session-gated but push is not; Notification type and SubagentStop unused; statusLine patch clobbers settings; BLE untestable in simulator.
- [Codex weekly window: which programmatic source works on this Mac](issues/02-codex-weekly-window.md) - use `codex app-server` `account/rateLimits/read`; pick the window with `windowDurationMins == 10080`; headless OK, ~1.5 s per call; REST endpoint is fallback only.
- [Claude weekly window: verify the OAuth usage endpoint and headless access](issues/03-claude-weekly-window.md) - fields `seven_day.utilization` or `limits[kind=weekly_all].percent`, scale 0-100; endpoint 429s hard with no safe interval, so share the statusline cache and poll no faster than 5 min; Keychain read likely silent from a LaunchAgent (MODERATE).
- [Install upstream oh-my-wrist unchanged and prove the BLE link to the epix](issues/04-install-upstream-as-is.md) - fork installed and running under launchd since 2026-09-07; BLE link proven by daemon log; watch still on the store build until the fork `.prg` is sideloaded.
- [Codex job-finished events: where they come from](issues/05-codex-job-finished-source.md) - `~/.codex/hooks.json` Stop and SubagentStop run `oh-my-wrist codex-hook`; notify key untouched; no job name or outcome yet.
- [Watch screens: weekly usage for two providers, and the alert card](issues/07-watch-screens-prototype.md) - usage screen rebuilt as two weekly bars over a new payload; no alert card, outcomes are haptic plus history row.
- [Usage acquisition: session-independent polling vs statusLine relay](issues/08-usage-acquisition-strategy.md) - daemon polls every 5 min; Claude shares the statusline cache; Codex via app-server; failed reads keep the last value for 30 min (fixed 2026-09-07).
- [Sideload the fork build onto the watch](issues/12-sideload-fork-build.md) - fork has its own app id; build with `tools/build_garmin.sh release`, push with `tools/sideload_mtp.py` (libmtp with explicit storage and folder ids); store copy to be removed by hand in the phone app.

## Not yet specified

- **herdr as a unified event source.** Not used by the build; still a candidate if per-harness hooks prove too noisy (ticket 06).
- **Daemon lifecycle.** Now `com.nelson.oh-my-wrist` LaunchAgent, editable install from this repo. Open: whether to move the plist under the automations repo's config.
- **Out-of-range behaviour.** What the watch shows when the Mac is out of BLE range (stale marker, last-updated time) and whether alerts queue.
- **OpenCode surface.** Whether to keep, ignore, or remove OpenCode support in the fork.

## Out of scope

- 5-hour rate-limit windows. Weekly only, per Nelson 2026-09-03.
- Any phone or cloud relay, tunnel endpoint, or Connect IQ HTTP polling route. BLE direct only.
- Token or dollar spend; only window utilization.
- Showing agent usage on the custom watch face (separate effort at `~/dev/personal/epix-face`).
- Publishing the fork to the Connect IQ store; sideload or private build only.
- Android.
