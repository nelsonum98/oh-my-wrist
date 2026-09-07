# Install upstream oh-my-wrist unchanged and prove the BLE link to the epix

Type: task
Status: resolved
Blocked by: none

## Question

Does upstream oh-my-wrist work as shipped on this Mac and this watch? Everything downstream assumes the BLE path is real; prove it before designing on top of it.

HITL checklist for Nelson (agent drives the Mac side):

1. Back up `~/.claude/settings.json` to `~/.claude/settings.json.bak-<date>` before anything patches it.
2. Install the daemon from the fork checkout: `cd ~/dev/oh-my-wrist && uv venv && uv pip install -e ".[dev]"`.
3. On the phone, install the oh-my-wrist watch app from the Connect IQ store (link in README) onto the epix Pro.
4. Run `oh-my-wrist install --provider claude`, then diff `~/.claude/settings.json` against the backup and confirm the security hooks and herdr hook survived.
5. Start the daemon in the foreground, open the watch app, run `python tools/check_connection.py` and confirm history rows, stats, and usage bars move on the watch.
6. Run one real Claude Code session and note what the watch shows at session end, and whether the existing 5h/7d bars populate.

Answer records: what worked, BLE stability on macOS, anything the security hooks blocked, and whether the upstream launchd service was registered (and its label).

## Answer

Resolved 2026-09-07 by evidence rather than by the checklist. The fork (not upstream as-is) was installed on 2026-09-07 with `uv tool install` (editable, pointing at the repo), registered as launchd `com.nelson.oh-my-wrist`, and the daemon log shows history frames, stats, and alerts pushed to a connected watch through the afternoon, so the BLE link is proven. Backups of `~/.claude/settings.json` and `~/.codex/hooks.json` are under `~/.oh-my-wrist/backups/`. The security hooks and the herdr SessionStart hook survived. statusLine was replaced by the oh-my-wrist relay with `padding` and `refreshInterval` preserved and the previous command chained (`~/.oh-my-wrist/prev_statusline.json`). Caveat: the watch still runs the Connect IQ store build; the fork's watch app (two-provider usage payload) was compiled at `build/garmin/oh-my-wrist-epix2pro51mm.prg` on 2026-09-07 and is not yet sideloaded.
