"""Session-independent weekly usage readers for Claude and Codex.

The Claude reader shares the existing status-line cache and only refreshes it
after five minutes. The Codex reader asks the local ``codex app-server`` so it
never handles the Codex OAuth token directly.
"""

from __future__ import annotations

import json
import os
import select
import signal
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WEEK_MINUTES = 10_080
CLAUDE_FETCH_INTERVAL_SECONDS = 300
CLAUDE_STALE_TOLERANCE_SECONDS = 1_800
CODEX_STALE_TOLERANCE_SECONDS = 1_800



@dataclass(frozen=True)
class WeeklyUsage:
    used_percent: int
    resets_at: int | None = None


# Last successful Codex reading and when it was taken. A transient app-server
# failure (timeout, busy machine) should not blank the watch's Codex bar; the
# weekly window moves slowly enough that a reading up to 30 minutes old is
# still accurate to within a percent, matching the Claude reader's tolerance.
_codex_last_good: tuple[WeeklyUsage, float] | None = None


def _pct(value: Any) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return -1


def _parse_iso_epoch(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def parse_claude_usage(data: dict[str, Any]) -> WeeklyUsage | None:
    for limit in data.get("limits", []):
        if isinstance(limit, dict) and limit.get("kind") == "weekly_all":
            value = _pct(limit.get("percent"))
            if value >= 0:
                return WeeklyUsage(value, _parse_iso_epoch(limit.get("resets_at")))
    weekly = data.get("seven_day")
    if isinstance(weekly, dict):
        value = _pct(weekly.get("utilization"))
        if value >= 0:
            return WeeklyUsage(value, _parse_iso_epoch(weekly.get("resets_at")))
    return None


def _claude_cache_path() -> Path:
    return Path(tempfile.gettempdir()) / f"claude-statusline-oauth-usage-{os.getuid()}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _read_claude_token() -> str | None:
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            payload = json.loads(result.stdout)
            token = payload.get("claudeAiOauth", {}).get("accessToken")
            if isinstance(token, str) and token:
                return token
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass

    credentials = Path.home() / ".claude" / ".credentials.json"
    data = _read_json(credentials)
    token = (data or {}).get("claudeAiOauth", {}).get("accessToken")
    return token if isinstance(token, str) and token else None


def fetch_claude_weekly(now: float | None = None) -> WeeklyUsage | None:
    """Read the shared cache, refreshing at most once every five minutes."""
    current = time.time() if now is None else now
    cache = _claude_cache_path()
    cached = _read_json(cache)
    try:
        age = current - cache.stat().st_mtime
    except OSError:
        age = float("inf")

    if cached is not None and age < CLAUDE_FETCH_INTERVAL_SECONDS:
        return parse_claude_usage(cached)

    token = _read_claude_token()
    if token:
        request = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = json.loads(response.read())
            if isinstance(body, dict) and parse_claude_usage(body) is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                fd, temp_name = tempfile.mkstemp(dir=cache.parent, prefix=cache.name)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(body, handle, separators=(",", ":"))
                    os.chmod(temp_name, 0o600)
                    os.replace(temp_name, cache)
                finally:
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass
                return parse_claude_usage(body)
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass

    if cached is not None and age < CLAUDE_STALE_TOLERANCE_SECONDS:
        return parse_claude_usage(cached)
    return None


def fetch_codex_weekly(timeout: float = 8.0) -> WeeklyUsage | None:
    """Read the weekly Codex window, keeping the last good value on failure."""
    global _codex_last_good
    live = _fetch_codex_weekly_live(timeout)
    now = time.monotonic()
    if live is not None:
        _codex_last_good = (live, now)
        return live
    if _codex_last_good is not None:
        cached, taken_at = _codex_last_good
        if now - taken_at <= CODEX_STALE_TOLERANCE_SECONDS:
            return cached
        _codex_last_good = None
    return None


def _fetch_codex_weekly_live(timeout: float) -> WeeklyUsage | None:
    """Read the general weekly Codex window through the local app-server."""
    executable = shutil.which("codex")
    if executable is None:
        user_local = Path.home() / ".local" / "bin" / "codex"
        if user_local.is_file() and os.access(user_local, os.X_OK):
            executable = str(user_local)
    if executable is None:
        return None
    popen_kwargs: dict[str, Any] = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [executable, "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        **popen_kwargs,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    deadline = time.monotonic() + timeout
    try:
        initialize = {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "oh-my-wrist", "version": "0.1.3"}
            },
        }
        process.stdin.write(json.dumps(initialize) + "\n")
        process.stdin.flush()
        if _read_response(process.stdout, 1, deadline) is None:
            return None
        process.stdin.write(
            json.dumps(
                {"id": 2, "method": "account/rateLimits/read", "params": None}
            )
            + "\n"
        )
        process.stdin.flush()
        response = _read_response(process.stdout, 2, deadline)
        limits = (response or {}).get("result", {}).get("rateLimits", {})
        return parse_codex_rate_limits(limits)
    except (OSError, BrokenPipeError, ValueError):
        return None
    finally:
        _terminate_process_tree(process)


def _terminate_process_tree(process: subprocess.Popen) -> None:
    """Stop only the app-server process and children launched in its session."""
    if os.name == "posix":
        process_group = process.pid
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            return
        if process.poll() is None:
            process.wait(timeout=2)
        return

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _read_response(stream: Any, request_id: int, deadline: float) -> dict | None:
    while time.monotonic() < deadline:
        readable, _, _ = select.select([stream], [], [], min(0.25, deadline - time.monotonic()))
        if not readable:
            continue
        line = stream.readline()
        if not line:
            return None
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("id") == request_id:
            return value
    return None


def parse_codex_rate_limits(limits: dict[str, Any]) -> WeeklyUsage | None:
    """Select the seven-day window by duration, regardless of slot."""
    for name in ("primary", "secondary"):
        window = limits.get(name)
        if (
            isinstance(window, dict)
            and window.get("windowDurationMins") == WEEK_MINUTES
        ):
            value = _pct(window.get("usedPercent"))
            if value >= 0:
                reset = window.get("resetsAt")
                return WeeklyUsage(value, int(reset) if reset is not None else None)
    return None
