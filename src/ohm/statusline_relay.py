"""
statusline_relay.py — Claude Code statusLine entry point.

Claude Code pipes a JSON blob on stdin to the configured statusLine command
(see docs.anthropic.com/en/docs/claude-code/statusline).  We extract the
``/usage``-equivalent quota percentages and forward them to the BLE daemon,
then chain to the user's previously-configured statusLine so their display is
preserved.

Forwarded fields (rounded ints, -1 when absent):
    rate_limits.five_hour.used_percentage  → meta["s"]  (5-hour session)
    rate_limits.seven_day.used_percentage  → meta["w"]  (7-day week)

``rate_limits`` is only present for Claude.ai Pro/Max subscribers and only
after the first API response in a session; each window may be independently
absent — hence the -1 sentinel.

Must never block or crash Claude Code: always exits 0.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from ohm.protocol import CanonicalIpcMessage, send_to_daemon

# Saved original statusLine command, written by the installer when it takes
# over the statusLine setting.  Chained so the user's display is preserved.
_PREV_STATUSLINE_PATH = Path.home() / ".oh-my-wrist" / "prev_statusline"
_IPC_TIMEOUT_SECONDS = 0.25


def _read_stdin() -> str:
    try:
        return sys.stdin.read()
    except Exception:
        return ""


def _extract_pct(rate_limits: dict, window: str) -> int:
    """Return the rounded used_percentage for a window, or -1 if absent/null."""
    win = rate_limits.get(window)
    if not isinstance(win, dict):
        return -1
    pct = win.get("used_percentage")
    if not isinstance(pct, (int, float)):
        return -1
    return max(0, min(100, round(pct)))


def _parse_usage(raw: str) -> tuple[int, int]:
    """Extract (session_pct, week_pct) from the statusLine JSON; -1 when absent."""
    try:
        data = json.loads(raw) if raw.strip() else {}
        rate_limits = data.get("rate_limits")
        if not isinstance(rate_limits, dict):
            return -1, -1
        return (
            _extract_pct(rate_limits, "five_hour"),
            _extract_pct(rate_limits, "seven_day"),
        )
    except Exception:
        return -1, -1


def _chain_previous(raw: str) -> None:
    """Run the user's saved statusLine command and pass its stdout through."""
    try:
        command = _PREV_STATUSLINE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if not command:
        return
    try:
        result = subprocess.run(
            command,
            shell=True,
            input=raw,
            capture_output=True,
            text=True,
            timeout=5,
        )
        sys.stdout.write(result.stdout)
    except Exception:
        pass


def main() -> None:
    raw = _read_stdin()
    session_pct, week_pct = _parse_usage(raw)

    msg = CanonicalIpcMessage(
        provider="claude",
        provider_event="statusline",
        canonical_event="usage",
        ts=time.time(),
        meta={"s": session_pct, "w": week_pct},
    )

    try:
        asyncio.run(
            asyncio.wait_for(send_to_daemon(msg), timeout=_IPC_TIMEOUT_SECONDS)
        )
    except Exception:
        pass

    _chain_previous(raw)
    sys.exit(0)


if __name__ == "__main__":
    main()
