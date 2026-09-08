"""
test_end_to_end_normalization.py — End-to-end pipeline tests.

Tests the full path:
  raw Claude Code JSON payload
  → claude_adapter
  → CanonicalEvent
  → history_encoder.encode_event()
  → BLE binary frame (≤ MAX_FRAME_LEN bytes)

Also tests:
  → SessionState.on_event()
  → SessionState.to_ble_payload() (≤ MAX_STATS_LEN bytes)

Simulates a complete realistic Claude Code session.
"""

from __future__ import annotations

import json

import pytest

from ohm.adapters.claude_adapter import adapt_claude_hook
from ohm.history_encoder import decode_frame, encode_event
from ohm.icons import IconId
from ohm.protocol import HookEvent, MAX_FRAME_LEN, MAX_STATS_LEN
from ohm.session_state import SessionState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claude_raw(
    event: str,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    session_id: str | None = "sess-claude",
) -> dict:
    payload: dict = {"event": event}
    if tool_name:
        payload["tool_name"] = tool_name
    if tool_input is not None:
        payload["tool_input"] = tool_input
    if session_id:
        payload["session_id"] = session_id
    return payload


def _decode(ev) -> dict:
    frame = encode_event(ev)
    assert len(frame) <= MAX_FRAME_LEN, (
        f"Frame too long: {len(frame)} bytes for {ev.canonical_event}"
    )
    decoded = decode_frame(frame)
    assert decoded is not None
    return decoded


# ---------------------------------------------------------------------------
# Single-event round-trips — Claude Code
# ---------------------------------------------------------------------------


class TestClaudeRoundTrip:
    @pytest.mark.parametrize(
        "raw,expected_icon",
        [
            (_claude_raw("PreToolUse", "Bash", {"command": "git status"}), IconId.PLAY),
            (_claude_raw("PreToolUse", "Edit", {"path": "main.py"}), IconId.PENCIL),
            (_claude_raw("PreToolUse", "Write", {"path": "out.txt"}), IconId.PENCIL),
            (_claude_raw("PreToolUse", "Read", {"path": "/etc/hosts"}), IconId.EYE),
            (_claude_raw("PreToolUse", "WebFetch", {"url": "https://x"}), IconId.GLOBE),
            (
                _claude_raw("PreToolUse", "WebSearch", {"query": "p async"}),
                IconId.GLOBE,
            ),
            (_claude_raw("PreToolUse", "TodoWrite", {}), IconId.CLIPBOARD),
            (_claude_raw("PreToolUse", "Agent", {}), IconId.WRENCH),
            (_claude_raw("PostToolUse", "Bash", {"command": "ls"}), IconId.CHECK),
            (_claude_raw("Notification"), IconId.PAUSE),
            (_claude_raw("Stop"), IconId.STOP),
        ],
    )
    def test_icon(self, raw, expected_icon):
        hook = HookEvent.model_validate(raw)
        canonical = adapt_claude_hook(hook, raw_payload=raw)
        decoded = _decode(canonical)
        assert decoded["icon"] == int(expected_icon)

    @pytest.mark.parametrize(
        "raw",
        [
            _claude_raw("PreToolUse", "Bash", {"command": "git status"}),
            _claude_raw("PreToolUse", "Edit", {"path": "a" * 100}),
            _claude_raw("PreToolUse", "Write", {"path": "b" * 100}),
            _claude_raw("PostToolUse", "Bash"),
            _claude_raw("Notification"),
            _claude_raw("Stop"),
        ],
    )
    def test_frame_byte_limit(self, raw):
        hook = HookEvent.model_validate(raw)
        canonical = adapt_claude_hook(hook, raw_payload=raw)
        _decode(canonical)

    def test_long_command_truncated(self):
        raw = _claude_raw("PreToolUse", "Bash", {"command": "echo " + "x" * 200})
        hook = HookEvent.model_validate(raw)
        canonical = adapt_claude_hook(hook)
        decoded = _decode(canonical)
        # "echo" is the first word — it fits whole, so the text is just "echo"
        assert decoded["icon"] == int(IconId.PLAY)
        assert decoded["text"].startswith("echo")

    def test_unicode_path_truncated(self):
        raw = _claude_raw("PreToolUse", "Edit", {"path": "ファイル" * 20})
        hook = HookEvent.model_validate(raw)
        canonical = adapt_claude_hook(hook)
        decoded = _decode(canonical)
        # Text must still decode cleanly (multi-byte not split)
        assert isinstance(decoded["text"], str)

    def test_multibyte_path_truncated(self):
        raw = _claude_raw("PreToolUse", "Write", {"path": "ファイル" * 10})
        hook = HookEvent.model_validate(raw)
        canonical = adapt_claude_hook(hook)
        _decode(canonical)


# ---------------------------------------------------------------------------
# Full Claude Code session simulation
# ---------------------------------------------------------------------------


CLAUDE_SESSION = [
    (
        _claude_raw("PreToolUse", "Read", {"path": "README.md"}),
        IconId.EYE,
        "Read README",
    ),
    (_claude_raw("PostToolUse", "Read"), IconId.CHECK, "Read done"),
    (
        _claude_raw("PreToolUse", "Bash", {"command": "git log --oneline"}),
        IconId.PLAY,
        "Git log",
    ),
    (_claude_raw("PostToolUse", "Bash"), IconId.CHECK, "Git log done"),
    (
        _claude_raw("PreToolUse", "Edit", {"path": "src/main.py"}),
        IconId.PENCIL,
        "Edit main",
    ),
    (_claude_raw("PostToolUse", "Edit"), IconId.CHECK, "Edit done"),
    (
        _claude_raw("PreToolUse", "Write", {"path": "src/util.py"}),
        IconId.PENCIL,
        "Write util",
    ),
    (_claude_raw("PostToolUse", "Write"), IconId.CHECK, "Write done"),
    (
        _claude_raw("PreToolUse", "Bash", {"command": "pytest -x"}),
        IconId.PLAY,
        "Run tests",
    ),
    (_claude_raw("PostToolUse", "Bash"), IconId.CHECK, "Tests done"),
    (
        _claude_raw("PreToolUse", "WebSearch", {"query": "pydantic v2"}),
        IconId.GLOBE,
        "Web search",
    ),
    (_claude_raw("PostToolUse", "WebSearch"), IconId.CHECK, "Search done"),
    (_claude_raw("PreToolUse", "TodoWrite", {}), IconId.CLIPBOARD, "Todo update"),
    (_claude_raw("PostToolUse", "TodoWrite"), IconId.CHECK, "Todo done"),
    (_claude_raw("Notification"), IconId.PAUSE, "Idle"),
    (_claude_raw("PreToolUse", "Agent", {}), IconId.WRENCH, "Agent"),
    (_claude_raw("PostToolUse", "Agent"), IconId.CHECK, "Agent done"),
    (_claude_raw("Stop"), IconId.STOP, "Session stop"),
]


class TestClaudeSessionSimulation:
    @pytest.mark.parametrize("raw,expected_icon,desc", CLAUDE_SESSION)
    def test_step(self, raw, expected_icon, desc):
        hook = HookEvent.model_validate(raw)
        canonical = adapt_claude_hook(hook, raw_payload=raw)
        decoded = _decode(canonical)
        assert decoded["icon"] == int(expected_icon), (
            f"[{desc}] expected icon {expected_icon!r}, got {decoded['icon']:#x}"
        )

    def test_session_state_after_full_session(self):
        s = SessionState()
        for raw, _, _ in CLAUDE_SESSION:
            hook = HookEvent.model_validate(raw)
            canonical = adapt_claude_hook(hook, raw_payload=raw)
            s.on_event(canonical)

        assert s.is_active is False
        assert s.bash_count == 2
        assert len(s.edited_files) == 2  # main.py and util.py
        assert s.last_completion_time is not None

        payload = s.to_ble_payload()
        assert len(payload) <= MAX_STATS_LEN
        data = json.loads(payload)
        assert data["b"] == 2
        assert data["e"] == 2
        assert data["t"] > 0
