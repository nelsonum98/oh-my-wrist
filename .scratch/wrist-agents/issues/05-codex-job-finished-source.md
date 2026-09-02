# Codex job-finished events: where they come from

Type: grilling
Status: open
Blocked by: 01, 02

## Question

How does the fork learn that a Codex job finished, with a job name and outcome, given that `~/.codex/config.toml` `notify` is already occupied by the Computer Use client?

Options to decide between: a wrapper script that chains the existing notify target and forwards to the daemon; `~/.codex/hooks.json` (exists on this Mac, mirrors the security hooks); tailing session JSONL; or `codex app-server` events. Also decide what a "job" is on the Codex side (a `codex exec` dispatch via `~/.local/bin/codex-dispatch`? an app-server thread?), where its name comes from, and how outcome is derived.
