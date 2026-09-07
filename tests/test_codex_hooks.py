from __future__ import annotations

import asyncio
import io
import json
import sys
import time
from unittest.mock import AsyncMock, patch

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
        first_patch = hooks_path.read_bytes()
        patch_codex_hooks()
        assert hooks_path.read_bytes() == first_patch
    result = json.loads(hooks_path.read_text())
    assert result["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "security-hook"
    assert result["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "herdr-hook"
    assert "Stop" in result["hooks"]
    assert "SubagentStop" in result["hooks"]
    assert "SessionEnd" not in result["hooks"]


def test_codex_hook_lifecycle_preserves_mixed_groups(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    own_handler = {
        "type": "command",
        "command": "/tmp/oh-my-wrist codex-hook",
        "timeout": 2,
    }
    keep_handler = {"type": "command", "command": "keep-me", "timeout": 9}
    untouched_group = {
        "matcher": "other",
        "statusMessage": "unchanged",
        "hooks": [{"type": "command", "command": "other-hook"}],
    }
    original = {
        "hooks": {
            "SessionEnd": [
                {
                    "matcher": "shared-session",
                    "statusMessage": "session metadata",
                    "hooks": [own_handler, keep_handler],
                },
                untouched_group,
            ],
            "Stop": [
                {
                    "matcher": "shared-stop",
                    "statusMessage": "stop metadata",
                    "hooks": [own_handler, keep_handler],
                }
            ],
        },
        "topLevelMetadata": {"preserve": True},
    }
    hooks_path.write_text(json.dumps(original))
    with (
        patch("ohm.install.CODEX_HOOKS_PATH", hooks_path),
        patch("ohm.install.Path.home", return_value=tmp_path),
    ):
        patch_codex_hooks()
        first_patch = hooks_path.read_bytes()
        patch_codex_hooks()
        assert hooks_path.read_bytes() == first_patch

        patched = json.loads(hooks_path.read_text())
        assert patched["topLevelMetadata"] == original["topLevelMetadata"]
        assert patched["hooks"]["SessionEnd"] == [
            {
                "matcher": "shared-session",
                "statusMessage": "session metadata",
                "hooks": [keep_handler],
            },
            untouched_group,
        ]
        assert patched["hooks"]["Stop"][0] == {
            "matcher": "shared-stop",
            "statusMessage": "stop metadata",
            "hooks": [keep_handler],
        }
        assert len(patched["hooks"]["Stop"]) == 2
        assert len(patched["hooks"]["SubagentStop"]) == 1

        remove_codex_hooks()
    result = json.loads(hooks_path.read_text())
    assert result["topLevelMetadata"] == original["topLevelMetadata"]
    assert result["hooks"]["SessionEnd"] == [
        {
            "matcher": "shared-session",
            "statusMessage": "session metadata",
            "hooks": [keep_handler],
        },
        untouched_group,
    ]
    assert result["hooks"]["Stop"] == [
        {
            "matcher": "shared-stop",
            "statusMessage": "stop metadata",
            "hooks": [keep_handler],
        }
    ]
    assert "SubagentStop" not in result["hooks"]


def test_patch_migrates_legacy_session_end_hook(tmp_path) -> None:
    hooks_path = tmp_path / ".codex" / "hooks.json"
    hooks_path.parent.mkdir()
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionEnd": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/tmp/oh-my-wrist codex-hook",
                                }
                            ]
                        },
                        {"hooks": [{"type": "command", "command": "keep-me"}]},
                    ]
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
    assert result["hooks"]["SessionEnd"] == [
        {"hooks": [{"type": "command", "command": "keep-me"}]}
    ]
    assert len(result["hooks"]["Stop"]) == 1


def test_codex_hook_returns_empty_json_response(capsys) -> None:
    from ohm import codex_hook_relay

    payload = json.dumps({"hook_event_name": "Stop", "session_id": "test"})
    relay = AsyncMock()
    with (
        patch.object(sys, "stdin", new=io.StringIO(payload)),
        patch.object(codex_hook_relay, "relay_payload", new=relay),
        pytest.raises(SystemExit) as exit_info,
    ):
        codex_hook_relay.main()
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "{}\n"
    relay.assert_awaited_once_with(json.loads(payload), provider="codex")


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
