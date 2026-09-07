"""
protocol.py — Shared constants, message schema, and IPC transport abstraction.

The IPC transport is selected at import time based on sys.platform:
  - macOS / Linux : Unix domain socket at SOCKET_PATH
  - Windows       : Named pipe at NAMED_PIPE_PATH

Wire format
-----------
The daemon accepts two JSON message formats on the same socket:

1. Legacy IpcMessage (Claude Code hook_relay) — backward compatible.
2. CanonicalIpcMessage (OpenCode plugin and new code) — provider-agnostic.

Both formats are newline-terminated JSON.  The daemon detects the format by
checking for the ``"provider"`` key: if present it is a CanonicalIpcMessage,
otherwise it is a legacy IpcMessage.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# BLE GATT UUIDs — must match the Garmin Connect IQ app exactly
# ---------------------------------------------------------------------------

# GATT Service UUID (custom 128-bit). Must match the Garmin Connect IQ
# app's OHM_SERVICE_UUID constant in BleManager.mc.
OHM_SERVICE_UUID = "0FA155B0-0C21-723A-970C-9821F1C5FFAB"

# Optional user-selected connection discriminator. ID 0 preserves the shipped
# service UUID; non-zero IDs derive a unique service UUID so nearby users do not
# pair with each other's daemons during the scan phase.
CONNECTION_ID_MIN = 0
CONNECTION_ID_MAX = 255
_SERVICE_UUID_CONNECTION_ID_BASE = 0x55


def service_uuid_for_connection_id(connection_id: int) -> str:
    """Return the advertised OHM service UUID for a connection ID.

    The UUID shape stays close to the default service UUID while varying one
    byte of the first segment. The transform is bijective for 0..255, and ID 0
    maps exactly to ``OHM_SERVICE_UUID`` for backward-compatible default use.
    """
    if not CONNECTION_ID_MIN <= connection_id <= CONNECTION_ID_MAX:
        raise ValueError("connection_id must be in 0..255")
    discriminator = (_SERVICE_UUID_CONNECTION_ID_BASE + connection_id) & 0xFF
    return f"0FA1{discriminator:02X}B0-0C21-723A-970C-9821F1C5FFAB"


# Characteristic: per-event history frame (binary, max MAX_FRAME_LEN bytes,
# notifiable).  See HISTORY PROTOCOL below.  The watch maintains the history
# deque locally; each frame is one event.
HISTORY_CHAR_UUID = "0FA155B1-0C21-723A-970C-9821F1C5FFAB"

# Characteristic: session active flag (1 byte: 0x00 = idle, 0x01 = active)
SESSION_CHAR_UUID = "0FA155B2-0C21-723A-970C-9821F1C5FFAB"

# Characteristic: alert type (1 byte) — triggers haptic feedback on the watch
# Value meanings:
#   0x00 = no alert (default/clear)
#   0x01 = IDLE_WAITING  — assistant is waiting for user permission/input
#   0x02 = SESSION_DONE  — session has ended (Stop/session_stop event)
#   0x03 = DESTRUCTIVE   — about to run a potentially destructive shell command
#   0x04 = AGENT_DONE    — a sub-agent has completed
ALERT_CHAR_UUID = "0FA155B3-0C21-723A-970C-9821F1C5FFAB"

# Per-provider session statistics payloads (compact JSON, UTF-8, max 100 bytes, notifiable).
# Each provider gets its own characteristic so concurrent sessions don't clobber each other.
STATS_CLAUDE_CHAR_UUID = "0FA155B4-0C21-723A-970C-9821F1C5FFAB"
STATS_OPENCODE_CHAR_UUID = "0FA155B5-0C21-723A-970C-9821F1C5FFAB"

# Legacy usage payload retained for released watch clients ({"s":..,"w":..}).
USAGE_CHAR_UUID = "0FA155B6-0C21-723A-970C-9821F1C5FFAB"

# Provider usage payload for Claude and Codex ({"c":..,"x":..}).
PROVIDER_USAGE_CHAR_UUID = "0FA155B7-0C21-723A-970C-9821F1C5FFAB"

# History wire protocol
# ----------------------
# Each notification on HISTORY_CHAR_UUID is one self-contained binary frame:
#
#   +--------+--------+--------+--------+----------------+
#   | ver:1  | icon:1 | flags:1| len:1  | text: len B    |
#   +--------+--------+--------+--------+----------------+
#
# The total stays under ATT MTU 23 (so no Long Write is needed).  The watch
# owns the history deque and the icon catalogue; the daemon is stateless w.r.t.
# recent events.  Flag bit definitions live in :mod:`ohm.icons`.
PROTOCOL_VERSION = 0x01
ENTRY_TEXT_MAX = 18  # bytes of UTF-8 text per frame
MAX_FRAME_LEN = 4 + ENTRY_TEXT_MAX  # = 22, fits ATT MTU 23

# Maximum stats payload length in bytes
MAX_STATS_LEN = 100

# Maximum usage payload length in bytes. Realistic payloads are tiny
# ({"c":100,"x":100} == 17 bytes) and must fit a single notify on the default
# ATT MTU of 23 (~20 bytes usable). Enforced defensively in the daemon
# (_push_usage skips oversized payloads).
MAX_USAGE_LEN = 20

# Alert type constants
ALERT_NONE = 0x00
ALERT_IDLE_WAITING = 0x01
ALERT_SESSION_DONE = 0x02
ALERT_DESTRUCTIVE = 0x03
ALERT_AGENT_DONE = 0x04

# ---------------------------------------------------------------------------
# IPC socket / named-pipe paths
# ---------------------------------------------------------------------------


def _default_unix_socket_path() -> str:
    """Return the Unix-socket path using POSIX separators on every host."""
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    return str(PurePosixPath("/tmp") / f"oh-my-wrist-{uid}" / "ohm.sock")


SOCKET_PATH = _default_unix_socket_path()
NAMED_PIPE_PATH = r"\\.\pipe\ohm"

# Resolve at import time which backend to use
if sys.platform == "win32":
    IPC_BACKEND: Literal["pipe", "unix"] = "pipe"
else:
    IPC_BACKEND = "unix"

# ---------------------------------------------------------------------------
# Legacy message schema (IPC wire format — Claude Code hook_relay)
# ---------------------------------------------------------------------------

EventType = Literal[
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "Stop",
    "SessionStart",
    "SessionEnd",
    "SubagentStop",
    "Unknown",
]


class HookEvent(BaseModel):
    """Raw hook event received on stdin from Claude Code.

    Claude Code sends the event type as ``hook_event_name`` on stdin — without
    the alias every real hook falls back to the default ``"Unknown"`` and the
    whole pipeline downstream of this point treats every event as unknown.
    """

    event: str = Field(default="Unknown", alias="hook_event_name")
    tool_name: str | None = Field(default=None)
    tool_input: dict | None = Field(default=None)
    session_id: str | None = Field(default=None)
    notification_type: str | None = Field(default=None)
    agent_id: str | None = Field(default=None)
    agent_type: str | None = Field(default=None)
    cwd: str | None = Field(default=None)
    message: str | None = Field(default=None)

    model_config = {"populate_by_name": True}


class IpcMessage(BaseModel):
    """Legacy IPC message sent from hook_relay to ble_daemon.

    Preserved for backward compatibility.  New code should use
    :class:`CanonicalIpcMessage` instead.
    """

    status: str
    event: str
    event_data: dict = Field(default_factory=dict)
    alert_type: int = Field(default=0)
    ts: float = Field(default_factory=time.time)


# Alias kept for clarity in new code — same model, richer name
HookMessage = IpcMessage

# ---------------------------------------------------------------------------
# Canonical IPC message (multi-provider wire format)
# ---------------------------------------------------------------------------


class CanonicalIpcMessage(BaseModel):
    """Provider-agnostic IPC message sent to ble_daemon.

    This is the preferred wire format for all new code.  The daemon detects
    this format by the presence of the ``"provider"`` key.
    """

    provider: str  # "claude" or "opencode"
    provider_event: str = Field(default="")
    canonical_event: str  # CanonicalEventType literal
    session_id: str | None = Field(default=None)
    tool_name: str | None = Field(default=None)
    label: str | None = Field(default=None)
    path: str | None = Field(default=None)
    status_text: str | None = Field(default=None)
    active: bool = Field(default=True)
    alert_type: int = Field(default=0)
    ts: float = Field(default_factory=time.time)
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# IPC helpers
# ---------------------------------------------------------------------------


def encode_message(msg: IpcMessage | CanonicalIpcMessage) -> bytes:
    """Encode an IPC message to a newline-terminated JSON bytes object."""
    return (msg.model_dump_json() + "\n").encode("utf-8")


def decode_message(raw: bytes) -> IpcMessage | CanonicalIpcMessage:
    """Decode a raw bytes line into the appropriate IPC message type.

    Detects format by presence of the ``"provider"`` key.
    """
    text = raw.decode("utf-8").strip()
    data = json.loads(text)
    if "provider" in data:
        return CanonicalIpcMessage.model_validate(data)
    return IpcMessage.model_validate(data)


# ---------------------------------------------------------------------------
# Async IPC client (used by hook_relay — non-blocking, fire-and-forget)
# ---------------------------------------------------------------------------


async def _send_unix(msg: IpcMessage | CanonicalIpcMessage) -> None:
    """Send a message over a Unix domain socket (non-blocking)."""
    reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
    try:
        writer.write(encode_message(msg))
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _send_pipe(msg: IpcMessage | CanonicalIpcMessage) -> None:
    """Send a message over a Windows named pipe (non-blocking)."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write_pipe_sync, msg)


def _write_pipe_sync(msg: IpcMessage | CanonicalIpcMessage) -> None:
    """Synchronous named-pipe write (runs in a thread on Windows)."""
    import ctypes
    import ctypes.wintypes as wt  # type: ignore[import]

    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    handle = ctypes.windll.kernel32.CreateFileW(  # type: ignore[attr-defined]
        NAMED_PIPE_PATH,
        GENERIC_WRITE,
        0,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise OSError("Named pipe not available")
    try:
        data = encode_message(msg)
        written = wt.DWORD(0)
        ctypes.windll.kernel32.WriteFile(  # type: ignore[attr-defined]
            handle, data, len(data), ctypes.byref(written), None
        )
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]


async def send_to_daemon(msg: IpcMessage | CanonicalIpcMessage) -> None:
    """Send a status message to the running BLE daemon (non-blocking)."""
    if IPC_BACKEND == "unix":
        await _send_unix(msg)
    else:
        await _send_pipe(msg)
