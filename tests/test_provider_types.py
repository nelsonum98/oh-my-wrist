"""
test_provider_types.py — Tests for provider_types.py.

Covers:
- TOOL_INTENT mapping completeness and correctness
- get_tool_intent() for known, unknown, empty, and None inputs
- CanonicalEvent construction and defaults
- tool_intent property delegation
"""

from __future__ import annotations

import time

import pytest

from ohm.provider_types import (
    TOOL_INTENT,
    CanonicalEvent,
    get_tool_intent,
)


# ---------------------------------------------------------------------------
# TOOL_INTENT mapping
# ---------------------------------------------------------------------------


class TestToolIntentMapping:
    def test_bash_maps_to_shell(self):
        assert TOOL_INTENT["bash"] == "shell"

    def test_shell_maps_to_shell(self):
        assert TOOL_INTENT["shell"] == "shell"

    def test_edit_maps_to_edit(self):
        assert TOOL_INTENT["edit"] == "edit"

    def test_write_maps_to_edit(self):
        assert TOOL_INTENT["write"] == "edit"

    def test_multiedit_maps_to_edit(self):
        assert TOOL_INTENT["multiedit"] == "edit"

    def test_read_maps_to_read(self):
        assert TOOL_INTENT["read"] == "read"

    def test_webfetch_maps_to_web(self):
        assert TOOL_INTENT["webfetch"] == "web"

    def test_websearch_maps_to_web(self):
        assert TOOL_INTENT["websearch"] == "web"

    def test_todowrite_maps_to_todo(self):
        assert TOOL_INTENT["todowrite"] == "todo"

    def test_agent_maps_to_agent(self):
        assert TOOL_INTENT["agent"] == "agent"

    def test_subagent_maps_to_agent(self):
        assert TOOL_INTENT["subagent"] == "agent"

    def test_dispatch_maps_to_agent(self):
        assert TOOL_INTENT["dispatch"] == "agent"

    def test_permission_maps_to_permission(self):
        assert TOOL_INTENT["permission"] == "permission"

    def test_all_values_are_strings(self):
        for k, v in TOOL_INTENT.items():
            assert isinstance(v, str), f"TOOL_INTENT[{k!r}] is not a string"

    def test_all_keys_are_lowercase(self):
        for k in TOOL_INTENT:
            assert k == k.lower(), f"Key {k!r} is not lowercase"


# ---------------------------------------------------------------------------
# get_tool_intent()
# ---------------------------------------------------------------------------


class TestGetToolIntent:
    @pytest.mark.parametrize(
        "tool,expected",
        [
            ("Bash", "shell"),
            ("BASH", "shell"),
            ("bash", "shell"),
            ("Shell", "shell"),
            ("Edit", "edit"),
            ("Write", "edit"),
            ("MultiEdit", "edit"),
            ("Read", "read"),
            ("WebFetch", "web"),
            ("WebSearch", "web"),
            ("TodoWrite", "todo"),
            ("Agent", "agent"),
            ("SubAgent", "agent"),
            ("Task", "todo"),
            ("Dispatch", "agent"),
        ],
    )
    def test_known_tools(self, tool, expected):
        assert get_tool_intent(tool) == expected

    @pytest.mark.parametrize(
        "tool",
        [
            "UnknownTool",
            "FutureTool",
            "XYZ",
            "123",
        ],
    )
    def test_unknown_tools_return_unknown(self, tool):
        assert get_tool_intent(tool) == "unknown"

    def test_empty_string_returns_unknown(self):
        assert get_tool_intent("") == "unknown"

    def test_none_returns_unknown(self):
        assert get_tool_intent(None) == "unknown"  # type: ignore[arg-type]

    def test_whitespace_only_returns_unknown(self):
        assert get_tool_intent("   ") == "unknown"

    def test_case_insensitive_mixed(self):
        assert get_tool_intent("bAsH") == "shell"
        assert get_tool_intent("WRITE") == "edit"
        assert get_tool_intent("todowrite") == "todo"


# ---------------------------------------------------------------------------
# CanonicalEvent construction
# ---------------------------------------------------------------------------


class TestCanonicalEventConstruction:
    def test_minimal_construction(self):
        ev = CanonicalEvent(provider="claude", canonical_event="tool_start")
        assert ev.provider == "claude"
        assert ev.canonical_event == "tool_start"

    def test_defaults(self):
        ev = CanonicalEvent(provider="opencode", canonical_event="session_stop")
        assert ev.provider_event == ""
        assert ev.session_id is None
        assert ev.tool_name is None
        assert ev.label is None
        assert ev.path is None
        assert ev.status_text is None
        assert ev.active is True
        assert ev.meta == {}
        assert ev.ts > 0

    def test_ts_is_recent(self):
        before = time.time()
        ev = CanonicalEvent(provider="claude", canonical_event="tool_end")
        after = time.time()
        assert before <= ev.ts <= after

    def test_all_fields_set(self):
        ev = CanonicalEvent(
            provider="opencode",
            canonical_event="file_edit",
            provider_event="file.edited",
            session_id="sess-123",
            tool_name="Write",
            label="app.py",
            path="/src/app.py",
            status_text="editing app.py",
            active=True,
            ts=1234567890.0,
            meta={"extra": "data"},
        )
        assert ev.session_id == "sess-123"
        assert ev.path == "/src/app.py"
        assert ev.status_text == "editing app.py"
        assert ev.meta == {"extra": "data"}
        assert ev.ts == 1234567890.0

    def test_provider_opencode(self):
        ev = CanonicalEvent(provider="opencode", canonical_event="session_start")
        assert ev.provider == "opencode"

    def test_meta_is_independent_per_instance(self):
        ev1 = CanonicalEvent(provider="claude", canonical_event="tool_start")
        ev2 = CanonicalEvent(provider="claude", canonical_event="tool_start")
        ev1.meta["key"] = "val"
        assert "key" not in ev2.meta


# ---------------------------------------------------------------------------
# CanonicalEvent computed properties
# ---------------------------------------------------------------------------


class TestCanonicalEventProperties:
    @pytest.mark.parametrize(
        "tool,expected_intent",
        [
            ("Bash", "shell"),
            ("Edit", "edit"),
            ("Read", "read"),
            ("WebFetch", "web"),
            ("Agent", "agent"),
            (None, "unknown"),
            ("", "unknown"),
        ],
    )
    def test_tool_intent_property(self, tool, expected_intent):
        ev = CanonicalEvent(
            provider="claude",
            canonical_event="tool_start",
            tool_name=tool,
        )
        assert ev.tool_intent == expected_intent

    def test_active_false_for_session_stop(self):
        # active is set by the adapter, not auto-derived, but verify the field
        ev = CanonicalEvent(
            provider="claude",
            canonical_event="session_stop",
            active=False,
        )
        assert ev.active is False

    def test_active_true_for_tool_start(self):
        ev = CanonicalEvent(
            provider="claude",
            canonical_event="tool_start",
            active=True,
        )
        assert ev.active is True
