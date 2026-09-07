# Watch screens: weekly usage for two providers, and the alert card

Type: prototype
Status: resolved
Blocked by: 01, 04

## Question

How should the usage screen and a job-finished alert look on a 454x454 AMOLED, given upstream's terminal aesthetic?

Prototype two or three mockups (HTML or SVG at 454x454) of a weekly-only usage screen showing Claude and Codex side by side with reset times, and of the alert rendering (job name, outcome, haptic pattern). React to them before anything is coded in Monkey C.

## Answer

Resolved 2026-09-07 by construction. The usage screen was rebuilt as two weekly bars, Claude and Codex, fed by a new `PROVIDER_USAGE_CHAR_UUID` payload `{"c":..,"x":..}` (`garmin/source/UsageModel.mc`, `OhMyWristUsageView.mc`), with the legacy `{"s","w"}` payload kept for the store build. No separate alert card exists: a job outcome is a haptic pattern plus a history row. Reset times are not shown. Mockups were not produced because the screen already exists; any redesign is a follow-on once it has been seen on the wrist.
