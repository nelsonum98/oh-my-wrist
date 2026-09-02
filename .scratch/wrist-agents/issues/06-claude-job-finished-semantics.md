# Claude Code job-finished semantics

Type: grilling
Status: open
Blocked by: 01, 04

## Question

Which Claude Code sessions count as a "job" for wrist alerts, where does the job name come from, how is outcome (done / needs input / failed) derived, and how does this coexist with upstream's existing session-done haptic?

Inputs: background jobs already emit `result:` / `needs input:` / `failed:` lines that a classifier tracks; interactive sessions probably should not buzz on every Stop. Decide the hook set (Stop, SubagentStop, Notification) and the dedupe rule.

Research 01 found upstream ignores `notification_type` and `SubagentStop`; re-verify the live hook contract before deciding. Also weigh `herdr` as the source.
