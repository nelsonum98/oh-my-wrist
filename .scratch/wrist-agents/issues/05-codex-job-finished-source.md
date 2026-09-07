# Codex job-finished events: where they come from

Type: grilling
Status: resolved
Blocked by: 01, 02

## Question

How does the fork learn that a Codex job finished, with a job name and outcome, given that `~/.codex/config.toml` `notify` is already occupied by the Computer Use client?

Options to decide between: a wrapper script that chains the existing notify target and forwards to the daemon; `~/.codex/hooks.json` (exists on this Mac, mirrors the security hooks); tailing session JSONL; or `codex app-server` events. Also decide what a "job" is on the Codex side (a `codex exec` dispatch via `~/.local/bin/codex-dispatch`? an app-server thread?), where its name comes from, and how outcome is derived.

Also weigh `herdr` as the event source instead of per-harness hooks (research 01 suggests it could cover both providers).

## Answer

Resolved 2026-09-07 by construction (commits f1e2d1a, 85a4fae). Codex events come from `~/.codex/hooks.json` `Stop` and `SubagentStop` entries running `oh-my-wrist codex-hook` (`src/ohm/codex_hook_relay.py`), sharing Claude's hook envelope and tagged `provider=codex`. The occupied `notify` key is untouched. A Codex "job" is currently any turn end (`Stop` -> session_stop, haptic 0x02) or subagent end (`SubagentStop` -> job_done, haptic 0x04); no name or outcome is derived yet. herdr was not used. Whether every Codex turn should buzz is ticket 06's question.
