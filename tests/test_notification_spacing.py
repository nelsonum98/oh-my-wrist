"""
test_notification_spacing.py — Integration test proving the notification queue
serializes BLE notifications with ≥100ms spacing to avoid overwhelming the
watch BLE stack.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch


from ohm.ble_daemon import BleDaemon, _NOTIFY_SPACING_S
from ohm.protocol import (
    ALERT_IDLE_WAITING,
    HISTORY_CHAR_UUID,
    STATS_CLAUDE_CHAR_UUID,
    STATS_OPENCODE_CHAR_UUID,
)
from ohm.provider_types import CanonicalEvent


def _make_daemon():
    daemon = BleDaemon()
    mock_server = MagicMock()
    mock_server.get_characteristic.return_value = MagicMock()
    daemon._server = mock_server
    daemon._device_connected = True
    daemon._has_subscribers = True
    return daemon, mock_server


class TestNotificationSpacing:
    def test_rapid_ipc_burst_produces_spaced_notifications(self):
        """3 rapid IPC messages should produce notifications spaced ≥100ms apart."""
        daemon, mock_server = _make_daemon()
        timestamps: list[float] = []

        def recording_update(*args, **kwargs):
            timestamps.append(time.monotonic())

        mock_server.update_value = recording_update

        async def _run():
            # Start the drain task
            drain = asyncio.ensure_future(daemon._drain_notify_queue())

            # Simulate 3 rapid IPC messages (session_start, tool_start, tool_end)
            daemon._push_event(
                CanonicalEvent(provider="claude", canonical_event="session_start")
            )
            daemon._push_event(
                CanonicalEvent(
                    provider="claude",
                    canonical_event="tool_start",
                    tool_name="Bash",
                    label="pytest",
                )
            )
            daemon._push_event(
                CanonicalEvent(provider="claude", canonical_event="tool_end")
            )

            # Wait for all 3 to drain (3 * spacing + margin)
            await asyncio.sleep(_NOTIFY_SPACING_S * 4)

            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

        # Should have exactly 3 notifications (one per changed frame)
        assert len(timestamps) == 3
        # Each pair should be spaced ≥ _NOTIFY_SPACING_S apart
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i - 1]
            assert delta >= _NOTIFY_SPACING_S * 0.9, (
                f"Notifications {i - 1}→{i} spaced only {delta * 1000:.1f}ms apart"
            )

    def test_single_ipc_with_alert_and_stats_produces_spaced_burst(self):
        """A single IPC message that triggers HISTORY + ALERT + STATS should
        space all notifications ≥100ms apart."""
        daemon, mock_server = _make_daemon()
        timestamps: list[float] = []

        def recording_update(*args, **kwargs):
            timestamps.append(time.monotonic())

        mock_server.update_value = recording_update

        async def _run():
            drain = asyncio.ensure_future(daemon._drain_notify_queue())

            with patch("ohm.ble_daemon.load_config") as mock_cfg:
                mock_cfg.return_value = MagicMock(
                    haptic_allowed=MagicMock(return_value=True)
                )

                # Simulate _process_ipc_message flow:
                # 1. _push_event (HISTORY)
                daemon._push_event(
                    CanonicalEvent(provider="claude", canonical_event="session_stop")
                )
                # 2. _send_alert (ALERT)
                await daemon._send_alert(ALERT_IDLE_WAITING)
                # 3. _push_stats (STATS_CLAUDE + STATS_OPENCODE)
                daemon._push_stats(force=True)

            # Wait for queue to drain
            await asyncio.sleep(_NOTIFY_SPACING_S * 6)

            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

        # Should have: HISTORY + ALERT + ALERT_RESET + STATS_CLAUDE + STATS_OPENCODE = 5
        assert len(timestamps) >= 4
        # All consecutive pairs spaced ≥100ms
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i - 1]
            assert delta >= _NOTIFY_SPACING_S * 0.9, (
                f"Notifications {i - 1}→{i} spaced only {delta * 1000:.1f}ms apart"
            )

    def test_subscribe_burst_produces_spaced_notifications(self):
        """Subscribing to 3 characteristics rapidly coalesces into a single
        deferred push (1.5s after the last subscribe), producing 3 spaced
        notifications — not a burst of 3 immediate ones."""
        daemon, mock_server = _make_daemon()
        timestamps: list[float] = []

        def recording_update(*args, **kwargs):
            timestamps.append(time.monotonic())

        mock_server.update_value = recording_update

        # Push an event first so _notify_history has something to re-push
        daemon._push_event(
            CanonicalEvent(provider="claude", canonical_event="tool_end")
        )

        async def _run():
            drain = asyncio.ensure_future(daemon._drain_notify_queue())

            # Simulate rapid CCCD subscribes (as the watch does)
            for uuid in [
                HISTORY_CHAR_UUID,
                STATS_CLAUDE_CHAR_UUID,
                STATS_OPENCODE_CHAR_UUID,
            ]:
                char = MagicMock()
                char.uuid = uuid
                daemon._handle_write(char, bytearray([0x01, 0x00]))

            # Wait for deferred push (1.5s) + queue drain (3 × spacing)
            await asyncio.sleep(1.5 + _NOTIFY_SPACING_S * 5)

            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

        # 1 initial + 5 deferred: history, 2 stats, legacy and provider usage.
        assert len(timestamps) == 6
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i - 1]
            assert delta >= _NOTIFY_SPACING_S * 0.9, (
                f"Notifications {i - 1}→{i} spaced only {delta * 1000:.1f}ms apart"
            )

    def test_queue_drains_completely(self):
        """All enqueued notifications must eventually be delivered — no stuck items."""
        daemon, mock_server = _make_daemon()
        delivered = []

        def recording_update(*args, **kwargs):
            delivered.append(args)

        mock_server.update_value = recording_update

        async def _run():
            drain = asyncio.ensure_future(daemon._drain_notify_queue())

            # Enqueue 5 notifications rapidly
            for _ in range(5):
                daemon._enqueue_notify(HISTORY_CHAR_UUID)

            # Wait for all to drain
            await asyncio.sleep(_NOTIFY_SPACING_S * 6)

            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

        assert len(delivered) == 5
        assert daemon._notify_queue.empty()
