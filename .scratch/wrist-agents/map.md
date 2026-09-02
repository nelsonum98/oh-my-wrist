# Map: wrist-agents

Label: wayfinder:map
Repo: ~/dev/oh-my-wrist (fork of yazon/oh-my-wrist, upstream remote `upstream`)

## Destination

A locked spec plus a proven data path, handed to a Codex executor to build: this fork of oh-my-wrist running on Nelson's epix Pro (Gen 2) 51 mm, showing (a) the **weekly** usage window for Claude Max and for the ChatGPT/Codex subscription, and (b) **job-finished alerts** carrying job name and outcome (done / needs input / failed) from Claude Code background jobs and Codex jobs. The map is done when nothing is left to decide before an executor builds it.

## Notes

- Hardware: Garmin epix Pro (Gen 2) 51 mm (`epix2pro51mm`), AMOLED 454x454, Connect IQ Generic BLE supported. Phone: iPhone. Transport: direct BLE Mac-to-watch as upstream does; no phone, no HTTP, no tunnel.
- Upstream already ships: Claude Code hook adapter, OpenCode plugin, a Claude usage screen (5h + 7d bars fed by a statusLine relay), haptics, launchd service. The gaps are a Codex provider, weekly-only usage, session-independent usage fetch, and job-level alerts.
- Existing assets on this Mac: `~/.claude/statusline-command.sh` already fetches the Claude OAuth usage endpoint; `~/.codex/config.toml` `notify` is occupied by the Computer Use client; `~/.codex/hooks.json` exists; `herdr` tracks agent state across panes. Both usage endpoints are unofficial and may change.
- Skills every session should consult: `codex-orchestrator` (build handoff), `tool-onboarding` (before any new dependency), `grilling` + `domain-modeling` for grilling tickets. Glossary in `CONTEXT.md`.
- Standing preferences: `uv` for Python, launchd label `com.nelson.<name>`, never hardcode secrets, back up `~/.claude/settings.json` before any tool patches it. Acceptance gate for build tickets later: watch shows it, not just tests pass.
- Ticket status/claim/blocking conventions: `docs/agents/issue-tracker.md`.

## Decisions so far

<!-- one line per resolved ticket: [title](issues/NN-slug.md) - gist -->

- [Upstream internals: how oh-my-wrist moves events and usage to the watch](issues/01-upstream-internals.md) - providers hardcoded two-way on daemon and watch; usage fetch is session-gated but push is not; Notification type and SubagentStop unused; statusLine patch clobbers settings; BLE untestable in simulator.
- [Codex weekly window: which programmatic source works on this Mac](issues/02-codex-weekly-window.md) - use `codex app-server` `account/rateLimits/read`; pick the window with `windowDurationMins == 10080`; headless OK, ~1.5 s per call; REST endpoint is fallback only.

## Not yet specified

- **herdr as a unified event source.** `herdr` already tracks Claude Code and Codex session state on this Mac. It may be a cleaner source for "job finished" than per-harness hooks. Revisit once the internals research shows how upstream ingests events.
- **Coexistence with existing hooks.** Hook patches are additive (research 01). Remaining questions: the statusLine overwrite, and the occupied Codex `notify` key; both fold into tickets 05 and 08.
- **Daemon lifecycle.** Upstream registers its own launchd service; whether to keep that, rename it under `com.nelson.*`, or fold into the automations repo.
- **Out-of-range behaviour.** What the watch shows when the Mac is out of BLE range (stale marker, last-updated time) and whether alerts queue.
- **OpenCode surface.** Whether to keep, ignore, or remove OpenCode support in the fork.

## Out of scope

- 5-hour rate-limit windows. Weekly only, per Nelson 2026-09-03.
- Any phone or cloud relay, tunnel endpoint, or Connect IQ HTTP polling route. BLE direct only.
- Token or dollar spend; only window utilization.
- Showing agent usage on the custom watch face (separate effort at `~/dev/epix-face`).
- Publishing the fork to the Connect IQ store; sideload or private build only.
- Android.
