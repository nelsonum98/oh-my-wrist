# CONTEXT.md - wrist-agents glossary

Terms used across the map, tickets, and code in this fork. Glossary only; no implementation detail.

- **Provider**: a coding-agent harness the daemon receives events from. Upstream: Claude Code, OpenCode. This fork adds Codex.
- **Weekly window**: a provider subscription's 7-day rate-limit bucket, expressed as percent used and reset time. The only usage figure this effort shows. The 5-hour window is out of scope.
- **Job**: a unit of agent work whose completion Nelson wants on his wrist. Which sessions count as jobs, per provider, is decided in tickets 05 and 06.
- **Outcome**: the terminal state of a job: done, needs input, or failed.
- **Alert**: a haptic plus on-screen card the watch shows when a job reaches an outcome.
- **Daemon**: the Mac-side process that ingests provider events and usage and streams them to the watch over BLE.
