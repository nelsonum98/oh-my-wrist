"""
Tests for the USAGE characteristic path in ble_daemon.py

Covers:
- A usage CanonicalIpcMessage updates usage state and enqueues a USAGE notify.
- Identical usage payloads are suppressed (no redundant notify).
- A usage message does NOT produce a history frame.
- Absent windows default to -1.
- A non-claude usage message is ignored.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from ohm.protocol import USAGE_CHAR_UUID, CanonicalIpcMessage
from ohm.usage_fetchers import WeeklyUsage


def _make_daemon():
    mock_server = MagicMock()
    mock_char = MagicMock()
    mock_char.value = bytearray()
    mock_server.get_characteristic.return_value = mock_char

    with patch("ohm.ble_daemon.BlessServer", return_value=mock_server):
        from ohm.ble_daemon import BleDaemon

        daemon = BleDaemon()
        daemon._server = mock_server
        daemon._device_connected = True
        daemon._has_subscribers = True
        return daemon, mock_server, mock_char


def _usage_msg(provider="claude", s=23, w=41):
    return CanonicalIpcMessage(
        provider=provider,
        provider_event="statusline",
        canonical_event="usage",
        meta={"s": s, "w": w},
    )


class TestUsageIngestion:
    def test_usage_message_updates_state_and_notifies(self):
        daemon, _, _ = _make_daemon()
        daemon._process_ipc_message(_usage_msg(s=23, w=41))
        assert daemon._usage == {"c": 41, "x": -1}
        assert daemon._legacy_usage == {"s": 23, "w": 41}
        service_uuid, char_uuid = daemon._notify_queue.get_nowait()
        assert char_uuid == USAGE_CHAR_UUID

    def test_usage_message_does_not_create_history_frame(self):
        daemon, _, _ = _make_daemon()
        daemon._process_ipc_message(_usage_msg())
        assert daemon._last_frame == b""

    def test_identical_payload_suppressed(self):
        daemon, _, _ = _make_daemon()
        daemon._process_ipc_message(_usage_msg(s=10, w=20))
        daemon._notify_queue.get_nowait()  # drain first notify
        daemon._notify_queue.get_nowait()  # drain provider notify
        daemon._process_ipc_message(_usage_msg(s=10, w=20))
        assert daemon._notify_queue.empty()

    def test_absent_windows_default_to_minus_one(self):
        daemon, _, _ = _make_daemon()
        msg = CanonicalIpcMessage(
            provider="claude",
            provider_event="statusline",
            canonical_event="usage",
            meta={},
        )
        daemon._process_ipc_message(msg)
        assert daemon._usage == {"c": -1, "x": -1}

    def test_non_claude_usage_ignored(self):
        daemon, _, _ = _make_daemon()
        daemon._process_ipc_message(_usage_msg(provider="opencode"))
        assert daemon._usage == {"c": -1, "x": -1}
        assert daemon._notify_queue.empty()

    def test_usage_payload_is_compact_json(self):
        daemon, _, _ = _make_daemon()
        daemon._usage = {"c": 5, "x": 99}
        assert daemon._usage_payload() == b'{"c":5,"x":99}'

    def test_released_client_payload_preserves_session_and_weekly(self):
        daemon, _, _ = _make_daemon()
        daemon._process_ipc_message(_usage_msg(s=23, w=41))
        assert daemon._legacy_usage_payload() == b'{"s":23,"w":41}'

    def test_session_only_change_is_not_suppressed(self):
        daemon, _, _ = _make_daemon()
        daemon._process_ipc_message(_usage_msg(s=10, w=20))
        daemon._notify_queue.get_nowait()
        daemon._notify_queue.get_nowait()
        daemon._process_ipc_message(_usage_msg(s=11, w=20))
        assert daemon._notify_queue.qsize() == 2

    def test_out_of_range_values_are_clamped(self):
        daemon, _, _ = _make_daemon()
        daemon._process_ipc_message(_usage_msg(s=150, w=-5))
        assert daemon._usage == {"c": -1, "x": -1}

    def test_non_numeric_values_become_minus_one(self):
        daemon, _, _ = _make_daemon()
        daemon._process_ipc_message(_usage_msg(s="abc", w=None))
        assert daemon._usage == {"c": -1, "x": -1}


def test_periodic_refresh_clears_expired_usage() -> None:
    daemon, _, _ = _make_daemon()
    daemon._usage = {"c": 42, "x": 18}

    def expired_claude():
        daemon._stop_event.set()
        return None

    with (
        patch("ohm.ble_daemon.fetch_claude_weekly", side_effect=expired_claude),
        patch(
            "ohm.ble_daemon.fetch_codex_weekly",
            return_value=WeeklyUsage(18),
        ),
        patch.object(daemon, "_push_usage") as push,
    ):
        asyncio.run(daemon._periodic_usage_task())

    assert daemon._usage == {"c": -1, "x": 18}
    assert daemon._legacy_usage == {"s": -1, "w": -1}
    push.assert_called_once()
