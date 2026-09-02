# Install upstream oh-my-wrist unchanged and prove the BLE link to the epix

Type: task
Status: open
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
