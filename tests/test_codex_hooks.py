from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

import pytest

from ohm.hook_relay import relay_payload
from ohm.install import patch_codex_hooks, remove_codex_hooks


def test_codex_hooks_are_additive_and_do_not_touch_notify(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "security-hook"}],
                        }
                    ],
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "herdr-hook"}]}
                    ],
                }
            }
        )
    )
    with (
        patch("ohm.install.CODEX_HOOKS_PATH", hooks_path),
        patch("ohm.install.Path.home", return_value=tmp_path),
    ):
        patch_codex_hooks()
    result = json.loads(hooks_path.read_text())
    assert result["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "security-hook"
    assert result["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "herdr-hook"
    assert "SessionEnd" in result["hooks"]
    assert "SubagentStop" in result["hooks"]


def test_remove_codex_hooks_preserves_existing_hooks(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    with (
        patch("ohm.install.CODEX_HOOKS_PATH", hooks_path),
        patch("ohm.install.Path.home", return_value=tmp_path),
    ):
        patch_codex_hooks()
        data = json.loads(hooks_path.read_text())
        data["hooks"]["SessionEnd"].insert(
            0, {"hooks": [{"type": "command", "command": "keep-me"}]}
        )
        hooks_path.write_text(json.dumps(data))
        remove_codex_hooks()
    result = json.loads(hooks_path.read_text())
    assert result["hooks"]["SessionEnd"] == [
        {"hooks": [{"type": "command", "command": "keep-me"}]}
    ]


def test_hook_ipc_is_bounded_when_socket_stalls() -> None:
    async def stalled_send(_message) -> None:
        await asyncio.sleep(5)

    started = time.perf_counter()
    with (
        patch("ohm.hook_relay.send_to_daemon", side_effect=stalled_send),
        pytest.raises(TimeoutError),
    ):
        asyncio.run(
            relay_payload(
                {
                    "hook_event_name": "SessionEnd",
                    "session_id": "synthetic",
                    "cwd": "/tmp/project",
                },
                provider="codex",
            )
        )
    assert time.perf_counter() - started < 1
