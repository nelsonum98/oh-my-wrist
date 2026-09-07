"""
history_encoder.py — Convert a CanonicalEvent into a single binary frame for
the watch HISTORY characteristic.

Frame layout (matches :data:`protocol.MAX_FRAME_LEN` = 22 bytes max, well under
ATT MTU 23 so no Long Write is needed):

    +--------+--------+--------+--------+----------------+
    | ver:1  | icon:1 | flags:1| len:1  | text: len B    |
    +--------+--------+--------+--------+----------------+

The watch owns the history deque and the icon glyph catalogue.  This module
is therefore the **only** place in the codebase that decides which icon and
flag bits represent a given canonical event — the watch never sees an emoji
byte on the wire.

Each call to :func:`encode_event` is pure and stateless: completion
correlation between tool_start / tool_end is handled by the watch via the
``FLAG_CLEAR_PREV_SPINNER`` bit set on tool_end-derived frames.
"""

from __future__ import annotations

import os

from ohm.icons import (
    FLAG_ACCENT,
    FLAG_CLEAR_PREV_SPINNER,
    FLAG_SPINNER,
    FLAGS_NONE,
    IconId,
)
from ohm.protocol import ENTRY_TEXT_MAX, PROTOCOL_VERSION
from ohm.provider_types import CanonicalEvent

# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------


def _basename(path: str) -> str:
    """Return the filename component of a path string."""
    return os.path.basename(path)


def _first_word(command: str) -> str:
    """Return the first whitespace-separated token of a shell command."""
    parts = command.split()
    return parts[0] if parts else ""


def _shell_label(command: str) -> str:
    """Extract a meaningful display label from a shell command.

    Skips leading ``cd …`` segments in compound commands so that
    ``cd /repo && pytest -x`` shows "pytest" instead of "cd".
    """
    import re

    segments = re.split(r"\s*(?:&&|;)\s*", command.strip())
    non_cd = [s for s in segments if s and not re.match(r"^cd\s|^cd$", s)]
    if non_cd:
        return _first_word(non_cd[0])
    if segments and segments[-1].strip().startswith("cd"):
        target = segments[-1].strip().split()[1:]
        if target:
            return os.path.basename(target[-1].rstrip("/"))
    return _first_word(command)


def _utf8_truncate(text: str, max_bytes: int) -> str:
    """Truncate ``text`` so its UTF-8 encoding is at most ``max_bytes`` bytes.

    Never splits a multi-byte UTF-8 sequence: walks backward from the byte
    limit until it finds a byte that is either ASCII (< 0x80) or the start
    of a multi-byte sequence (≥ 0xC0).
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    cut = max_bytes
    while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
        cut -= 1
    return encoded[:cut].decode("utf-8")


# ---------------------------------------------------------------------------
# Classifier: CanonicalEvent → (icon, flags, text)
# ---------------------------------------------------------------------------


def _classify(ev: CanonicalEvent) -> tuple[int, int, str]:
    """Map a canonical event to an icon ID, a flags byte, and a display label."""
    ce = ev.canonical_event
    intent = ev.tool_intent
    label = ev.label or ""
    path = ev.path or ""
    meta = ev.meta or {}

    if ce == "tool_start":
        if intent == "shell":
            cmd = label or meta.get("command", "")
            return IconId.PLAY, FLAG_SPINNER, _shell_label(cmd)
        if intent == "edit":
            return IconId.PENCIL, FLAG_SPINNER, _basename(path or label)
        if intent == "read":
            return IconId.EYE, FLAG_SPINNER, _basename(path or label)
        if intent == "web":
            return IconId.GLOBE, FLAG_SPINNER, ""
        if intent == "todo":
            return IconId.CLIPBOARD, FLAG_SPINNER, ""
        if intent == "agent":
            return IconId.WRENCH, FLAG_SPINNER, ev.tool_name or label
        # unknown intent — still spin while the tool runs
        return IconId.WRENCH, FLAG_SPINNER, ev.tool_name or label

    if ce == "tool_end":
        # Belt-and-suspenders: forward available label so orphaned completions
        # that miss watch-side collapse still carry context (avoids bare "ok").
        # Normal collapse keeps the original tool_start label and ignores this.
        text = _basename(path or label) if (path or label) else ""
        return IconId.CHECK, FLAG_CLEAR_PREV_SPINNER, text

    if ce == "session_start":
        return IconId.GREEN_CIRCLE, FLAGS_NONE, ""

    if ce == "session_idle":
        return IconId.PAUSE, FLAG_CLEAR_PREV_SPINNER, ""

    if ce == "session_stop":
        return IconId.STOP, FLAG_CLEAR_PREV_SPINNER, ""

    if ce == "session_error":
        return IconId.WARNING, FLAG_ACCENT | FLAG_CLEAR_PREV_SPINNER, ""

    if ce == "file_edit":
        return IconId.PENCIL, FLAGS_NONE, _basename(path or label)

    if ce == "todo_update":
        return IconId.CLIPBOARD, FLAGS_NONE, ""

    if ce == "permission_request":
        text = label or meta.get("title", "") or meta.get("message", "")
        return IconId.QUESTION, FLAG_ACCENT, text

    if ce == "permission_reply":
        approved = meta.get("approved", meta.get("decision"))
        if approved is False or str(label).lower() == "denied":
            return IconId.NO_ENTRY, FLAG_ACCENT, ""
        return IconId.CHECK, FLAG_CLEAR_PREV_SPINNER, ""

    if ce == "command":
        cmd = label or meta.get("command", "")
        return IconId.PLAY, FLAGS_NONE, _shell_label(cmd)

    if ce == "status":
        text = ev.status_text or label or ""
        return IconId.STATUS_DOT, FLAGS_NONE, text

    if ce == "job_done":
        return IconId.CHECK, FLAG_CLEAR_PREV_SPINNER, label

    if ce == "job_needs_input":
        return IconId.QUESTION, FLAG_ACCENT | FLAG_CLEAR_PREV_SPINNER, label

    if ce == "job_failed":
        return IconId.WARNING, FLAG_ACCENT | FLAG_CLEAR_PREV_SPINNER, label

    # unknown / future events
    return IconId.QUESTION, FLAGS_NONE, ev.provider_event or ce


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encode_event(ev: CanonicalEvent) -> bytes:
    """Encode a canonical event as one binary frame for HISTORY_CHAR_UUID.

    The returned frame is at most :data:`protocol.MAX_FRAME_LEN` bytes and
    is safe to send as a single ATT MTU 23 notification.
    """
    icon, flags, text = _classify(ev)
    truncated = _utf8_truncate(text, ENTRY_TEXT_MAX)
    text_bytes = truncated.encode("utf-8")
    return (
        bytes([PROTOCOL_VERSION, int(icon), int(flags), len(text_bytes)]) + text_bytes
    )


def decode_frame(frame: bytes) -> dict | None:
    """Decode a frame for testing / debugging.  Returns ``None`` on any error.

    The watch decoder (``garmin/source/HistoryDecoder.mc``) implements the
    same logic in Monkey C; this Python mirror is used by unit tests for
    round-trip verification.
    """
    if not frame or len(frame) < 4:
        return None
    if frame[0] != PROTOCOL_VERSION:
        return None
    icon = frame[1]
    flags = frame[2]
    length = frame[3]
    if 4 + length > len(frame):
        return None
    try:
        text = frame[4 : 4 + length].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return {"icon": icon, "flags": flags, "text": text}
