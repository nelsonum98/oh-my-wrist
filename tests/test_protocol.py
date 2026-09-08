"""Tests for the protocol message codec."""

from __future__ import annotations

from ohm.protocol import IpcMessage, decode_message, encode_message


class TestIpcMessageCodec:
    def test_encode_decode_roundtrip(self):
        original = IpcMessage(
            status="edit: main.py", event="PreToolUse", ts=1715000000.123
        )
        encoded = encode_message(original)
        decoded = decode_message(encoded)
        assert decoded.status == original.status
        assert decoded.event == original.event
        assert abs(decoded.ts - original.ts) < 1e-6

    def test_encode_ends_with_newline(self):
        msg = IpcMessage(status="ok: done", event="PostToolUse")
        encoded = encode_message(msg)
        assert encoded.endswith(b"\n")
