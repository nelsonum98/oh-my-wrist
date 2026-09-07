# Claude Code job-finished semantics

Type: grilling
Status: open
Blocked by: 01, 04

## Question

Which Claude Code sessions count as a "job" for wrist alerts, where does the job name come from, how is outcome (done / needs input / failed) derived, and how does this coexist with upstream's existing session-done haptic?

Inputs: background jobs already emit `result:` / `needs input:` / `failed:` lines that a classifier tracks; interactive sessions probably should not buzz on every Stop. Decide the hook set (Stop, SubagentStop, Notification) and the dedupe rule.

Research 01 found upstream ignores `notification_type` and `SubagentStop`; re-verify the live hook contract before deciding. Also weigh `herdr` as the source.

Update 2026-09-07. Facts now in hand: Claude Code fires `Notification` with `notification_type` in {`agent_completed`, `agent_needs_input`, `permission_prompt`, `idle_prompt`, ...}; `Stop` carries no interactive-vs-background flag; `SubagentStop` carries `agent_id` and `agent_type`; `TaskCompleted` also exists. The fork currently buzzes on every `Stop` (0x02), every `SubagentStop` (0x04), and every `PreToolUse` matching a broad destructive regex (0x03): about 260 haptics in the 17:00 hour on 2026-09-07, the 0x03 ones triggered by `git log --format=` and `rm -f` in ordinary commands. The decision is the alert policy per event, per provider, and how a job gets a name.
