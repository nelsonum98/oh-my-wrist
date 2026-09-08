"""
test_ble_daemon_extended.py — Extended edge-case tests for ble_daemon.py.

Covers
------
- _push_event: every canonical event produces a valid frame ≤ MAX_FRAME_LEN
- Session flag transitions through realistic event sequences
- Characteristic read handler dispatches on every known UUID
- IPC socket lifecycle: stale file removal, garbage data tolerance
- IPC message round-trip via the Unix socket handler
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from ohm.icons import (
    FLAG_ACCENT,
    FLAG_CLEAR_PREV_SPINNER,
    FLAG_SPINNER,
    IconId,
)
from ohm.protocol import (
    ALERT_CHAR_UUID,
    OHM_SERVICE_UUID,
    HISTORY_CHAR_UUID,
    MAX_FRAME_LEN,
    PROTOCOL_VERSION,
    SESSION_CHAR_UUID,
    SOCKET_PATH,
    STATS_CLAUDE_CHAR_UUID,
    STATS_OPENCODE_CHAR_UUID,
    service_uuid_for_connection_id,
)
from ohm.provider_types import CanonicalEvent


# ============================================================================
# Helpers
# ============================================================================


def _make_daemon():
    """Return a BleDaemon with a mocked BlessServer."""
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


def _ev(canonical: str, **kwargs) -> CanonicalEvent:
    base = {"provider": "claude", "canonical_event": canonical}
    base.update(kwargs)
    return CanonicalEvent(**base)


# ============================================================================
# _push_event — frame correctness
# ============================================================================


class TestPushEventFrames:
    def test_empty_text(self):
        daemon, _ = _make_daemon()
        daemon._push_event(_ev("tool_end"))
        # ver, icon, flags, len, [no text]
        assert daemon._last_frame[3] == 0
        assert len(daemon._last_frame) == 4

    def test_long_label_truncated_within_frame_limit(self):
        daemon, _ = _make_daemon()
        long_label = "A" * 100
        daemon._push_event(_ev("tool_start", tool_name="Bash", label=long_label))
        assert len(daemon._last_frame) <= MAX_FRAME_LEN

    def test_multibyte_in_label_not_split(self):
        daemon, _ = _make_daemon()
        daemon._push_event(
            _ev("tool_start", tool_name="Edit", path="/p/ファイルファイルファイル.py")
        )
        # The text portion must decode cleanly
        text_len = daemon._last_frame[3]
        daemon._last_frame[4 : 4 + text_len].decode("utf-8")
        assert len(daemon._last_frame) <= MAX_FRAME_LEN

    def test_every_canonical_event_under_max_frame_len(self):
        daemon, _ = _make_daemon()
        for ce in (
            "tool_start",
            "tool_end",
            "session_start",
            "session_idle",
            "session_stop",
            "session_error",
            "file_edit",
            "todo_update",
            "permission_request",
            "permission_reply",
            "command",
            "status",
        ):
            daemon._push_event(_ev(ce, tool_name="Bash", label="x", path="/x"))
            assert len(daemon._last_frame) <= MAX_FRAME_LEN

    def test_protocol_version_byte(self):
        daemon, _ = _make_daemon()
        daemon._push_event(_ev("session_start"))
        assert daemon._last_frame[0] == PROTOCOL_VERSION

    def test_tool_end_sets_clear_prev_spinner_flag(self):
        daemon, _ = _make_daemon()
        daemon._push_event(_ev("tool_end"))
        assert daemon._last_frame[2] & FLAG_CLEAR_PREV_SPINNER

    def test_session_error_sets_accent_and_clear_flag(self):
        daemon, _ = _make_daemon()
        daemon._push_event(_ev("session_error"))
        flags = daemon._last_frame[2]
        assert flags & FLAG_ACCENT
        assert flags & FLAG_CLEAR_PREV_SPINNER

    def test_tool_start_shell_has_spinner(self):
        daemon, _ = _make_daemon()
        daemon._push_event(_ev("tool_start", tool_name="Bash", label="pytest"))
        assert daemon._last_frame[1] == int(IconId.PLAY)
        assert daemon._last_frame[2] & FLAG_SPINNER


# ============================================================================
# Session flag transitions
# ============================================================================


class TestSessionFlagTransitions:
    def test_default_session_flag_is_inactive(self):
        daemon, _ = _make_daemon()
        assert daemon._session_active == b"\x00"

    def test_active_event_sets_flag(self):
        daemon, _ = _make_daemon()
        daemon._push_event(
            _ev("tool_start", tool_name="Bash", label="pytest"),
            session_active=True,
        )
        assert daemon._session_active == b"\x01"

    def test_stop_clears_flag(self):
        daemon, _ = _make_daemon()
        daemon._push_event(_ev("tool_start", tool_name="Bash"), session_active=True)
        daemon._push_event(_ev("session_stop"), session_active=False)
        assert daemon._session_active == b"\x00"

    def test_toggle_sequence(self):
        daemon, _ = _make_daemon()
        for active in (True, False, True, True, False):
            daemon._push_event(_ev("status"), session_active=active)
            assert daemon._session_active == (b"\x01" if active else b"\x00")


# ============================================================================
# Mock BLE server interaction
# ============================================================================


class TestBleMockInteraction:
    def test_update_value_called_per_push_event(self):
        daemon, mock_server = _make_daemon()
        daemon._push_event(_ev("tool_end"))
        assert daemon._notify_queue.qsize() == 1

    def test_update_value_targets_history_char(self):
        daemon, mock_server = _make_daemon()
        daemon._push_event(_ev("tool_end"))
        service_uuid, char_uuid = daemon._notify_queue.get_nowait()
        assert service_uuid == OHM_SERVICE_UUID
        assert char_uuid == HISTORY_CHAR_UUID

    def test_no_crash_on_server_update_value_exception(self):
        daemon, mock_server = _make_daemon()
        mock_server.update_value.side_effect = RuntimeError("BLE stack error")
        daemon._push_event(_ev("tool_end"))  # must not raise

    def test_no_crash_on_get_characteristic_exception(self):
        daemon, mock_server = _make_daemon()
        mock_server.get_characteristic.side_effect = RuntimeError("char not found")
        daemon._push_event(_ev("tool_end"))  # must not raise

    def test_rapid_event_sequence(self):
        daemon, mock_server = _make_daemon()
        events = [
            _ev("session_start"),
            _ev("tool_start", tool_name="Bash", label="pytest"),
            _ev("tool_end"),
            _ev("session_idle"),
            _ev("session_stop"),
        ]
        for ev in events:
            daemon._push_event(ev)
        # Each push enqueues one notification
        assert daemon._notify_queue.qsize() == len(events)
        # Last frame is the stop frame
        assert daemon._last_frame[1] == int(IconId.STOP)


# ============================================================================
# Characteristic read handler
# ============================================================================


class TestCharacteristicReadHandler:
    def test_read_history_char_returns_last_frame(self):
        daemon, _ = _make_daemon()
        daemon._push_event(_ev("tool_end"))
        char = MagicMock()
        char.uuid = HISTORY_CHAR_UUID
        result = daemon._handle_read(char)
        assert bytes(result) == daemon._last_frame

    def test_read_session_char(self):
        daemon, _ = _make_daemon()
        daemon._push_event(_ev("tool_start", tool_name="Bash"), session_active=True)
        char = MagicMock()
        char.uuid = SESSION_CHAR_UUID
        result = daemon._handle_read(char)
        assert bytes(result) == b"\x01"

    def test_read_alert_char(self):
        daemon, _ = _make_daemon()
        char = MagicMock()
        char.uuid = ALERT_CHAR_UUID
        result = daemon._handle_read(char)
        assert bytes(result) == b"\x00"

    def test_read_stats_chars(self):
        daemon, _ = _make_daemon()
        for uuid in (STATS_CLAUDE_CHAR_UUID, STATS_OPENCODE_CHAR_UUID):
            char = MagicMock()
            char.uuid = uuid
            result = daemon._handle_read(char)
            assert isinstance(result, bytearray)

    def test_read_unknown_uuid_returns_empty(self):
        daemon, _ = _make_daemon()
        char = MagicMock()
        char.uuid = "00000000-0000-0000-0000-000000000000"
        result = daemon._handle_read(char)
        assert bytes(result) == b""


# ============================================================================
# IPC socket lifecycle
# ============================================================================


class TestIpcSocketLifecycle:
    def test_socket_path_is_user_private(self):
        uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
        assert SOCKET_PATH.endswith(f"/oh-my-wrist-{uid}/ohm.sock")
        assert SOCKET_PATH != "/tmp/ohm.sock"

# ============================================================================
# IPC message round-trip via Unix socket handler
# ============================================================================


class TestIpcMessageHandling:
    def test_config_update_requires_control_token(self):
        from ohm.protocol import CanonicalIpcMessage

        daemon, _ = _make_daemon()
        daemon._apply_connection_id = MagicMock()
        msg = CanonicalIpcMessage(
            provider="claude",
            provider_event="oh-my-wrist.config",
            canonical_event="config_update",
            active=False,
            meta={"connection_id": 42},
        )

        with (
            patch("ohm.ble_daemon.asyncio.ensure_future") as ensure_future,
            patch("ohm.ble_daemon.get_control_token", return_value="secret"),
        ):
            daemon._process_ipc_message(msg)

        daemon._apply_connection_id.assert_not_called()
        ensure_future.assert_not_called()
        assert daemon._last_frame == b""

    def test_config_update_schedules_connection_id_apply_with_valid_token(self):
        from ohm.protocol import CanonicalIpcMessage

        daemon, _ = _make_daemon()
        apply_result = object()
        daemon._apply_connection_id = MagicMock(return_value=apply_result)
        msg = CanonicalIpcMessage(
            provider="claude",
            provider_event="oh-my-wrist.config",
            canonical_event="config_update",
            active=False,
            meta={"connection_id": 42, "control_token": "secret"},
        )

        with (
            patch("ohm.ble_daemon.asyncio.ensure_future") as ensure_future,
            patch("ohm.ble_daemon.get_control_token", return_value="secret"),
        ):
            daemon._process_ipc_message(msg)

        daemon._apply_connection_id.assert_called_once_with(42)
        ensure_future.assert_called_once_with(apply_result)
        assert daemon._last_frame == b""

    @pytest.mark.asyncio
    async def test_apply_connection_id_restarts_ble_server_and_clears_state(self):
        daemon, mock_server = _make_daemon()
        mock_server.stop = AsyncMock()
        daemon._service_uuid = OHM_SERVICE_UUID
        daemon._device_connected = True
        daemon._has_subscribers = True
        daemon._notify_queue.put_nowait((OHM_SERVICE_UUID, HISTORY_CHAR_UUID))

        with patch.object(daemon, "_setup_ble", new_callable=AsyncMock) as setup_ble:
            await daemon._apply_connection_id(12)

        mock_server.stop.assert_awaited_once()
        setup_ble.assert_awaited_once_with(connection_id=12)
        assert daemon._device_connected is False
        assert daemon._has_subscribers is False
        assert daemon._notify_queue.empty()

    @pytest.mark.asyncio
    async def test_apply_connection_id_rolls_back_when_new_setup_fails(self):
        daemon, mock_server = _make_daemon()
        mock_server.stop = AsyncMock()
        daemon._connection_id = 7
        daemon._service_uuid = service_uuid_for_connection_id(7)
        daemon._device_connected = True
        daemon._has_subscribers = True

        with patch.object(
            daemon,
            "_setup_ble",
            new_callable=AsyncMock,
            side_effect=[RuntimeError("new service failed"), None],
        ) as setup_ble:
            await daemon._apply_connection_id(12)

        mock_server.stop.assert_awaited_once()
        assert setup_ble.await_args_list == [
            call(connection_id=12),
            call(connection_id=7),
        ]
        assert daemon._connection_id == 7
        assert daemon._service_uuid == service_uuid_for_connection_id(7)

    @pytest.mark.asyncio
    async def test_canonical_message_pushes_frame(self):
        from ohm.protocol import CanonicalIpcMessage, encode_message

        daemon, _ = _make_daemon()

        msg = CanonicalIpcMessage(
            provider="claude",
            provider_event="PreToolUse",
            canonical_event="tool_start",
            tool_name="Edit",
            path="/repo/main.py",
        )
        raw = encode_message(msg)

        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        await daemon._handle_unix_client(reader, writer)

        assert daemon._last_frame[1] == int(IconId.PENCIL)
        assert daemon._session_active == b"\x01"

    @pytest.mark.asyncio
    async def test_session_stop_clears_session_flag(self):
        from ohm.protocol import CanonicalIpcMessage, encode_message

        daemon, _ = _make_daemon()
        daemon._push_event(_ev("tool_start", tool_name="Bash"), session_active=True)

        msg = CanonicalIpcMessage(
            provider="claude",
            provider_event="Stop",
            canonical_event="session_stop",
        )
        raw = encode_message(msg)

        reader = asyncio.StreamReader()
        reader.feed_data(raw)
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()

        await daemon._handle_unix_client(reader, writer)

        assert daemon._session_active == b"\x00"

    @pytest.mark.asyncio
    async def test_empty_data_no_crash(self):
        daemon, _ = _make_daemon()
        reader = asyncio.StreamReader()
        reader.feed_eof()
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        await daemon._handle_unix_client(reader, writer)

    @pytest.mark.asyncio
    async def test_garbage_data_no_crash(self):
        daemon, _ = _make_daemon()
        reader = asyncio.StreamReader()
        reader.feed_data(b"\xff\xfe garbage \x00\x01\n")
        writer = MagicMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        await daemon._handle_unix_client(reader, writer)
