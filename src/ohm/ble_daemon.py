"""
ble_daemon.py — Long-running BLE GATT peripheral process.

Responsibilities
----------------
1. Listen on the local IPC socket for messages from hook_relay (Claude) and
   the OpenCode plugin.  Both providers send CanonicalIpcMessage; legacy
   IpcMessage from older hook_relay versions is also accepted.
2. Advertise a BLE GATT peripheral (bless) with:
     - OHM_SERVICE_UUID service
     - HISTORY_CHAR_UUID         (Read + Notify) — per-event binary frame
     - SESSION_CHAR_UUID         (Read)
     - ALERT_CHAR_UUID           (Read + Notify)
     - STATS_CLAUDE_CHAR_UUID    (Read + Notify) — Claude-only counters
     - STATS_OPENCODE_CHAR_UUID  (Read + Notify) — OpenCode-only counters
3. On receiving a status update:
     a. Route the IPC message into the matching provider's SessionState via
        MultiProviderSessionState.
     b. Encode the canonical event into a binary frame via
        :func:`history_encoder.encode_event`.
     c. Update HISTORY_CHAR_UUID and send a GATT notification.  The watch
        owns the history deque and the spinner animation.
     d. If alert_type != 0 and haptic is allowed: write alert byte to
        ALERT_CHAR_UUID, then reset to 0x00 after 500 ms.
     e. Update both per-provider STATS characteristics with the current
        per-session statistics.
4. Run a periodic 10-second task to push stats even during idle periods.
5. Log all activity to ~/.oh-my-wrist/daemon.log.
6. Write a PID file to ~/.oh-my-wrist/daemon.pid.

Platform notes
--------------
macOS   : Core Bluetooth via objc bridge (bless uses it internally).
          Must run as a regular user (not root).
Windows : WinRT Windows.Devices.Bluetooth (bless).
          Requires Windows 10 Build 17763+.
Linux   : BlueZ D-Bus API (bless).
          User must be in the `bluetooth` group.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import sys
from pathlib import Path
from typing import Any

import sys as _sys

if _sys.platform == "win32":
    from ohm.platform.windows_ble import (  # type: ignore[import]
        BlessServer,
        BlessGATTCharacteristic,
        GATTCharacteristicProperties,
        GATTAttributePermissions,
    )
else:
    from bless import (
        BlessServer,
        BlessGATTCharacteristic,
        GATTCharacteristicProperties,
        GATTAttributePermissions,
    )  # type: ignore[import]
from loguru import logger

from ohm.config import get_control_token, load_config
from ohm.history_encoder import encode_event
from ohm.protocol import (
    ALERT_CHAR_UUID,
    ALERT_NONE,
    OHM_SERVICE_UUID,
    CanonicalIpcMessage,
    HISTORY_CHAR_UUID,
    IPC_BACKEND,
    IpcMessage,
    MAX_FRAME_LEN,
    MAX_USAGE_LEN,
    NAMED_PIPE_PATH,
    PROVIDER_USAGE_CHAR_UUID,
    SESSION_CHAR_UUID,
    SOCKET_PATH,
    STATS_CLAUDE_CHAR_UUID,
    STATS_OPENCODE_CHAR_UUID,
    USAGE_CHAR_UUID,
    decode_message,
    service_uuid_for_connection_id,
)
from ohm.provider_types import CanonicalEvent
from ohm.session_state import MultiProviderSessionState
from ohm.usage_fetchers import fetch_claude_weekly, fetch_codex_weekly

_bless_discoverable_patched = False
_BLE_DIAGNOSTICS_ENABLED = os.environ.get("OHM_BLE_DIAG") == "1"


def _patch_bless_le_discoverable() -> None:
    global _bless_discoverable_patched
    if _bless_discoverable_patched or sys.platform != "linux":
        return
    _bless_discoverable_patched = True

    try:
        from bless.backends.bluezdbus.dbus.advertisement import (
            BlueZLEAdvertisement,
            Type,
        )
        from bless.backends.bluezdbus.dbus.application import BlueZGattApplication
        from dbus_next.service import dbus_property as _dbus_property

        class _DiscoverableLEAdvertisement(BlueZLEAdvertisement):
            """BlueZLEAdvertisement with the Discoverable D-Bus property."""

            def __init__(self, advertising_type, index, app):
                super().__init__(advertising_type, index, app)
                self._discoverable = True

            @_dbus_property()
            def Discoverable(self) -> "b":  # type: ignore[override]  # noqa: N802
                return self._discoverable

            @Discoverable.setter  # type: ignore
            def Discoverable(self, value: "b"):  # type: ignore  # noqa: N802
                self._discoverable = value

        async def _start_advertising_discoverable(self, adapter):  # type: ignore[override]
            await self.set_name(adapter, self.app_name)
            advertisement = _DiscoverableLEAdvertisement(
                Type.PERIPHERAL, len(self.advertisements) + 1, self
            )
            self.advertisements.append(advertisement)
            advertisement._service_uuids.append(self.services[0].UUID)
            self.bus.export(advertisement.path, advertisement)
            iface = adapter.get_interface("org.bluez.LEAdvertisingManager1")
            await iface.call_register_advertisement(advertisement.path, {})

        BlueZGattApplication.start_advertising = _start_advertising_discoverable  # type: ignore[assignment]
        logger.info("Patched bless LEAdvertisement with Discoverable=true")
    except Exception as exc:
        logger.warning("Failed to patch bless Discoverable flag: {}", exc)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path.home() / ".oh-my-wrist"
_LOG_PATH = _CONFIG_DIR / "daemon.log"
_PID_PATH = _CONFIG_DIR / "daemon.pid"

# Periodic keepalive intervals (seconds). The keepalive loop both pushes stats
# and re-advertises after a central disconnects. LIVE is intentionally slack
# (5 s, not 1–2 s) to avoid overwhelming the watch BLE stack. Combined with
# payload-diff suppression in _push_stats, this keeps the steady-state
# notification rate well under one per second.
_STATS_PUSH_INTERVAL_IDLE = 10
_STATS_PUSH_INTERVAL_LIVE = 5

# Minimum inter-notification spacing (seconds). All update_value calls are
# serialized through an async queue with this delay to avoid overwhelming
# the watch BLE stack's internal buffer.
_NOTIFY_SPACING_S = 0.15

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        str(_LOG_PATH),
        rotation="10 MB",
        retention=3,
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
    )
    logger.add(sys.stderr, level="INFO")

    # Route bless / bleak / dbus_next loggers through stdlib logging so they
    # appear in our file log. Useful for diagnosing connection / GATT issues.
    # Note: bless 0.3.0 uses dbus_next (not dbus_fast).
    import logging

    class _LoguruBridge(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = self.format(record)
            except Exception:
                msg = record.getMessage()
            logger.opt(depth=6).log(record.levelname, msg)

    bridge = _LoguruBridge()
    bridge.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    for name in ("bless", "bleak", "dbus_next"):
        lg = logging.getLogger(name)
        lg.handlers = [bridge]
        lg.setLevel(logging.DEBUG)
        lg.propagate = False


# ---------------------------------------------------------------------------
# PID management
# ---------------------------------------------------------------------------


def _write_pid() -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _PID_PATH.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        _PID_PATH.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# IPC message → CanonicalEvent conversion
# ---------------------------------------------------------------------------


def _ipc_to_canonical(msg: IpcMessage | CanonicalIpcMessage) -> CanonicalEvent:
    """Convert any IPC message type to a CanonicalEvent for the session engine."""
    if isinstance(msg, CanonicalIpcMessage):
        return CanonicalEvent(
            provider=msg.provider,
            provider_event=msg.provider_event,
            canonical_event=msg.canonical_event,  # type: ignore[arg-type]
            session_id=msg.session_id,
            tool_name=msg.tool_name,
            label=msg.label,
            path=msg.path,
            status_text=msg.status_text,
            active=msg.active,
            ts=msg.ts,
            meta=msg.meta,
        )
    # Legacy IpcMessage path — kept so older test fixtures keep working.
    from ohm.adapters.claude_adapter import adapt_claude_hook
    from ohm.protocol import HookEvent

    hook = HookEvent(
        event=msg.event,
        tool_name=msg.event_data.get("tool_name"),
        tool_input=msg.event_data.get("tool_input"),
        session_id=msg.event_data.get("session_id"),
    )
    return adapt_claude_hook(hook, raw_payload=msg.event_data)


def _clamp_pct(value: object) -> int:
    """Coerce a usage percentage to an int in [-1, 100]; garbage -> -1."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1
    return max(-1, min(100, n))


def _control_token_valid(meta: dict[str, Any] | None) -> bool:
    """Return True when a daemon control message carries the local token."""
    token = (meta or {}).get("control_token")
    if not isinstance(token, str) or not token:
        return False
    try:
        expected = get_control_token()
    except Exception as exc:
        logger.warning("Could not load daemon control token: {}", exc)
        return False
    return secrets.compare_digest(token, expected)


# ---------------------------------------------------------------------------
# BLE Daemon
# ---------------------------------------------------------------------------


class BleDaemon:
    """Manages the BLE GATT peripheral and the IPC socket listener."""

    def __init__(self) -> None:
        self._server: BlessServer | None = None
        self._connection_id: int = 0
        self._service_uuid: str = OHM_SERVICE_UUID
        self._last_frame: bytes = b""
        self._session_active: bytes = b"\x00"
        self._current_alert: bytes = b"\x00"
        self._stop_event = asyncio.Event()
        self._multi = MultiProviderSessionState()
        # Last-pushed stats payload per provider; used to suppress redundant
        # notifications — only push when the JSON actually changed.
        self._last_pushed_stats: dict[str, bytes] = {}
        # Weekly-only usage percentages: c=Claude, x=Codex, -1=unknown.
        # Kept below one default ATT notification (JSON is at most 17 bytes).
        self._usage: dict[str, int] = {"c": -1, "x": -1}
        # Released clients use s=Claude five-hour and w=Claude seven-day.
        self._legacy_usage: dict[str, int] = {"s": -1, "w": -1}
        # Last-pushed usage payload, for redundant-notification suppression.
        self._last_pushed_usage: bytes = b""
        # Async queue serializing all BLE notifications with inter-notification
        # spacing to avoid overwhelming the watch BLE stack.
        self._notify_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._ble_restart_lock = asyncio.Lock()
        # Deferred post-subscribe push: fires once 1.5s after the *last*
        # CCCD subscribe arrives, giving the watch time to finish all CCCD
        # writes and transition to PHASE_READY before any notifications land.
        self._deferred_push_handle: asyncio.TimerHandle | None = None
        # True while the watch is still subscribing to CCCDs.  Blocks the
        # periodic stats task from pushing notifications that would land
        # during the subscribe phase.
        self._subscribe_settling: bool = False
        # Cached connection state — updated by the periodic task and the
        # deferred-push callback.  Used by _enqueue_notify to suppress
        # notifications when no central is connected (sending to nobody
        # wastes cycles and can confuse some BLE stacks).
        self._device_connected: bool = False
        # True once at least one CCCD subscribe (0x01 0x00) has been received
        # from the connected central.  Notifications are suppressed until this
        # is set — per BLE spec, peripherals must not notify before subscribe.
        self._has_subscribers: bool = False
        # macOS: detect CCCD subscribes via the connection monitor
        # (CoreBluetooth never calls write_request_func for CCCD writes).
        self._corebluetooth_subscribe_via_monitor: bool = False
        # BlueZ pairing agent (Linux only) — kept alive for daemon lifetime.
        # _agent_bus: dbus_next connection (preferred)
        # _agent_process: bluetoothctl subprocess (fallback)
        self._agent_bus: Any = None
        self._agent_process: Any = None

    # ------------------------------------------------------------------
    # BLE setup
    # ------------------------------------------------------------------

    async def _setup_ble(self, connection_id: int | None = None) -> None:
        """Initialise bless server and register the GATT service."""
        _patch_bless_le_discoverable()
        cfg = load_config()
        old_connection_id = self._connection_id
        old_service_uuid = self._service_uuid
        target_connection_id = (
            cfg.connection_id if connection_id is None else connection_id
        )
        target_service_uuid = service_uuid_for_connection_id(target_connection_id)

        loop = asyncio.get_event_loop()
        server = BlessServer(name="OHW", loop=loop)
        server.read_request_func = self._handle_read
        server.write_request_func = self._handle_write

        # CCCD subscribes are handled natively on Linux (BlueZ D-Bus
        # StartNotify) and macOS (CoreBluetooth didSubscribeToCharacteristic)
        # — neither reaches _handle_write. Install platform hooks so we
        # detect when the watch subscribes.
        if sys.platform == "linux":
            self._hook_bluez_start_notify()
        elif sys.platform == "darwin":
            self._corebluetooth_subscribe_via_monitor = True

        await server.add_new_service(target_service_uuid)

        # Notify-capable characteristics must NOT have a cached value on
        # macOS CoreBluetooth — pass None here, seed after server.start().
        await server.add_new_characteristic(
            target_service_uuid,
            HISTORY_CHAR_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )

        # SESSION characteristic — Read only (cached value is fine)
        await server.add_new_characteristic(
            target_service_uuid,
            SESSION_CHAR_UUID,
            GATTCharacteristicProperties.read,
            self._session_active,
            GATTAttributePermissions.readable,
        )

        await server.add_new_characteristic(
            target_service_uuid,
            ALERT_CHAR_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )

        await server.add_new_characteristic(
            target_service_uuid,
            STATS_CLAUDE_CHAR_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )
        await server.add_new_characteristic(
            target_service_uuid,
            STATS_OPENCODE_CHAR_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )

        await server.add_new_characteristic(
            target_service_uuid,
            USAGE_CHAR_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )
        await server.add_new_characteristic(
            target_service_uuid,
            PROVIDER_USAGE_CHAR_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            None,
            GATTAttributePermissions.readable,
        )

        try:
            await server.start()

            # Seed initial values now that the service is live.
            server.get_characteristic(HISTORY_CHAR_UUID).value = bytearray(
                self._last_frame
            )
            server.get_characteristic(ALERT_CHAR_UUID).value = bytearray(
                self._current_alert
            )
            server.get_characteristic(STATS_CLAUDE_CHAR_UUID).value = bytearray(
                self._multi.payload_for("claude")
            )
            server.get_characteristic(STATS_OPENCODE_CHAR_UUID).value = bytearray(
                self._multi.payload_for("opencode")
            )
            server.get_characteristic(USAGE_CHAR_UUID).value = bytearray(
                self._legacy_usage_payload()
            )
            server.get_characteristic(PROVIDER_USAGE_CHAR_UUID).value = bytearray(
                self._usage_payload()
            )
            self._server = server
            self._connection_id = target_connection_id
            self._service_uuid = target_service_uuid
            logger.info(
                "BLE peripheral advertising as 'OHW' (connection_id={}, service {})",
                self._connection_id,
                self._service_uuid,
            )

            # On Linux, install our StartNotify hook now that .app exists.
            if sys.platform == "linux" and hasattr(self, "_bluez_start_notify_hook"):
                try:
                    server.app.StartNotify = self._bluez_start_notify_hook
                    logger.info("BlueZ StartNotify hook installed")
                except Exception as exc:
                    logger.warning("Failed to install StartNotify hook: {}", exc)

            # On Linux, set a faster advertising interval (~500ms instead of default 1.28s)
            if sys.platform == "linux":
                await self._set_advertising_interval()

            # On Linux, ensure BlueZ adapter accepts incoming BLE connections
            if (
                sys.platform == "linux"
                and self._agent_bus is None
                and self._agent_process is None
            ):
                await self._ensure_bluez_pairable()

            # On Windows, clear stale OS-level BLE bonds so a re-launched watch
            # app can pair from a clean slate (see _remove_stale_windows_bonds).
            if sys.platform == "win32":
                await self._remove_stale_windows_bonds()

            # Log adapter state for diagnostics
            await self._log_ble_diagnostics()
        except Exception:
            if self._server is server:
                self._server = None
            self._connection_id = old_connection_id
            self._service_uuid = old_service_uuid
            try:
                result = server.stop()
                if hasattr(result, "__await__"):
                    await result
            except Exception as cleanup_exc:
                logger.warning(
                    "Failed to stop partially-started BLE server: {}", cleanup_exc
                )
            raise

    def _hook_bluez_start_notify(self) -> None:
        """Replace bless's no-op StartNotify with a hook that sets _has_subscribers.

        On Linux/BlueZ, CCCD subscribe (0x01 0x00) is handled by BlueZ via the
        D-Bus StartNotify method — it never reaches WriteValue/_handle_write.
        Bless sets StartNotify to `lambda x: None` by default. We replace it
        with our own callback that triggers the same subscribe logic that
        _handle_write would on macOS/Windows.
        """

        def _on_start_notify(_char: Any) -> None:
            logger.info("BlueZ StartNotify fired — setting _has_subscribers=True")
            self._has_subscribers = True
            self._schedule_deferred_push()

        # Defer until after start() when .app is guaranteed to exist.
        self._bluez_start_notify_hook = _on_start_notify

    async def _set_advertising_interval(self) -> None:
        """Set BLE advertising interval to ~500ms via hcitool.

        Default BlueZ advertising interval is 1.28s which is slow for discovery.
        BLE advertising interval is in units of 0.625ms.
        500ms / 0.625ms = 800 = 0x0320, little-endian = 0x20 0x03.
        """
        import subprocess

        try:
            # HCI LE Set Advertising Parameters (OGF=0x08, OCF=0x0006)
            # Params: min_interval(2) max_interval(2) adv_type(1) own_addr_type(1)
            #         peer_addr_type(1) peer_addr(6) channel_map(1) filter_policy(1)
            result = subprocess.run(
                [
                    "hcitool",
                    "-i",
                    "hci0",
                    "cmd",
                    "0x08",
                    "0x0006",
                    "20",
                    "03",  # min interval: 0x0320 = 800 * 0.625ms = 500ms
                    "20",
                    "03",  # max interval: 0x0320 = 800 * 0.625ms = 500ms
                    "00",  # adv type: ADV_IND (connectable undirected)
                    "00",  # own address type: public
                    "00",  # peer address type
                    "00",
                    "00",
                    "00",
                    "00",
                    "00",
                    "00",  # peer address
                    "07",  # channel map: all channels
                    "00",  # filter policy: allow all
                ],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                logger.info("BLE advertising interval set to 500ms")
            else:
                logger.warning(
                    "hcitool set adv interval failed: {}",
                    result.stderr.decode().strip(),
                )
        except Exception as exc:
            logger.warning("Failed to set advertising interval: {}", exc)

    async def _ensure_bluez_pairable(self) -> None:
        """Configure BlueZ adapter to accept incoming BLE pairing/connections.

        Without this, BlueZ silently rejects all incoming connection requests
        from the Garmin watch (pairDevice returns a Device but the CONNECTED
        callback never fires on the watch side).

        Two steps:
          1. Set adapter Pairable=true via bluetoothctl (persistent).
          2. Spawn a long-lived bluetoothctl process that acts as a
             NoInputNoOutput agent for the lifetime of this daemon.
        """
        import subprocess

        # Step 1: Set Pairable on (this persists, unlike the agent)
        try:
            result = subprocess.run(
                ["bluetoothctl", "pairable", "on"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                logger.info("BlueZ: pairable on — OK")
            else:
                logger.warning("BlueZ: pairable on — failed: {}", result.stderr.strip())
        except Exception as exc:
            logger.warning("BlueZ: pairable on — exception: {}", exc)

        # Step 1b: Remove all previously bonded devices.
        # When the nRF52 reboots or the simulator resets, it loses its stored
        # Long-Term Key (LTK). BlueZ still has the old LTK and rejects the
        # next connection attempt with an authentication error — silently, from
        # the watch's perspective (CONNECTED callback never fires).
        # Clearing bonded devices on every daemon start ensures both sides
        # always begin with a clean, un-bonded state.
        await self._remove_stale_bonds()

        # Step 2: Register a NoInputNoOutput pairing agent via dbus_next.
        # The agent class lives in platform/bluez_agent.py which deliberately
        # omits "from __future__ import annotations" — that import makes all
        # annotations lazy strings, causing dbus_next to see "'s'" instead of
        # "s" and fail to infer D-Bus signatures.
        try:
            await self._register_bluez_agent()
        except Exception as exc:
            logger.warning(
                "BlueZ: dbus_next agent failed: {} — "
                "falling back to bluetoothctl subprocess",
                exc,
            )
            await self._register_agent_subprocess_fallback()

    async def _remove_stale_bonds(self) -> None:
        """Remove all BlueZ-bonded devices on startup.

        When the nRF52 DK reboots or the Garmin simulator resets, it loses
        its stored Long-Term Key (LTK). BlueZ on Ubuntu still holds the old
        LTK and silently rejects the next connection request — the watch sees
        pairDevice() succeed but onConnectedStateChanged(CONNECTED) never
        fires because BlueZ drops the link at the HCI level after LTK mismatch.

        Solution: remove all known devices on every daemon start so BlueZ and
        the nRF52 always negotiate a fresh bond.
        """
        import subprocess

        try:
            # List all known (bonded/paired/cached) devices
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return
            removed = 0
            for line in result.stdout.strip().splitlines():
                # Format: "Device XX:XX:XX:XX:XX:XX Name"
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == "Device":
                    addr = parts[1]
                    rm = subprocess.run(
                        ["bluetoothctl", "remove", addr],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if rm.returncode == 0:
                        logger.info("BlueZ: removed stale device {}", addr)
                        removed += 1
                    else:
                        logger.debug("BlueZ: remove {} — {}", addr, rm.stderr.strip())
            if removed == 0:
                logger.debug("BlueZ: no stale devices to remove")
        except Exception as exc:
            logger.warning("BlueZ: stale bond removal failed: {}", exc)

    async def _remove_stale_windows_bonds(self) -> None:
        """Windows analog of _remove_stale_bonds — unpair Garmin watches at
        OS level on daemon start.

        Garmin Connect IQ docs say "pairing does not persist across application
        instances", so the watch app resets its in-memory bond state every
        launch. Windows, however, persists the Long-Term Key in its system
        Bluetooth database. Subsequent pair attempts hit an SMP authentication
        mismatch and fail silently — pairDevice() returns a Device object on
        the watch but onConnectedStateChanged(CONNECTED) never fires because
        Windows drops the link at the HCI/SMP layer.

        We enumerate paired BLE devices and unpair those whose friendly name
        matches a known Garmin product family. Name-based filtering is
        deliberate — removing every paired Bluetooth device would nuke the
        user's headphones, mouse, etc.
        """
        try:
            from winrt.windows.devices.enumeration import DeviceInformation  # type: ignore[import]
        except Exception as exc:
            logger.debug("Windows: WinRT enumeration imports unavailable: {}", exc)
            return

        # Friendly-name keywords for Garmin product families. Lowercased for
        # case-insensitive substring match. Add new product lines here.
        keywords = (
            "fenix",
            "venu",
            "vivoactive",
            "forerunner",
            "fr",
            "epix",
            "edge",
            "garmin",
            "instinct",
            "approach",
            "tactix",
            "enduro",
            "swim",
            "lily",
            "marq",
            "descent",
        )

        # AQS filter restricting enumeration to paired BLE devices only.
        # ProtocolId {bb7bb05e-...} is the Bluetooth LE protocol GUID per MS docs.
        # Using a pre-built AQS string sidesteps winrt-python's overload
        # resolution for BluetoothLEDevice.get_device_selector_from_pairing_state
        # which raises "Invalid parameter count" in some bindings.
        ble_selector = (
            'System.Devices.Aep.ProtocolId:="{bb7bb05e-5972-42b5-94fc-76eaa7084d49}" '
            "AND System.Devices.Aep.IsPaired:=System.StructuredQueryType.Boolean#True"
        )

        devices: Any = None
        # Strategy 1: explicit AQS-filter overload (preferred — narrowest match).
        for method_name in ("find_all_async_aqs_filter", "find_all_async"):
            method = getattr(DeviceInformation, method_name, None)
            if method is None:
                continue
            try:
                devices = await method(ble_selector)
                logger.debug(
                    "Windows: device enum via {} returned {} entries",
                    method_name,
                    len(list(devices)) if devices else 0,
                )
                # Need to re-call because len() consumed the iterator in some bindings.
                devices = await method(ble_selector)
                break
            except Exception as exc:
                logger.debug("Windows: {} failed: {}", method_name, exc)
                devices = None

        # Strategy 2: enumerate everything paired (no protocol filter) if BLE-only
        # AQS failed. Some bindings reject the ProtocolId clause.
        if devices is None:
            generic_selector = (
                "System.Devices.Aep.IsPaired:=System.StructuredQueryType.Boolean#True"
            )
            for method_name in ("find_all_async_aqs_filter", "find_all_async"):
                method = getattr(DeviceInformation, method_name, None)
                if method is None:
                    continue
                try:
                    devices = await method(generic_selector)
                    break
                except Exception as exc:
                    logger.debug("Windows: {}(generic) failed: {}", method_name, exc)
                    devices = None

        if devices is None:
            logger.warning(
                "Windows: paired device enumeration failed via all WinRT paths"
            )
            return

        removed = 0
        for device_info in devices:
            try:
                name = (device_info.name or "").lower()
                if not any(kw in name for kw in keywords):
                    continue
                pairing = device_info.pairing
                if pairing is None or not pairing.is_paired:
                    continue

                logger.info("Windows: unpairing stale bond '{}'", device_info.name)
                result = await pairing.unpair_async()
                # DeviceUnpairingResultStatus: 0=Unpaired, 1=AlreadyUnpaired,
                # 2=OperationAlreadyInProgress, 3=AccessDenied, 4=Failed.
                status = int(result.status) if result is not None else -1
                if status in (0, 1):
                    removed += 1
                    logger.info(
                        "Windows: unpaired '{}' (status={})", device_info.name, status
                    )
                else:
                    logger.warning(
                        "Windows: unpair of '{}' returned status={}",
                        device_info.name,
                        status,
                    )
            except Exception as exc:
                logger.warning(
                    "Windows: error unpairing device '{}': {}",
                    getattr(device_info, "name", "?"),
                    exc,
                )

        if removed == 0:
            logger.debug("Windows: no stale Garmin bonds to remove")
        else:
            logger.info("Windows: removed {} stale Garmin bond(s)", removed)

    async def _register_bluez_agent(self) -> None:
        """Register a NoInputNoOutput pairing agent via dbus_next.

        Delegates to platform.bluez_agent which lives in a module WITHOUT
        'from __future__ import annotations' — that future import causes
        dbus_next to see "'s'" instead of "s" in annotations, breaking
        its D-Bus signature inference.
        """
        from ohm.platform.bluez_agent import register_agent

        self._agent_bus = await register_agent()

    async def _register_agent_subprocess_fallback(self) -> None:
        """Fallback: spawn a persistent bluetoothctl process as agent."""
        try:
            self._agent_process = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._agent_process.stdin.write(b"agent NoInputNoOutput\ndefault-agent\n")
            await self._agent_process.stdin.drain()
            await asyncio.sleep(0.5)
            logger.info(
                "BlueZ: agent subprocess fallback started (PID {})",
                self._agent_process.pid,
            )
        except Exception as exc:
            logger.warning("BlueZ: agent subprocess fallback failed: {}", exc)
            self._agent_process = None

    async def _log_ble_diagnostics(self) -> None:
        """Log BLE adapter/backend state for connection debugging."""
        try:
            is_adv = await self._check_advertising()
            logger.info("BLE diagnostics: advertising={}", is_adv)
        except Exception as exc:
            logger.warning("BLE diagnostics failed: {}", exc)

        # On Linux, check if BlueZ adapter is powered and connectable
        if sys.platform == "linux":
            try:
                import subprocess

                result = subprocess.run(
                    ["bluetoothctl", "show"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if any(
                        k in line
                        for k in ("Powered", "Discoverable", "Pairable", "Advertising")
                    ):
                        logger.info("BlueZ adapter: {}", line)
            except Exception as exc:
                logger.debug("bluetoothctl diagnostics failed: {}", exc)

    def _clear_notify_queue(self) -> None:
        """Drop pending notifications that target a now-stopped GATT service."""
        while True:
            try:
                self._notify_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _apply_connection_id(self, connection_id: int) -> None:
        """Restart BLE advertising with the service UUID for ``connection_id``."""
        try:
            new_service_uuid = service_uuid_for_connection_id(connection_id)
        except ValueError as exc:
            logger.warning("Ignoring invalid connection_id update: {}", exc)
            return

        async with self._ble_restart_lock:
            if connection_id == self._connection_id and self._server is not None:
                logger.debug("Connection ID already {}; no BLE restart", connection_id)
                return

            old_server = self._server
            old_connection_id = self._connection_id
            old_service_uuid = self._service_uuid
            logger.info(
                "Applying connection ID change: {} ({}) -> {} ({})",
                old_connection_id,
                old_service_uuid,
                connection_id,
                new_service_uuid,
            )

            if self._deferred_push_handle is not None:
                self._deferred_push_handle.cancel()
                self._deferred_push_handle = None

            self._server = None
            self._device_connected = False
            self._has_subscribers = False
            self._subscribe_settling = False
            self._clear_notify_queue()

            if old_server is not None:
                try:
                    result = old_server.stop()
                    if hasattr(result, "__await__"):
                        await result
                except Exception as exc:
                    logger.warning("Failed to stop BLE server for ID change: {}", exc)

            try:
                await self._setup_ble(connection_id=connection_id)
                logger.info("Connection ID {} is now active", connection_id)
            except Exception as exc:
                logger.exception(
                    "Failed to restart BLE server for connection ID {}: {}",
                    connection_id,
                    exc,
                )
                self._server = None
                self._connection_id = old_connection_id
                self._service_uuid = old_service_uuid
                try:
                    await self._setup_ble(connection_id=old_connection_id)
                    logger.warning(
                        "Rolled BLE advertising back to connection ID {}",
                        old_connection_id,
                    )
                except Exception as rollback_exc:
                    self._server = None
                    self._connection_id = old_connection_id
                    self._service_uuid = old_service_uuid
                    logger.exception(
                        "Failed to roll BLE advertising back to connection ID {}: {}",
                        old_connection_id,
                        rollback_exc,
                    )

    # ------------------------------------------------------------------
    # Connection monitor — fast-poll for connection state changes
    # ------------------------------------------------------------------

    async def _connection_monitor_task(self) -> None:
        """Polls connection state every 2s and logs transitions.

        The bless library on Linux/BlueZ has no connection callback, so this is
        the only way to detect when a central connects or disconnects. This runs
        at 2s intervals (much faster than the 5-10s stats keepalive) so we can
        see in the logs exactly when a connection attempt arrives and whether it
        sticks or immediately drops.

        After detecting a new connection, waits for the watch to complete GATT
        setup (~10s), then initiates bonding from our side via BlueZ D-Bus.
        This avoids workarea crashes on remote-disconnect by ensuring the link
        is bonded.
        """
        was_connected = False
        connected_since: float | None = None
        import time

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=2.0)
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass

            try:
                is_conn = await self._check_connected()
                is_adv = await self._check_advertising()
            except Exception as exc:
                logger.warning("CONNECTION MONITOR: state check failed: {}", exc)
                continue

            if is_conn != was_connected:
                if is_conn:
                    connected_since = time.monotonic()
                    logger.info(
                        "CONNECTION MONITOR: central CONNECTED "
                        "(advertising={}, settling={})",
                        is_adv,
                        self._subscribe_settling,
                    )
                    if self._corebluetooth_subscribe_via_monitor:
                        self._has_subscribers = True
                        self._schedule_deferred_push()
                    try:
                        await self._log_connection_parameters()
                    except Exception as exc:
                        logger.warning("CONNECTION MONITOR: log params failed: {}", exc)

                else:
                    duration = (
                        f"{time.monotonic() - connected_since:.1f}s"
                        if connected_since is not None
                        else "?"
                    )
                    logger.info(
                        "CONNECTION MONITOR: central DISCONNECTED "
                        "(was connected for {}, advertising={}, settling={})",
                        duration,
                        is_adv,
                        self._subscribe_settling,
                    )
                    connected_since = None
                    # Log BlueZ disconnect reason if available
                    if sys.platform == "linux":
                        try:
                            await self._log_disconnect_reason()
                        except Exception as exc:
                            logger.warning(
                                "CONNECTION MONITOR: disconnect reason query failed: {}",
                                exc,
                            )
                was_connected = is_conn
                self._device_connected = is_conn
                if not is_conn:
                    self._has_subscribers = False
            elif is_conn:
                # Heartbeat while connected — proves the monitor is alive
                # and that is_connected() still returns True.
                uptime = (
                    f"{time.monotonic() - connected_since:.0f}s"
                    if connected_since is not None
                    else "?"
                )
                logger.debug(
                    "CONNECTION MONITOR: connected (uptime={}, advertising={}, settling={})",
                    uptime,
                    is_adv,
                    self._subscribe_settling,
                )
            else:
                # Idle — not connected.
                logger.debug(
                    "CONNECTION MONITOR: idle (advertising={})",
                    is_adv,
                )

    async def _log_connection_parameters(self) -> None:
        """Query BlueZ D-Bus for connected device properties (RSSI, pairing
        state, address type, etc.) and log them for debugging.

        On Linux, BlueZ exposes device properties at:
            /org/bluez/hci0/dev_XX_XX_XX_XX_XX_XX
        Properties of interest: RSSI, Connected, Paired, Trusted,
        AddressType, ServicesResolved, ManufacturerData.
        """
        if not _BLE_DIAGNOSTICS_ENABLED or sys.platform != "linux":
            return

        try:
            import subprocess

            # Use bluetoothctl to list connected devices and their info
            result = subprocess.run(
                ["bluetoothctl", "devices", "Connected"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                logger.debug("No connected devices found via bluetoothctl")
                return

            for line in result.stdout.strip().splitlines():
                # Format: "Device XX:XX:XX:XX:XX:XX Name"
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "Device":
                    addr = parts[1]
                    await self._log_device_info(addr)
        except Exception as exc:
            logger.debug("Connection parameter query failed: {}", exc)

    async def _log_device_info(self, address: str) -> None:
        """Log detailed BlueZ device info for a specific address."""
        if not _BLE_DIAGNOSTICS_ENABLED:
            return
        import subprocess

        try:
            result = subprocess.run(
                ["bluetoothctl", "info", address],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return

            # Log interesting properties
            props_of_interest = (
                "RSSI",
                "Connected",
                "Paired",
                "Trusted",
                "Bonded",
                "AddressType",
                "ServicesResolved",
                "Appearance",
                "Icon",
                "Class",
                "Modalias",
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if any(
                    line.startswith(p + ":") or ("\t" + p + ":") in line
                    for p in props_of_interest
                ):
                    logger.info("  BLE device {}: {}", address, line)

            # Also try to get connection parameters via hcitool
            # (interval, latency, supervision timeout)
            await self._log_hci_conn_info(address)
        except Exception as exc:
            logger.debug("Device info query failed for {}: {}", address, exc)

    async def _log_hci_conn_info(self, address: str) -> None:
        """Query HCI-level connection info (interval, latency, timeout).

        BlueZ 5.72 deprecates most hcitool commands. We use:
          - hcitool con: active connections (still works)
          - btmgmt conn-info: RSSI + TX power (BlueZ 5.56+)
          - /sys/kernel/debug/bluetooth/hci0/: connection parameters (needs root)
        """
        if not _BLE_DIAGNOSTICS_ENABLED:
            return
        import subprocess

        # Active connections with handle
        try:
            result = subprocess.run(
                ["hcitool", "con"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if address.upper() in line.upper():
                        logger.info("  HCI connection: {}", line.strip())
        except Exception:
            pass

        # btmgmt conn-info gives RSSI + TX power on modern BlueZ
        try:
            result = subprocess.run(
                ["btmgmt", "conn-info", "-t", "le", address],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    logger.info("  btmgmt conn-info: {}", line.strip())
        except Exception:
            pass

        # Try reading LE connection parameters from kernel debug filesystem
        # Format: /sys/kernel/debug/bluetooth/hci0/conn_min_interval etc.
        # These are per-device after BlueZ 5.50+ in /sys/kernel/debug/
        try:
            addr_path = address.upper().replace(":", "_")
            debug_base = f"/sys/kernel/debug/bluetooth/hci0/{addr_path}"
            import os

            if os.path.isdir(debug_base):
                for param in (
                    "conn_min_interval",
                    "conn_max_interval",
                    "conn_latency",
                    "supervision_timeout",
                ):
                    path = f"{debug_base}/{param}"
                    if os.path.exists(path):
                        with open(path) as f:
                            val = f.read().strip()
                            logger.info("  LE param {}: {}", param, val)
            else:
                # Try global connection params as fallback
                debug_global = "/sys/kernel/debug/bluetooth/hci0"
                if os.path.isdir(debug_global):
                    for param in (
                        "conn_min_interval",
                        "conn_max_interval",
                        "conn_latency",
                        "supervision_timeout",
                    ):
                        path = f"{debug_global}/{param}"
                        if os.path.exists(path):
                            with open(path) as f:
                                val = f.read().strip()
                                logger.info("  LE default {}: {}", param, val)
        except Exception as exc:
            logger.debug("  debug filesystem read failed: {}", exc)

    async def _log_disconnect_reason(self) -> None:
        """Query BlueZ / kernel logs for the BLE disconnect reason code.

        The HCI Disconnection Complete event carries a 1-byte reason code
        (BT Core Spec Vol 1, Part F, §1.3).  Common codes:
          0x08  Connection Timeout (supervision timeout expired)
          0x13  Remote User Terminated Connection
          0x16  Connection Terminated by Local Host
          0x3E  Connection Failed to be Established
        """
        if not _BLE_DIAGNOSTICS_ENABLED:
            return
        import subprocess

        # Check dmesg for the most recent BLE disconnect event
        try:
            result = subprocess.run(
                ["dmesg", "--time-format=reltime", "-l", "info,debug"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                # Look for the last HCI disconnect line
                for line in reversed(result.stdout.splitlines()[-50:]):
                    if "disconn" in line.lower() and (
                        "hci" in line.lower() or "bluetooth" in line.lower()
                    ):
                        logger.info("  Kernel disconnect: {}", line.strip())
                        break
        except Exception:
            pass

        # Also check bluetoothctl for recently removed/disconnected devices
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    logger.debug("  BlueZ known device: {}", line.strip())
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Notification queue — serializes all BLE notifications with spacing
    # ------------------------------------------------------------------

    async def _drain_notify_queue(self) -> None:
        """Loop forever: dequeue (service_uuid, char_uuid) and call update_value
        with _NOTIFY_SPACING_S between consecutive notifications."""
        while True:
            service_uuid, char_uuid = await self._notify_queue.get()
            if self._server is None:
                continue
            name = self._char_name(char_uuid.upper())
            try:
                self._server.update_value(service_uuid, char_uuid)
                logger.debug(
                    "Notify sent: {} (queue={})", name, self._notify_queue.qsize()
                )
            except Exception as exc:
                logger.warning("Notify FAILED for {}: {}", name, exc)
            await asyncio.sleep(_NOTIFY_SPACING_S)

    def _enqueue_notify(self, char_uuid: str) -> None:
        """Enqueue a notification for the given characteristic UUID.

        Suppressed when no central is connected or no CCCD subscribe has been
        received — per BLE spec, peripherals must not notify before the central
        subscribes.  Characteristic *values* are still updated by callers so a
        newly-connected client reads the latest state.
        """
        if not self._device_connected or not self._has_subscribers:
            return
        self._notify_queue.put_nowait((self._service_uuid, char_uuid))

    @staticmethod
    def _char_name(uuid: str) -> str:
        """Human-readable name for a characteristic UUID."""
        u = uuid.upper()
        if u == HISTORY_CHAR_UUID.upper():
            return "HISTORY"
        if u == SESSION_CHAR_UUID.upper():
            return "SESSION"
        if u == ALERT_CHAR_UUID.upper():
            return "ALERT"
        if u == STATS_CLAUDE_CHAR_UUID.upper():
            return "STATS_CLAUDE"
        if u == STATS_OPENCODE_CHAR_UUID.upper():
            return "STATS_OPENCODE"
        if u == USAGE_CHAR_UUID.upper():
            return "USAGE"
        if u == PROVIDER_USAGE_CHAR_UUID.upper():
            return "PROVIDER_USAGE"
        return uuid[:8]

    def _handle_read(
        self, characteristic: BlessGATTCharacteristic, **kwargs: Any
    ) -> bytearray:
        """Return the current value when a central reads a characteristic."""
        uuid = str(characteristic.uuid).upper()
        name = self._char_name(uuid)
        if uuid == HISTORY_CHAR_UUID.upper():
            val = bytearray(self._last_frame)
            logger.info("GATT read: {} ({} bytes)", name, len(val))
            return val
        if uuid == SESSION_CHAR_UUID.upper():
            val = bytearray(self._session_active)
            logger.info("GATT read: {} = 0x{}", name, val.hex())
            return val
        if uuid == ALERT_CHAR_UUID.upper():
            val = bytearray(self._current_alert)
            logger.info("GATT read: {} = 0x{}", name, val.hex())
            return val
        if uuid == STATS_CLAUDE_CHAR_UUID.upper():
            val = bytearray(self._multi.payload_for("claude"))
            logger.info("GATT read: {} ({} bytes)", name, len(val))
            return val
        if uuid == STATS_OPENCODE_CHAR_UUID.upper():
            val = bytearray(self._multi.payload_for("opencode"))
            logger.info("GATT read: {} ({} bytes)", name, len(val))
            return val
        if uuid == USAGE_CHAR_UUID.upper():
            val = bytearray(self._legacy_usage_payload())
            logger.info("GATT read: {} ({} bytes)", name, len(val))
            return val
        if uuid == PROVIDER_USAGE_CHAR_UUID.upper():
            val = bytearray(self._usage_payload())
            logger.info("GATT read: {} ({} bytes)", name, len(val))
            return val
        logger.warning("GATT read: unknown char {}", uuid)
        return bytearray()

    def _handle_write(
        self, characteristic: BlessGATTCharacteristic, value: Any, **kwargs: Any
    ) -> None:
        """Handle write requests. CCCD subscribes (0x01 0x00) get special treatment:
        we log them clearly and push current stats immediately so the watch sees
        valid values without waiting up to a full keepalive tick.
        """
        try:
            raw = bytes(value) if value is not None else b""
        except Exception:
            raw = b""

        if raw == b"\x01\x00":
            # Notification subscribe — do NOT push immediately.  Pushing while
            # CCCD writes are still in-flight creates a burst of 3+ notifications
            # that lands before the subscribe phase completes.
            #
            # Instead, schedule a single deferred push that fires 1.5s after
            # the *last* subscribe arrives — giving the watch time to finish
            # all CCCD writes and transition to PHASE_READY cleanly.
            uuid = str(characteristic.uuid).upper()
            name = self._char_name(uuid)
            logger.info("CCCD subscribe: {} ({})", name, uuid)
            self._has_subscribers = True
            self._schedule_deferred_push()
            return

        if raw == b"\x00\x00":
            uuid = str(characteristic.uuid).upper()
            name = self._char_name(uuid)
            logger.info("CCCD unsubscribe: {} ({})", name, uuid)
            return

        uuid = str(characteristic.uuid).upper()
        name = self._char_name(uuid)
        logger.info("GATT write: {} = 0x{} ({} bytes)", name, raw.hex(), len(raw))

    # ------------------------------------------------------------------
    # Deferred post-subscribe push
    # ------------------------------------------------------------------

    def _schedule_deferred_push(self) -> None:
        """(Re)schedule a single push of all current values 1.5s from now.

        Each CCCD subscribe resets the timer, so the push only fires once —
        1.5s after the *last* subscribe.  This gives the watch time to
        complete all CCCD writes, transition to PHASE_READY, and settle
        before any notifications arrive.
        """
        self._subscribe_settling = True
        if self._deferred_push_handle is not None:
            self._deferred_push_handle.cancel()
        loop = asyncio.get_event_loop()
        self._deferred_push_handle = loop.call_later(1.5, self._fire_deferred_push)

    def _fire_deferred_push(self) -> None:
        """Push current values for all subscribed characteristics."""
        self._deferred_push_handle = None
        self._subscribe_settling = False
        self._device_connected = True
        logger.info("Deferred post-subscribe push firing")
        self._notify_history()
        self._push_stats(force=True)
        self._push_usage(force=True)

    # ------------------------------------------------------------------
    # History frame push
    # ------------------------------------------------------------------

    def _push_event(self, ev: CanonicalEvent, session_active: bool = True) -> None:
        """Encode ``ev`` into a binary frame and notify HISTORY_CHAR_UUID.

        Every canonical event produces exactly one frame; the watch handles
        deduplication, history accumulation, and spinner animation locally.
        Suppresses the notify when the frame is byte-identical to the last
        to avoid unnecessary notification load.
        """
        frame = encode_event(ev)
        if len(frame) > MAX_FRAME_LEN:
            logger.warning(
                "Encoded frame exceeds MAX_FRAME_LEN ({} > {}) — truncating",
                len(frame),
                MAX_FRAME_LEN,
            )
            frame = frame[:MAX_FRAME_LEN]

        frame_changed = frame != self._last_frame
        self._last_frame = frame
        self._session_active = b"\x01" if session_active else b"\x00"

        if self._server is None:
            return

        try:
            self._server.get_characteristic(SESSION_CHAR_UUID).value = bytearray(
                self._session_active
            )
            if frame_changed:
                self._server.get_characteristic(HISTORY_CHAR_UUID).value = bytearray(
                    frame
                )
                self._enqueue_notify(HISTORY_CHAR_UUID)
                logger.info(
                    "History frame pushed ({} bytes): {}", len(frame), frame.hex()
                )
            else:
                logger.debug("History frame unchanged, notification suppressed")
        except Exception as exc:
            logger.warning("Failed to update BLE characteristic: {}", exc)

    async def _send_alert(self, alert_type: int) -> None:
        """Write alert byte to ALERT_CHAR_UUID, then reset to 0x00 after 500 ms."""
        cfg = load_config()
        effective_alert = alert_type if cfg.haptic_allowed() else ALERT_NONE

        self._current_alert = bytes([effective_alert])

        if self._server is not None:
            try:
                self._server.get_characteristic(ALERT_CHAR_UUID).value = bytearray(
                    self._current_alert
                )
                self._enqueue_notify(ALERT_CHAR_UUID)
                if effective_alert != ALERT_NONE:
                    logger.info("Alert sent: 0x{:02X}", effective_alert)
            except Exception as exc:
                logger.warning("Failed to send alert: {}", exc)

        if effective_alert != ALERT_NONE:
            await asyncio.sleep(0.5)
            self._current_alert = b"\x00"
            if self._server is not None:
                try:
                    self._server.get_characteristic(ALERT_CHAR_UUID).value = bytearray(
                        b"\x00"
                    )
                    self._enqueue_notify(ALERT_CHAR_UUID)
                    logger.debug("Alert reset to 0x00")
                except Exception as exc:
                    logger.warning("Failed to reset alert: {}", exc)

    def _notify_history(self) -> None:
        """Re-push the most recent HISTORY frame to subscribers.

        Used on CCCD subscribe so a freshly connected watch sees at least
        the latest event immediately rather than waiting for the next real
        message to drive ``_push_event``.  No-op when no frame has been
        produced yet (``_last_frame == b""``).
        """
        if self._server is None or not self._last_frame:
            return
        try:
            self._server.get_characteristic(HISTORY_CHAR_UUID).value = bytearray(
                self._last_frame
            )
            self._enqueue_notify(HISTORY_CHAR_UUID)
            logger.debug("History frame re-notified ({} bytes)", len(self._last_frame))
        except Exception as exc:
            logger.warning("Failed to notify history: {}", exc)

    def _push_stats_for(self, provider: str, force: bool = False) -> None:
        """Update the stats characteristic value for a single provider and notify.

        Skipped entirely when no central is connected — the characteristic
        value will be pushed on the next connect via _fire_deferred_push.
        """
        if self._server is None or not self._device_connected:
            return
        uuid = (
            STATS_CLAUDE_CHAR_UUID if provider == "claude" else STATS_OPENCODE_CHAR_UUID
        )
        try:
            payload = self._multi.payload_for(provider)
            if not force and self._last_pushed_stats.get(provider) == payload:
                return
            self._server.get_characteristic(uuid).value = bytearray(payload)
            self._enqueue_notify(uuid)
            self._last_pushed_stats[provider] = payload
            logger.debug("Stats pushed [{}] ({} bytes)", provider, len(payload))
        except Exception as exc:
            logger.warning("Failed to update {} stats: {}", provider, exc)

    def _push_stats(self, force: bool = False) -> None:
        """Push both per-provider STATS characteristics, suppressing identical payloads."""
        self._push_stats_for("claude", force=force)
        self._push_stats_for("opencode", force=force)

    def _usage_payload(self) -> bytes:
        """Serialise Claude/Codex weekly usage as compact JSON ({"c":..,"x":..})."""
        return json.dumps(self._usage, separators=(",", ":")).encode("utf-8")

    def _legacy_usage_payload(self) -> bytes:
        """Keep the released watch app's session/weekly wire shape working."""
        return json.dumps(self._legacy_usage, separators=(",", ":")).encode("utf-8")

    def _push_usage(self, force: bool = False) -> None:
        """Update the USAGE characteristic value and notify, suppressing identical payloads."""
        if self._server is None or not self._device_connected:
            return
        try:
            payload = self._usage_payload()
            legacy_payload = self._legacy_usage_payload()
            payload_signature = legacy_payload + b"|" + payload
            if len(payload) > MAX_USAGE_LEN or len(legacy_payload) > MAX_USAGE_LEN:
                logger.warning(
                    "Usage payload too large ({} > {} bytes); skipping",
                    len(payload),
                    MAX_USAGE_LEN,
                )
                return
            if not force and self._last_pushed_usage == payload_signature:
                return
            self._server.get_characteristic(USAGE_CHAR_UUID).value = bytearray(
                legacy_payload
            )
            self._server.get_characteristic(PROVIDER_USAGE_CHAR_UUID).value = bytearray(
                payload
            )
            self._enqueue_notify(USAGE_CHAR_UUID)
            self._enqueue_notify(PROVIDER_USAGE_CHAR_UUID)
            self._last_pushed_usage = payload_signature
            logger.debug("Usage pushed ({} bytes)", len(payload))
        except Exception as exc:
            logger.warning("Failed to update usage: {}", exc)

    # ------------------------------------------------------------------
    # Keepalive task — pushes stats AND re-advertises on disconnect
    # ------------------------------------------------------------------

    async def _check_connected(self) -> bool:
        """Is a central currently connected?

        On Linux, bless's is_connected() is unreliable — it only checks whether
        any characteristic has a CCCD subscription, and never clears the list on
        ungraceful disconnects (link loss, supervision timeout, app close).
        We query BlueZ directly via ``bluetoothctl devices Connected`` which
        reflects the actual HCI link state.

        When BlueZ says "not connected" but bless still thinks subscriptions are
        active, we clear bless's stale state so is_connected() returns False on
        subsequent calls and so re-advertising can proceed.
        """
        if self._server is None:
            return False

        # On Linux, bypass bless and ask BlueZ directly.
        if sys.platform == "linux":
            try:
                return await self._check_connected_bluez()
            except Exception as exc:
                logger.debug(
                    "BlueZ connection check failed, falling back to bless: {}", exc
                )

        # Non-Linux or BlueZ check failed: use bless (best-effort).
        try:
            return bool(await self._server.is_connected())
        except Exception:
            return False

    async def _check_connected_bluez(self) -> bool:
        """Query BlueZ for actual HCI connection state.

        Uses ``bluetoothctl devices Connected`` which lists devices whose
        org.bluez.Device1.Connected property is True — this reflects the
        real radio link state, not cached subscription state.

        Side effect: if BlueZ says no device is connected but bless's
        subscribed_characteristics list is non-empty, we clear it. This
        fixes the stale is_connected() == True bug in bless where an
        ungraceful disconnect (link loss) never triggers StopNotify.
        """
        import subprocess

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["bluetoothctl", "devices", "Connected"],
                capture_output=True,
                text=True,
                timeout=3,
            ),
        )
        has_connected = result.returncode == 0 and bool(result.stdout.strip())

        # Fix bless stale state: if BlueZ says nobody is connected but
        # bless still has subscriptions, clear them so bless.is_connected()
        # and re-advertising work correctly.
        if not has_connected:
            try:
                app = self._server.app  # type: ignore[union-attr]
                if (
                    hasattr(app, "subscribed_characteristics")
                    and app.subscribed_characteristics
                ):
                    stale = len(app.subscribed_characteristics)
                    app.subscribed_characteristics.clear()
                    logger.info(
                        "Cleared {} stale bless subscription(s) "
                        "(BlueZ says not connected)",
                        stale,
                    )
            except Exception:
                pass

        return has_connected

    async def _check_advertising(self) -> bool:
        """Best-effort: is the GATT server currently advertising? Returns False
        on any backend that doesn't implement is_advertising() or on any error."""
        if self._server is None:
            return False
        try:
            return bool(await self._server.is_advertising())
        except Exception:
            return False

    async def _restart_advertising(self) -> None:
        """Best-effort re-issue of advertising. Critical on macOS / CoreBluetooth,
        which stops advertising once a central connects and never resumes after
        the central disconnects. No-op on backends already advertising."""
        if self._server is None:
            return
        try:
            await self._server.start()
            logger.info("BLE advertising (re)started")
        except Exception as exc:
            logger.debug("Could not (re)start advertising: {}", exc)

    async def _periodic_stats_task(self) -> None:
        """Keepalive loop. Every tick:
          1. Checks connection / advertising state.
          2. If no central is connected AND we're not advertising, re-issues
             advertising (cross-platform; works around macOS CoreBluetooth
             stopping advertising after the first connect).
          3. Pushes current stats so the watch's STATS chars stay live.
        Cadence switches between LIVE (5 s) and IDLE (10 s) based on whether
        a central was connected on the previous tick."""
        was_connected = False
        while not self._stop_event.is_set():
            interval = (
                _STATS_PUSH_INTERVAL_LIVE
                if was_connected
                else _STATS_PUSH_INTERVAL_IDLE
            )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=interval,
                )
            except asyncio.TimeoutError:
                is_conn = await self._check_connected()
                is_adv = await self._check_advertising()

                if was_connected and not is_conn:
                    logger.info("Central disconnected — restarting advertising")
                was_connected = is_conn
                self._device_connected = is_conn

                if not is_conn and not is_adv:
                    await self._restart_advertising()

                if is_conn and not self._subscribe_settling and self._has_subscribers:
                    self._push_stats()
                    self._push_usage()

    async def _periodic_usage_task(self) -> None:
        """Refresh both weekly subscription windows every five minutes."""
        while not self._stop_event.is_set():
            try:
                claude, codex = await asyncio.gather(
                    asyncio.to_thread(fetch_claude_weekly),
                    asyncio.to_thread(fetch_codex_weekly),
                )
                next_usage = {
                    "c": claude.used_percent if claude is not None else -1,
                    "x": codex.used_percent if codex is not None else -1,
                }
                changed = self._usage != next_usage
                self._usage = next_usage
                if self._legacy_usage["w"] != next_usage["c"]:
                    self._legacy_usage["w"] = next_usage["c"]
                    changed = True
                if changed:
                    logger.info(
                        "Weekly usage refreshed (Claude={}%, Codex={}%)",
                        self._usage["c"],
                        self._usage["x"],
                    )
                    self._push_usage()
            except Exception as exc:
                logger.warning("Weekly usage refresh failed: {}", exc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # IPC message processing (shared by Unix and Windows paths)
    # ------------------------------------------------------------------

    def _process_ipc_message(self, msg: IpcMessage | CanonicalIpcMessage) -> None:
        """Process a decoded IPC message from any provider."""
        provider = getattr(msg, "provider", "claude")
        canonical_event_name = (
            msg.canonical_event if isinstance(msg, CanonicalIpcMessage) else msg.event
        )

        # Daemon control message from `oh-my-wrist set-id`. This is not an AI
        # activity event, so it must not reach session state or HISTORY.
        if (
            isinstance(msg, CanonicalIpcMessage)
            and msg.canonical_event == "config_update"
        ):
            if not _control_token_valid(msg.meta):
                logger.warning("Ignoring unauthorized config_update")
                return
            try:
                connection_id = int((msg.meta or {}).get("connection_id"))
            except (TypeError, ValueError):
                logger.warning("Ignoring config_update without valid connection_id")
                return
            asyncio.ensure_future(self._apply_connection_id(connection_id))
            return

        # Usage quota is not a history
        # event: update the USAGE characteristic and return without touching
        # the session-state engine or the history frame.  Always intercepted
        # here (any provider) so "usage" never reaches the canonical-event
        # validator; only Claude actually carries usage data.
        if isinstance(msg, CanonicalIpcMessage) and msg.canonical_event == "usage":
            if provider == "claude":
                meta = msg.meta or {}
                session = _clamp_pct(meta.get("s", -1))
                weekly = _clamp_pct(meta.get("w", -1))
                self._legacy_usage = {"s": session, "w": weekly}
                self._usage["c"] = weekly
                logger.debug("Usage update: {}", self._usage)
                self._push_usage()
            return

        # Drop unknown canonical events at the daemon seam. A stale plugin or
        # a future event we haven't taught the pipeline about would otherwise
        # render as "? <provider_event>" on the watch, clobbering the live
        # status line under sustained load.
        if isinstance(msg, CanonicalIpcMessage) and msg.canonical_event == "unknown":
            logger.debug(
                "Dropping unknown event from {}: provider_event={}",
                provider,
                msg.provider_event,
            )
            return

        alert_type = msg.alert_type

        logger.debug(
            "IPC message: provider={} canonical_event={} alert=0x{:02X}",
            provider,
            canonical_event_name,
            alert_type,
        )

        # Feed canonical event to the per-provider session state engine.
        # MultiProviderSessionState owns isolation: a session_start from one
        # provider resets only that provider's counters.
        canonical = _ipc_to_canonical(msg)
        self._multi.on_event(canonical)

        # Encode the event into a binary frame and push to HISTORY_CHAR.
        # SESSION_CHAR reflects "any provider active".
        self._push_event(canonical, session_active=self._multi.any_active())

        # Fire haptic alert
        if alert_type != ALERT_NONE:
            asyncio.ensure_future(self._send_alert(alert_type))

        # Push stats
        self._push_stats()

    # ------------------------------------------------------------------
    # IPC socket listener — Unix domain socket
    # ------------------------------------------------------------------

    async def _handle_unix_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=2.0)
            if raw:
                msg = decode_message(raw)
                self._process_ipc_message(msg)
        except Exception as exc:
            logger.debug("IPC client error: {}", exc)
        finally:
            writer.close()

    async def _start_unix_server(self) -> asyncio.AbstractServer:
        socket_path = Path(SOCKET_PATH)
        socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(socket_path.parent, 0o700)
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass
        old_umask = os.umask(0o177) if os.name == "posix" else None
        try:
            server = await asyncio.start_unix_server(
                self._handle_unix_client,
                path=SOCKET_PATH,
            )
        finally:
            if old_umask is not None:
                os.umask(old_umask)
        if os.name == "posix":
            os.chmod(socket_path, 0o600)
        logger.info("IPC Unix socket listening at {}", SOCKET_PATH)
        return server

    # ------------------------------------------------------------------
    # IPC — Windows named pipe
    # ------------------------------------------------------------------

    async def _start_pipe_server(self) -> None:
        """Windows named pipe server loop (runs until stop event)."""
        import ctypes
        import ctypes.wintypes as wt  # type: ignore[import]

        PIPE_ACCESS_INBOUND = 0x00000001
        PIPE_TYPE_BYTE = 0x00000000
        PIPE_WAIT = 0x00000000
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        # Chunk size for a single ReadFile call. Messages are newline-terminated
        # JSON (see protocol.encode_message), so we loop reads until we see "\n"
        # or hit MAX_MESSAGE bytes. Real-world CanonicalIpcMessage payloads
        # (long tool inputs, file paths) routinely exceed 512 bytes.
        CHUNK_SIZE = 4096
        MAX_MESSAGE = 65536

        loop = asyncio.get_event_loop()

        def _read_full_message(h: int) -> bytes:
            """Blocking: read from pipe until we see \\n or pipe closes."""
            chunks: list[bytes] = []
            total = 0
            while total < MAX_MESSAGE:
                buf = ctypes.create_string_buffer(CHUNK_SIZE)
                got = wt.DWORD(0)
                ok = ctypes.windll.kernel32.ReadFile(  # type: ignore[attr-defined]
                    h, buf, CHUNK_SIZE, ctypes.byref(got), None
                )
                if not ok or got.value == 0:
                    break
                chunk = buf.raw[: got.value]
                chunks.append(chunk)
                total += got.value
                if b"\n" in chunk:
                    break
            return b"".join(chunks)

        while not self._stop_event.is_set():
            handle = ctypes.windll.kernel32.CreateNamedPipeW(  # type: ignore[attr-defined]
                NAMED_PIPE_PATH,
                PIPE_ACCESS_INBOUND,
                PIPE_TYPE_BYTE | PIPE_WAIT,
                1,
                CHUNK_SIZE,
                CHUNK_SIZE,
                0,
                None,
            )
            if handle == INVALID_HANDLE_VALUE:
                logger.error("Failed to create named pipe")
                await asyncio.sleep(1)
                continue

            _ = await loop.run_in_executor(
                None,
                lambda h=handle: ctypes.windll.kernel32.ConnectNamedPipe(h, None),  # type: ignore[attr-defined]
            )

            try:
                raw = await loop.run_in_executor(None, _read_full_message, handle)
                if raw:
                    # Strip trailing newline(s) before decoding.
                    raw = raw.rstrip(b"\r\n")
                    if raw:
                        try:
                            msg = decode_message(raw)
                            self._process_ipc_message(msg)
                        except Exception as exc:
                            logger.debug(
                                "Named pipe message decode error ({} bytes): {}",
                                len(raw),
                                exc,
                            )
            except Exception as exc:
                logger.debug("Named pipe read error: {}", exc)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        _setup_logging()
        _write_pid()
        logger.info("BLE daemon starting (PID {})", os.getpid())

        if sys.platform != "win32":
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._stop_event.set)

        try:
            await self._setup_ble()

            stats_task = asyncio.ensure_future(self._periodic_stats_task())
            usage_task = asyncio.ensure_future(self._periodic_usage_task())
            notify_task = asyncio.ensure_future(self._drain_notify_queue())
            conn_monitor_task = asyncio.ensure_future(self._connection_monitor_task())

            if IPC_BACKEND == "unix":
                ipc_server = await self._start_unix_server()
                async with ipc_server:
                    await self._stop_event.wait()
            else:
                await self._start_pipe_server()

            stats_task.cancel()
            usage_task.cancel()
            notify_task.cancel()
            conn_monitor_task.cancel()
            try:
                await stats_task
            except asyncio.CancelledError:
                pass
            try:
                await usage_task
            except asyncio.CancelledError:
                pass
            try:
                await notify_task
            except asyncio.CancelledError:
                pass
            try:
                await conn_monitor_task
            except asyncio.CancelledError:
                pass

        except Exception as exc:
            logger.exception("Daemon error: {}", exc)
        finally:
            if self._agent_bus is not None:
                self._agent_bus.disconnect()
                logger.debug("BlueZ dbus_next agent disconnected")
            if self._agent_process is not None:
                self._agent_process.terminate()
                logger.debug("BlueZ agent subprocess terminated")
            if self._server is not None:
                await self._server.stop()
                logger.info("BLE peripheral stopped")
            _remove_pid()
            if IPC_BACKEND == "unix":
                try:
                    os.unlink(SOCKET_PATH)
                except FileNotFoundError:
                    pass
            logger.info("BLE daemon exited")


def run_daemon() -> None:
    """Entry point for the daemon process."""
    asyncio.run(BleDaemon().run())


if __name__ == "__main__":
    run_daemon()
