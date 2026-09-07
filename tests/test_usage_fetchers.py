from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock

from ohm.usage_fetchers import (
    fetch_claude_weekly,
    fetch_codex_weekly,
    parse_claude_usage,
    parse_codex_rate_limits,
)


def test_parse_claude_weekly_all() -> None:
    usage = parse_claude_usage(
        {
            "limits": [
                {"kind": "session", "percent": 90},
                {
                    "kind": "weekly_all",
                    "percent": 37,
                    "resets_at": "2026-09-08T14:00:00+00:00",
                },
            ]
        }
    )
    assert usage is not None
    assert usage.used_percent == 37
    assert usage.resets_at == 1788876000


def test_parse_codex_selects_weekly_secondary() -> None:
    usage = parse_codex_rate_limits(
        {
            "primary": {"usedPercent": 80, "windowDurationMins": 300},
            "secondary": {
                "usedPercent": 12,
                "windowDurationMins": 10080,
                "resetsAt": 1788996051,
            },
        }
    )
    assert usage is not None
    assert usage.used_percent == 12
    assert usage.resets_at == 1788996051


def test_codex_fetch_finds_user_local_binary_under_launchd_path(
    tmp_path, monkeypatch
) -> None:
    """The launchd PATH may omit ~/.local/bin; retain the durable fallback."""
    codex = tmp_path / ".local" / "bin" / "codex"
    codex.parent.mkdir(parents=True)
    codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    monkeypatch.setattr("ohm.usage_fetchers.Path.home", lambda: tmp_path)
    monkeypatch.setattr("ohm.usage_fetchers.shutil.which", lambda _: None)

    stdout = iter(
        [
            json.dumps({"id": 1, "result": {}}) + "\n",
            json.dumps(
                {
                    "id": 2,
                    "result": {
                        "rateLimits": {
                            "secondary": {
                                "usedPercent": 17,
                                "windowDurationMins": 10080,
                                "resetsAt": 1788996051,
                            }
                        }
                    },
                }
            )
            + "\n",
        ]
    )
    process = MagicMock()
    process.stdin = MagicMock()
    process.stdout.readline.side_effect = lambda: next(stdout, "")
    monkeypatch.setattr("ohm.usage_fetchers.select.select", lambda *args: ([1], [], []))
    popen = MagicMock(return_value=process)
    monkeypatch.setattr("ohm.usage_fetchers.subprocess.Popen", popen)
    monkeypatch.setattr("ohm.usage_fetchers._terminate_process_tree", lambda _: None)

    usage = fetch_codex_weekly()

    assert usage is not None and usage.used_percent == 17
    assert popen.call_args.args[0][0] == str(codex)


def test_claude_fetch_accepts_seven_day_response(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "usage.json"
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(
        {
            "seven_day": {
                "utilization": 37,
                "resets_at": "2030-01-01T00:00:00Z",
            }
        }
    ).encode()
    monkeypatch.setattr("ohm.usage_fetchers._claude_cache_path", lambda: cache)
    monkeypatch.setattr("ohm.usage_fetchers._read_claude_token", lambda: "unused")
    monkeypatch.setattr("ohm.usage_fetchers.urllib.request.urlopen", lambda *a, **k: response)

    usage = fetch_claude_weekly(now=1)

    assert usage is not None and usage.used_percent == 37
    assert json.loads(cache.read_text())["seven_day"]["utilization"] == 37


def test_codex_timeout_kills_only_owned_process_group(tmp_path, monkeypatch) -> None:
    child_pid_file = tmp_path / "child.pid"
    launcher = tmp_path / "codex"
    launcher.write_text(
        "#!/bin/sh\nsleep 60 &\necho $! > " + str(child_pid_file) + "\nwait\n"
    )
    launcher.chmod(0o755)
    monkeypatch.setattr("ohm.usage_fetchers.shutil.which", lambda _: str(launcher))
    monkeypatch.setattr("ohm.usage_fetchers._codex_last_good", None)

    assert fetch_codex_weekly(timeout=0.25) is None
    child_pid = int(child_pid_file.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("app-server child survived owned process-group cleanup")


def test_codex_keeps_last_good_value_on_transient_failure(monkeypatch) -> None:
    from ohm import usage_fetchers as u

    monkeypatch.setattr(u, "_codex_last_good", None)
    good = u.WeeklyUsage(41, 1_800_000_000)
    monkeypatch.setattr(u, "_fetch_codex_weekly_live", lambda timeout: good)
    assert u.fetch_codex_weekly() == good

    monkeypatch.setattr(u, "_fetch_codex_weekly_live", lambda timeout: None)
    assert u.fetch_codex_weekly() == good

    taken_at = u._codex_last_good[1]
    monkeypatch.setattr(
        u.time, "monotonic", lambda: taken_at + u.CODEX_STALE_TOLERANCE_SECONDS + 1
    )
    assert u.fetch_codex_weekly() is None
    assert u._codex_last_good is None

