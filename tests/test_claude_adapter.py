"""
test_claude_adapter.py — Tests for adapters/claude_adapter.py.

Covers:
- All HookEvent → CanonicalEvent mappings
- Label and path extraction for each tool type
- Provider field is always "claude"
- meta["raw"] preservation
- active flag for terminal events
- Unknown event types
- Null/empty tool_input handling
"""

from __future__ import annotations

import pytest

from ohm.adapters.claude_adapter import adapt_claude_hook
from ohm.protocol import HookEvent


def _make_hook(
    event: str,
    tool_name: str | None = None,
    tool_input: dict | None = None,
    session_id: str | None = None,
    **kwargs,
) -> HookEvent:
    return HookEvent(
        event=event,
        tool_name=tool_name,
        tool_input=tool_input,
        session_id=session_id,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Provider field
# ---------------------------------------------------------------------------


class TestProvider:
    def test_provider_is_always_claude(self):
        for event in [
            "PreToolUse",
            "PostToolUse",
            "Notification",
            "Stop",
            "SessionStart",
            "Unknown",
        ]:
            hook = _make_hook(event)
            result = adapt_claude_hook(hook)
            assert result.provider == "claude"


# ---------------------------------------------------------------------------
# Canonical event mapping
# ---------------------------------------------------------------------------


class TestCanonicalEventMapping:
    @pytest.mark.parametrize(
        "hook_event,expected_canonical",
        [
            ("PreToolUse", "tool_start"),
            ("PostToolUse", "tool_end"),
            ("Notification", "session_idle"),
            ("Stop", "session_stop"),
            ("SessionStart", "session_start"),
            ("SessionEnd", "job_done"),
            ("SubagentStop", "job_done"),
            ("Unknown", "unknown"),
            ("FutureEvent", "unknown"),
            ("", "unknown"),
        ],
    )
    def test_mapping(self, hook_event, expected_canonical):
        hook = _make_hook(hook_event)
        result = adapt_claude_hook(hook)
        assert result.canonical_event == expected_canonical

    def test_provider_event_preserved(self):
        hook = _make_hook("PreToolUse", "Bash", {"command": "ls"})
        result = adapt_claude_hook(hook)
        assert result.provider_event == "PreToolUse"

    def test_completed_agent_notification_is_job_done(self):
        hook = _make_hook(
            "Notification",
            notification_type="agent_completed",
            agent_type="worker",
        )
        result = adapt_claude_hook(hook)
        assert result.canonical_event == "job_done"
        assert result.label == "worker"
        assert result.active is False

    def test_agent_input_notification_is_distinct(self):
        hook = _make_hook(
            "Notification",
            notification_type="agent_needs_input",
            message="Choose a device",
        )
        result = adapt_claude_hook(hook)
        assert result.canonical_event == "job_needs_input"
        assert result.label == "Choose a device"


# ---------------------------------------------------------------------------
# Label and path extraction — PreToolUse
# ---------------------------------------------------------------------------


class TestLabelExtraction:
    def test_bash_label_is_command(self):
        hook = _make_hook("PreToolUse", "Bash", {"command": "git status"})
        result = adapt_claude_hook(hook)
        assert result.label == "git status"

    def test_bash_empty_command(self):
        hook = _make_hook("PreToolUse", "Bash", {"command": ""})
        result = adapt_claude_hook(hook)
        assert result.label == ""

    def test_bash_no_command_key(self):
        hook = _make_hook("PreToolUse", "Bash", {})
        result = adapt_claude_hook(hook)
        assert result.label == ""

    def test_edit_label_is_path(self):
        hook = _make_hook("PreToolUse", "Edit", {"path": "/src/main.py"})
        result = adapt_claude_hook(hook)
        assert result.label == "/src/main.py"
        assert result.path == "/src/main.py"

    def test_write_label_is_path(self):
        hook = _make_hook("PreToolUse", "Write", {"path": "/tmp/out.txt"})
        result = adapt_claude_hook(hook)
        assert result.path == "/tmp/out.txt"

    def test_multiedit_uses_path(self):
        hook = _make_hook("PreToolUse", "MultiEdit", {"path": "app.js"})
        result = adapt_claude_hook(hook)
        assert result.path == "app.js"

    def test_edit_falls_back_to_file_path(self):
        hook = _make_hook("PreToolUse", "Edit", {"file_path": "/alt/path.py"})
        result = adapt_claude_hook(hook)
        assert result.path == "/alt/path.py"

    def test_read_label_is_path(self):
        hook = _make_hook("PreToolUse", "Read", {"path": "/etc/hosts"})
        result = adapt_claude_hook(hook)
        assert result.label == "/etc/hosts"
        assert result.path == "/etc/hosts"

    def test_webfetch_label_is_url(self):
        hook = _make_hook("PreToolUse", "WebFetch", {"url": "https://example.com"})
        result = adapt_claude_hook(hook)
        assert result.label == "https://example.com"

    def test_websearch_label_is_query(self):
        hook = _make_hook("PreToolUse", "WebSearch", {"query": "python async"})
        result = adapt_claude_hook(hook)
        assert result.label == "python async"

    def test_todowrite_label_is_fixed(self):
        hook = _make_hook("PreToolUse", "TodoWrite", {"todos": []})
        result = adapt_claude_hook(hook)
        assert result.label == "todo update"

    def test_unknown_tool_label_is_tool_name(self):
        hook = _make_hook("PreToolUse", "MyCustomTool", {"arg": "val"})
        result = adapt_claude_hook(hook)
        assert result.label == "MyCustomTool"

    def test_none_tool_input(self):
        hook = _make_hook("PreToolUse", "Bash", None)
        result = adapt_claude_hook(hook)
        assert result.label == ""

    def test_posttooluse_no_label(self):
        hook = _make_hook("PostToolUse", "Bash", {"command": "ls"})
        result = adapt_claude_hook(hook)
        assert result.label is None

    def test_notification_no_label(self):
        hook = _make_hook("Notification")
        result = adapt_claude_hook(hook)
        assert result.label is None

    def test_stop_no_label(self):
        hook = _make_hook("Stop")
        result = adapt_claude_hook(hook)
        assert result.label is None


# ---------------------------------------------------------------------------
# active flag
# ---------------------------------------------------------------------------


class TestActiveFlag:
    @pytest.mark.parametrize(
        "event,expected_active",
        [
            ("PreToolUse", True),
            ("PostToolUse", True),
            ("Notification", True),
            ("SessionStart", True),
            ("Stop", False),
        ],
    )
    def test_active_flag(self, event, expected_active):
        hook = _make_hook(event)
        result = adapt_claude_hook(hook)
        assert result.active == expected_active


# ---------------------------------------------------------------------------
# session_id passthrough
# ---------------------------------------------------------------------------


class TestSessionId:
    def test_session_id_preserved(self):
        hook = _make_hook("PreToolUse", session_id="abc-123")
        result = adapt_claude_hook(hook)
        assert result.session_id == "abc-123"

    def test_session_id_none(self):
        hook = _make_hook("PreToolUse")
        result = adapt_claude_hook(hook)
        assert result.session_id is None


# ---------------------------------------------------------------------------
# meta["raw"] preservation
# ---------------------------------------------------------------------------


class TestMetaRaw:
    def test_raw_payload_stored_in_meta(self):
        payload = {
            "event": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        hook = HookEvent.model_validate(payload)
        result = adapt_claude_hook(hook, raw_payload=payload)
        assert result.meta.get("raw") == payload

    def test_no_raw_payload(self):
        hook = _make_hook("PreToolUse", "Bash", {"command": "ls"})
        result = adapt_claude_hook(hook)
        assert "raw" not in result.meta

    def test_empty_raw_payload(self):
        hook = _make_hook("PreToolUse")
        result = adapt_claude_hook(hook, raw_payload={})
        assert result.meta.get("raw") == {}


# ---------------------------------------------------------------------------
# tool_name passthrough
# ---------------------------------------------------------------------------


class TestToolName:
    def test_tool_name_preserved(self):
        hook = _make_hook("PreToolUse", "Bash")
        result = adapt_claude_hook(hook)
        assert result.tool_name == "Bash"

    def test_tool_name_none(self):
        hook = _make_hook("PostToolUse")
        result = adapt_claude_hook(hook)
        assert result.tool_name is None

    def test_tool_name_whitespace(self):
        hook = _make_hook("PreToolUse", "  Edit  ")
        result = adapt_claude_hook(hook)
        # tool_name is passed through as-is from HookEvent
        assert result.tool_name == "  Edit  "
