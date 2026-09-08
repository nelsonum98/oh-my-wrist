"""Distinct synthetic flag-combination coverage for HISTORY frame decoding."""

from __future__ import annotations

from ohm.history_encoder import decode_frame
from ohm.icons import IconId
from ohm.protocol import PROTOCOL_VERSION


class TestDecoderRoundTrip:
    def test_every_flag_combination_roundtrips(self):
        """Build a synthetic frame for every combination of the four defined
        flag bits and verify decode preserves the bits exactly."""
        for flags in range(0x10):  # bits 0..3
            frame = bytes([PROTOCOL_VERSION, int(IconId.PLAY), flags, 2]) + b"hi"
            decoded = decode_frame(frame)
            assert decoded is not None
            assert decoded["flags"] == flags
