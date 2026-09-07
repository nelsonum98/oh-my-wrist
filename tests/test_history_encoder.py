"""
Tests for history_encoder.encode_event — the CanonicalEvent → binary frame
classifier that drives the watch display.
"""

from __future__ import annotations

import pytest

from ohm.history_encoder import _classify, _shell_label, decode_frame, encode_event
from ohm.icons import (
    FLAG_ACCENT,
    FLAG_CLEAR_PREV_SPINNER,
    FLAG_SPINNER,
    FLAGS_NONE,
    IconId,
)
from ohm.protocol import MAX_FRAME_LEN, PROTOCOL_VERSION
from ohm.provider_types import CanonicalEvent


def _ev(canonical: str, **kwargs) -> CanonicalEvent:
    base = {"provider": "claude", "canonical_event": canonical}
    base.update(kwargs)
    return CanonicalEvent(**base)


# ============================================================================
# tool_start — one row per intent
# ============================================================================


class TestToolStartClassification:
    def test_shell_intent(self):
        icon, flags, text = _classify(
            _ev("tool_start", tool_name="Bash", meta={"command": "pytest -x tests/"})
        )
        assert icon == IconId.PLAY
        assert flags & FLAG_SPINNER
        assert text == "pytest"

    def test_shell_intent_uses_label_when_present(self):
        icon, flags, text = _classify(
            _ev("tool_start", tool_name="Bash", label="git status -s")
        )
        assert icon == IconId.PLAY
        assert text == "git"

    def test_edit_intent(self):
        icon, flags, text = _classify(
            _ev("tool_start", tool_name="Edit", path="/repo/src/main.py")
        )
        assert icon == IconId.PENCIL
        assert flags & FLAG_SPINNER
        assert text == "main.py"

    def test_edit_falls_back_to_label_if_no_path(self):
        icon, _, text = _classify(
            _ev("tool_start", tool_name="Write", label="/repo/out.txt")
        )
        assert icon == IconId.PENCIL
        assert text == "out.txt"

    def test_read_intent(self):
        icon, flags, text = _classify(
            _ev("tool_start", tool_name="Read", path="/etc/hosts")
        )
        assert icon == IconId.EYE
        assert flags & FLAG_SPINNER
        assert text == "hosts"

    def test_web_intent(self):
        icon, flags, text = _classify(
            _ev("tool_start", tool_name="WebFetch", label="https://x")
        )
        assert icon == IconId.GLOBE
        assert flags & FLAG_SPINNER
        assert text == ""

    def test_todo_intent(self):
        icon, flags, text = _classify(_ev("tool_start", tool_name="TodoWrite"))
        assert icon == IconId.CLIPBOARD
        assert flags & FLAG_SPINNER
        assert text == ""

    def test_agent_intent(self):
        icon, flags, text = _classify(
            _ev("tool_start", tool_name="Agent", label="research-bot")
        )
        assert icon == IconId.WRENCH
        assert flags & FLAG_SPINNER
        assert text == "Agent"

    def test_unknown_intent_uses_tool_name(self):
        icon, flags, text = _classify(_ev("tool_start", tool_name="MysteryTool"))
        assert icon == IconId.WRENCH
        assert flags & FLAG_SPINNER
        assert text == "MysteryTool"

    def test_shell_skips_leading_cd(self):
        _, _, text = _classify(
            _ev("tool_start", tool_name="Bash", label="cd /repo && pytest -x")
        )
        assert text == "pytest"

    def test_shell_standalone_cd_shows_dirname(self):
        _, _, text = _classify(
            _ev("tool_start", tool_name="Bash", label="cd /home/jason/ai/oh-my-wrist")
        )
        assert text == "oh-my-wrist"


# ============================================================================
# _shell_label — cd-skipping extraction
# ============================================================================


class TestShellLabel:
    def test_simple_command(self):
        assert _shell_label("pytest -x tests/") == "pytest"

    def test_cd_then_ampamp(self):
        assert _shell_label("cd /repo && git status") == "git"

    def test_cd_then_semicolon(self):
        assert _shell_label("cd /repo; npm test") == "npm"

    def test_multiple_cds_then_command(self):
        assert _shell_label("cd /a && cd sub && ruff check .") == "ruff"

    def test_standalone_cd_returns_basename(self):
        assert _shell_label("cd /home/user/project") == "project"

    def test_standalone_cd_trailing_slash(self):
        assert _shell_label("cd /home/user/project/") == "project"

    def test_bare_cd(self):
        assert _shell_label("cd") == "cd"

    def test_empty_string(self):
        assert _shell_label("") == ""

    def test_no_cd_at_all(self):
        assert _shell_label("git push origin main") == "git"

    def test_cd_in_middle_not_skipped(self):
        assert _shell_label("echo hello && cd /tmp") == "echo"


# ============================================================================
# Lifecycle / non-tool events
# ============================================================================


class TestLifecycleEvents:
    def test_tool_end(self):
        icon, flags, text = _classify(_ev("tool_end"))
        assert icon == IconId.CHECK
        assert flags & FLAG_CLEAR_PREV_SPINNER
        assert not (flags & FLAG_SPINNER)
        assert text == ""

    def test_session_start(self):
        icon, flags, _ = _classify(_ev("session_start"))
        assert icon == IconId.GREEN_CIRCLE
        assert flags == FLAGS_NONE

    def test_session_idle_clears_spinner(self):
        _, flags, _ = _classify(_ev("session_idle"))
        assert flags & FLAG_CLEAR_PREV_SPINNER

    def test_session_stop_clears_spinner(self):
        icon, flags, _ = _classify(_ev("session_stop"))
        assert icon == IconId.STOP
        assert flags & FLAG_CLEAR_PREV_SPINNER

    def test_session_error_accent_and_clear(self):
        icon, flags, _ = _classify(_ev("session_error"))
        assert icon == IconId.WARNING
        assert flags & FLAG_ACCENT
        assert flags & FLAG_CLEAR_PREV_SPINNER

    def test_file_edit(self):
        icon, flags, text = _classify(_ev("file_edit", path="/repo/app.py"))
        assert icon == IconId.PENCIL
        assert flags == FLAGS_NONE
        assert text == "app.py"

    def test_todo_update(self):
        icon, _, _ = _classify(_ev("todo_update"))
        assert icon == IconId.CLIPBOARD

    def test_permission_request_accent(self):
        icon, flags, _ = _classify(_ev("permission_request"))
        assert icon == IconId.QUESTION
        assert flags & FLAG_ACCENT

    def test_permission_reply_approved(self):
        icon, _, _ = _classify(_ev("permission_reply", meta={"approved": True}))
        assert icon == IconId.CHECK

    def test_permission_reply_denied_by_meta(self):
        icon, flags, _ = _classify(_ev("permission_reply", meta={"approved": False}))
        assert icon == IconId.NO_ENTRY
        assert flags & FLAG_ACCENT

    def test_permission_reply_denied_by_label(self):
        icon, _, _ = _classify(_ev("permission_reply", label="denied"))
        assert icon == IconId.NO_ENTRY

    def test_command_uses_first_word(self):
        icon, _, text = _classify(_ev("command", label="git push origin main"))
        assert icon == IconId.PLAY
        assert text == "git"

    def test_status_passes_text(self):
        icon, _, text = _classify(_ev("status", status_text="indexing"))
        assert icon == IconId.STATUS_DOT
        assert text == "indexing"

    def test_job_done_includes_label(self):
        icon, flags, text = _classify(_ev("job_done", label="codex:tests"))
        assert icon == IconId.CHECK
        assert flags & FLAG_CLEAR_PREV_SPINNER
        assert text == "codex:tests"

    def test_job_needs_input_is_accented(self):
        icon, flags, text = _classify(
            _ev("job_needs_input", label="claude:review")
        )
        assert icon == IconId.QUESTION
        assert flags & FLAG_ACCENT
        assert flags & FLAG_CLEAR_PREV_SPINNER
        assert text == "claude:review"

    def test_unknown_event_uses_question(self):
        icon, _, text = _classify(_ev("unknown", provider_event="weird.event"))
        assert icon == IconId.QUESTION
        assert text == "weird.event"


# ============================================================================
# encode_event — frame-level invariants
# ============================================================================


class TestEncodeEvent:
    def test_frame_version_byte(self):
        frame = encode_event(_ev("tool_end"))
        assert frame[0] == PROTOCOL_VERSION

    def test_frame_under_max_len(self):
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
            "job_done",
            "job_needs_input",
            "job_failed",
            "unknown",
        ):
            frame = encode_event(
                _ev(ce, tool_name="Bash", label="x" * 100, path="/x" * 50)
            )
            assert len(frame) <= MAX_FRAME_LEN, (
                f"{ce}: frame {len(frame)} > {MAX_FRAME_LEN}"
            )

    def test_long_label_truncated_to_entry_text_max(self):
        frame = encode_event(
            _ev("tool_start", tool_name="Edit", path="/" + "a" * 100 + ".py")
        )
        text_len = frame[3]
        # Text portion length is stored in byte 3 and must respect the cap
        assert text_len <= MAX_FRAME_LEN - 4

    def test_unicode_label_not_split(self):
        frame = encode_event(
            _ev("tool_start", tool_name="Edit", path="/" + "α" * 30 + ".py")
        )
        text_len = frame[3]
        text = frame[4 : 4 + text_len]
        text.decode("utf-8")  # must not raise


# ============================================================================
# decode_frame — round-trip
# ============================================================================


class TestDecodeFrameRoundTrip:
    @pytest.mark.parametrize(
        "ev_kwargs",
        [
            {"canonical_event": "tool_start", "tool_name": "Bash", "label": "ls"},
            {"canonical_event": "tool_end"},
            {"canonical_event": "session_start"},
            {"canonical_event": "session_idle"},
            {"canonical_event": "session_stop"},
            {"canonical_event": "session_error"},
            {"canonical_event": "file_edit", "path": "/a/b.py"},
            {"canonical_event": "permission_request"},
            {"canonical_event": "permission_reply", "meta": {"approved": True}},
            {"canonical_event": "permission_reply", "meta": {"approved": False}},
            {"canonical_event": "status", "status_text": "running"},
            {"canonical_event": "unknown", "provider_event": "x.y.z"},
        ],
    )
    def test_roundtrip(self, ev_kwargs):
        ce = ev_kwargs.pop("canonical_event")
        ev = _ev(ce, **ev_kwargs)
        frame = encode_event(ev)
        decoded = decode_frame(frame)
        assert decoded is not None
        icon, flags, _ = _classify(ev)
        assert decoded["icon"] == int(icon)
        assert decoded["flags"] == int(flags)

    def test_decode_rejects_too_short(self):
        assert decode_frame(b"") is None
        assert decode_frame(b"\x01\x00\x00") is None

    def test_decode_rejects_unknown_version(self):
        assert decode_frame(b"\x02\x01\x00\x00") is None

    def test_decode_rejects_truncated_text(self):
        # ver=1, icon=1, flags=0, len=10, but only 3 text bytes follow
        assert decode_frame(b"\x01\x01\x00\x0aabc") is None
