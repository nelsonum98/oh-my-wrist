"""Extended edge-case tests for the protocol message codec."""

from __future__ import annotations

import json

import pytest

from ohm.protocol import IpcMessage, decode_message, encode_message


# ============================================================================
# encode_message / decode_message round-trips
# ============================================================================


class TestCodecEdgeCases:
    def test_roundtrip_empty_status(self):
        msg = IpcMessage(status="", event="Stop", ts=1.0)
        decoded = decode_message(encode_message(msg))
        assert decoded.status == ""
        assert decoded.event == "Stop"

    def test_roundtrip_all_ascii(self):
        msg = IpcMessage(status="run: npm", event="PreToolUse", ts=1715000000.0)
        decoded = decode_message(encode_message(msg))
        assert decoded.status == msg.status
        assert decoded.event == msg.event
        assert abs(decoded.ts - msg.ts) < 1e-6

    def test_roundtrip_japanese(self):
        msg = IpcMessage(status="edit: ファイル.py", event="PreToolUse")
        decoded = decode_message(encode_message(msg))
        assert decoded.status == "edit: ファイル.py"

    def test_roundtrip_arabic(self):
        msg = IpcMessage(status="run: مرحبا", event="PreToolUse")
        decoded = decode_message(encode_message(msg))
        assert decoded.status == "run: مرحبا"

    def test_encoded_bytes_are_valid_json_line(self):
        msg = IpcMessage(status="ok: done", event="PostToolUse", ts=1.0)
        raw = encode_message(msg)
        line = raw.decode("utf-8").strip()
        parsed = json.loads(line)
        assert parsed["status"] == "ok: done"
        assert parsed["event"] == "PostToolUse"

    def test_encoded_bytes_end_with_newline(self):
        msg = IpcMessage(status="x", event="y")
        assert encode_message(msg).endswith(b"\n")

    def test_decode_strips_whitespace(self):
        msg = IpcMessage(status="x", event="y", ts=1.0)
        raw = encode_message(msg)
        # Add extra whitespace around the JSON
        padded = b"  " + raw.strip() + b"  \n"
        decoded = decode_message(padded)
        assert decoded.status == "x"

    def test_ts_survives_float_precision(self):
        ts = 1715000000.123456
        msg = IpcMessage(status="x", event="y", ts=ts)
        decoded = decode_message(encode_message(msg))
        assert abs(decoded.ts - ts) < 1e-4

    def test_multiple_messages_independent(self):
        msgs = [
            IpcMessage(status=f"status_{i}", event="PreToolUse", ts=float(i))
            for i in range(10)
        ]
        decoded = [decode_message(encode_message(m)) for m in msgs]
        for i, d in enumerate(decoded):
            assert d.status == f"status_{i}"
            assert d.ts == pytest.approx(float(i))
