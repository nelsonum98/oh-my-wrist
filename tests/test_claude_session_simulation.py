"""
test_claude_session_simulation.py — Full Claude Code session simulation.

Replays a realistic end-to-end working session through the new binary frame
protocol and asserts that every frame is well-formed, within byte limits,
carries the expected icon ID, and that the daemon's session flag transitions
correctly throughout the session.

Session scenario
----------------
A developer asks Claude Code to: start a session, read files, fetch docs,
edit code, run tests, write modules, update todos, wait for user input,
edit configs, build, and stop.

Each step fires PreToolUse → PostToolUse pairs (except Notification and Stop).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from ohm.adapters.claude_adapter import adapt_claude_hook
from ohm.history_encoder import decode_frame, encode_event
from ohm.icons import FLAG_SPINNER, IconId
from ohm.protocol import (
    CanonicalIpcMessage,
    HookEvent,
    MAX_FRAME_LEN,
    encode_message,
)


# ============================================================================
# Session event definitions
# ============================================================================


@dataclass
class SessionStep:
    description: str
    event: str
    tool_name: str | None = None
    tool_input: dict | None = None
    expected_icon: IconId | None = None  # must equal this icon
    expected_text_in: str | None = None  # must be a substring of frame text
    session_active_after: bool = True


SESSION = [
    SessionStep("Session starts", "SessionStart", expected_icon=IconId.GREEN_CIRCLE),
    SessionStep(
        "Read README.md",
        "PreToolUse",
        "Read",
        {"path": "/project/README.md"},
        expected_icon=IconId.EYE,
        expected_text_in="README.md",
    ),
    SessionStep(
        "README read complete", "PostToolUse", "Read", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Fetch API docs",
        "PreToolUse",
        "WebFetch",
        {"url": "https://docs.anthropic.com/"},
        expected_icon=IconId.GLOBE,
    ),
    SessionStep(
        "WebFetch complete", "PostToolUse", "WebFetch", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Read main app source",
        "PreToolUse",
        "Read",
        {"path": "/project/src/app.py"},
        expected_icon=IconId.EYE,
        expected_text_in="app.py",
    ),
    SessionStep(
        "app.py read complete", "PostToolUse", "Read", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Read database module",
        "PreToolUse",
        "Read",
        {"path": "/project/src/database.py"},
        expected_icon=IconId.EYE,
        expected_text_in="database.py",
    ),
    SessionStep(
        "database.py read complete",
        "PostToolUse",
        "Read",
        {},
        expected_icon=IconId.CHECK,
    ),
    SessionStep(
        "Edit main app",
        "PreToolUse",
        "Edit",
        {"path": "/project/src/app.py"},
        expected_icon=IconId.PENCIL,
        expected_text_in="app.py",
    ),
    SessionStep("Edit complete", "PostToolUse", "Edit", {}, expected_icon=IconId.CHECK),
    SessionStep(
        "npm install",
        "PreToolUse",
        "Bash",
        {"command": "npm install --save-dev typescript"},
        expected_icon=IconId.PLAY,
        expected_text_in="npm",
    ),
    SessionStep(
        "npm install complete", "PostToolUse", "Bash", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Run test suite",
        "PreToolUse",
        "Bash",
        {"command": "pytest tests/ -v"},
        expected_icon=IconId.PLAY,
        expected_text_in="pytest",
    ),
    SessionStep(
        "Tests complete", "PostToolUse", "Bash", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Edit failing test",
        "PreToolUse",
        "Edit",
        {"path": "/project/tests/test_database.py"},
        expected_icon=IconId.PENCIL,
    ),
    SessionStep(
        "Test edit complete", "PostToolUse", "Edit", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Run tests again",
        "PreToolUse",
        "Bash",
        {"command": "pytest tests/test_database.py"},
        expected_icon=IconId.PLAY,
        expected_text_in="pytest",
    ),
    SessionStep("Tests pass", "PostToolUse", "Bash", {}, expected_icon=IconId.CHECK),
    SessionStep(
        "Write utility module",
        "PreToolUse",
        "Write",
        {"path": "/project/src/utils/cache.py"},
        expected_icon=IconId.PENCIL,
        expected_text_in="cache.py",
    ),
    SessionStep(
        "Write complete", "PostToolUse", "Write", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Update todos",
        "PreToolUse",
        "TodoWrite",
        {"todos": []},
        expected_icon=IconId.CLIPBOARD,
    ),
    SessionStep(
        "Todo update complete",
        "PostToolUse",
        "TodoWrite",
        {},
        expected_icon=IconId.CHECK,
    ),
    SessionStep("Waits for confirmation", "Notification", expected_icon=IconId.PAUSE),
    SessionStep(
        "Edit config file",
        "PreToolUse",
        "Edit",
        {"path": "/project/config/settings.yaml"},
        expected_icon=IconId.PENCIL,
        expected_text_in="settings.yaml",
    ),
    SessionStep(
        "Config edit complete", "PostToolUse", "Edit", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Run production build",
        "PreToolUse",
        "Bash",
        {"command": "npm run build --production"},
        expected_icon=IconId.PLAY,
        expected_text_in="npm",
    ),
    SessionStep(
        "Build complete", "PostToolUse", "Bash", {}, expected_icon=IconId.CHECK
    ),
    SessionStep(
        "Session ends", "Stop", expected_icon=IconId.STOP, session_active_after=False
    ),
]


def _canonical(step: SessionStep):
    return adapt_claude_hook(
        HookEvent.model_validate(
            {
                "event": step.event,
                "tool_name": step.tool_name,
                "tool_input": step.tool_input,
            }
        )
    )


# ============================================================================
# Encoder simulation: every step produces a valid frame
# ============================================================================


class TestEncoderSimulation:
    def test_all_steps_produce_valid_frames(self):
        for step in SESSION:
            canonical = _canonical(step)
            frame = encode_event(canonical)
            assert len(frame) <= MAX_FRAME_LEN, (
                f"Step '{step.description}': frame {len(frame)} bytes "
                f"(limit {MAX_FRAME_LEN})"
            )
            decoded = decode_frame(frame)
            assert decoded is not None, f"Step '{step.description}': decode failed"

            if step.expected_icon is not None:
                assert decoded["icon"] == int(step.expected_icon), (
                    f"Step '{step.description}': expected icon {step.expected_icon!r}, "
                    f"got {decoded['icon']:#x}"
                )
            if step.expected_text_in is not None:
                assert step.expected_text_in in decoded["text"], (
                    f"Step '{step.description}': expected text containing "
                    f"{step.expected_text_in!r}, got {decoded['text']!r}"
                )

    def test_tool_start_frames_have_spinner_flag(self):
        for step in SESSION:
            if step.event != "PreToolUse":
                continue
            canonical = _canonical(step)
            decoded = decode_frame(encode_event(canonical))
            assert decoded["flags"] & FLAG_SPINNER, (
                f"Step '{step.description}': missing SPINNER flag"
            )

    def test_post_tool_use_always_check_icon(self):
        for step in SESSION:
            if step.event != "PostToolUse":
                continue
            canonical = _canonical(step)
            decoded = decode_frame(encode_event(canonical))
            assert decoded["icon"] == int(IconId.CHECK)

# ============================================================================
# Daemon simulation: replay through _push_event
# ============================================================================


def _make_daemon():
    mock_server = MagicMock()
    mock_server.get_characteristic.return_value = MagicMock()
    mock_server.update_value = MagicMock()
    with patch("ohm.ble_daemon.BlessServer", return_value=mock_server):
        from ohm.ble_daemon import BleDaemon

        daemon = BleDaemon()
        daemon._server = mock_server
        daemon._device_connected = True
        daemon._has_subscribers = True
        return daemon, mock_server


class TestDaemonSimulation:
    def _run_session(self):
        daemon, mock_server = _make_daemon()
        history = []
        for step in SESSION:
            canonical = _canonical(step)
            daemon._push_event(canonical, session_active=step.session_active_after)
            history.append(
                (step.description, bytes(daemon._last_frame), daemon._session_active)
            )
        return daemon, mock_server, history

    def test_all_frames_within_limit(self):
        _, _, history = self._run_session()
        for desc, frame, _ in history:
            assert len(frame) <= MAX_FRAME_LEN, (
                f"Step '{desc}': frame {len(frame)} bytes, limit {MAX_FRAME_LEN}"
            )

    def test_all_frames_decode(self):
        _, _, history = self._run_session()
        for desc, frame, _ in history:
            assert decode_frame(frame) is not None, (
                f"Step '{desc}': frame failed to decode"
            )

    def test_session_flag_cleared_after_stop(self):
        _, _, history = self._run_session()
        _, _, final_flag = history[-1]
        assert final_flag == b"\x00"

    def test_session_flag_active_during_session(self):
        _, _, history = self._run_session()
        for desc, _, flag in history[:-1]:
            assert flag == b"\x01", (
                f"Step '{desc}': session flag should be 0x01, got {flag!r}"
            )

    def test_update_value_called_per_step(self):
        daemon, mock_server, history = self._run_session()
        assert daemon._notify_queue.qsize() == len(SESSION)

    def test_final_frame_is_stop_icon(self):
        daemon, _, _ = self._run_session()
        decoded = decode_frame(bytes(daemon._last_frame))
        assert decoded["icon"] == int(IconId.STOP)

    def test_first_frame_is_session_start(self):
        _, _, history = self._run_session()
        first_desc, first_frame, _ = history[0]
        decoded = decode_frame(first_frame)
        assert decoded["icon"] == int(IconId.GREEN_CIRCLE), (
            f"First step '{first_desc}' icon {decoded['icon']:#x}"
        )


# ============================================================================
# Hook relay subprocess simulation
# ============================================================================


class TestHookRelaySubprocess:
    """Fire every session event through the hook_relay subprocess and verify exit 0."""

    @pytest.mark.parametrize("step", SESSION, ids=[s.description for s in SESSION])
    def test_relay_exits_zero(self, step):
        payload = json.dumps(
            {
                "event": step.event,
                "tool_name": step.tool_name,
                "tool_input": step.tool_input,
            }
        )
        result = subprocess.run(
            [sys.executable, "-m", "ohm.hook_relay"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Step '{step.description}': exited {result.returncode}; "
            f"stderr: {result.stderr[:200]!r}"
        )
        assert result.stdout == ""


# ============================================================================
# IPC message round-trips
# ============================================================================


class TestIpcMessages:
    def test_all_canonical_ipc_messages_encodable(self):
        for step in SESSION:
            canonical = _canonical(step)
            msg = CanonicalIpcMessage(
                provider="claude",
                provider_event=step.event,
                canonical_event=canonical.canonical_event,
                tool_name=canonical.tool_name,
                label=canonical.label,
                path=canonical.path,
                ts=time.time(),
            )
            raw = encode_message(msg)
            assert raw.endswith(b"\n")
            raw.decode("utf-8")
