"""
provider_types.py — Provider-agnostic canonical event model.

This module defines the single internal event type that both the Claude Code
adapter and the OpenCode adapter produce.  All downstream components (status
formatter, session state engine, BLE daemon) consume only CanonicalEvent and
must not depend on provider-specific schemas.

Canonical event types
---------------------
tool_start         — A tool is about to execute.
tool_end           — A tool has finished executing.
session_start      — A new coding session has begun.
session_idle       — The session is waiting for user input or approval.
session_stop       — The session has ended normally.
session_error      — The session encountered an error.
file_edit          — A file was edited.
todo_update        — The todo list was updated.
permission_request — The assistant is asking for user permission.
permission_reply   — The user replied to a permission request.
command            — A shell command was executed.
status             — A generic status update from the provider.
unknown            — Unrecognised upstream event (preserved for debugging).
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Provider identifiers
# ---------------------------------------------------------------------------

Provider = Literal["claude", "codex", "opencode"]

# ---------------------------------------------------------------------------
# Canonical event type literals
# ---------------------------------------------------------------------------

CanonicalEventType = Literal[
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
]

# ---------------------------------------------------------------------------
# Tool intent groups
# ---------------------------------------------------------------------------

# Maps tool names (case-insensitive) to a normalised intent label.
# Both Claude Code and OpenCode tool names are included so the formatter
# can use a single intent-based switch rather than per-provider branches.

TOOL_INTENT: dict[str, str] = {
    # shell / bash / command / run
    "bash": "shell",
    "shell": "shell",
    "run": "shell",
    "command": "shell",
    "exec": "shell",
    "execute": "shell",
    "terminal": "shell",
    # edit / write / patch / apply
    "edit": "edit",
    "write": "edit",
    "multiedit": "edit",
    "patch": "edit",
    "apply": "edit",
    "create": "edit",
    "overwrite": "edit",
    # read / open / view
    "read": "read",
    "open": "read",
    "view": "read",
    "cat": "read",
    "show": "read",
    # web / search / fetch
    "webfetch": "web",
    "websearch": "web",
    "fetch": "web",
    "search": "web",
    "browse": "web",
    "curl": "web",
    # todo / task / plan
    "todowrite": "todo",
    "todo": "todo",
    "task": "todo",
    "plan": "todo",
    "checklist": "todo",
    # permission / approval
    "permission": "permission",
    "approval": "permission",
    "confirm": "permission",
    # agent / sub-agent
    "agent": "agent",
    "subagent": "agent",
    "dispatch": "agent",
}


def get_tool_intent(tool_name: str) -> str:
    """Return the normalised intent group for a tool name.

    Falls back to ``"unknown"`` for unrecognised tools.
    """
    return TOOL_INTENT.get((tool_name or "").lower().strip(), "unknown")


# ---------------------------------------------------------------------------
# Canonical event model
# ---------------------------------------------------------------------------


class CanonicalEvent(BaseModel):
    """Provider-agnostic internal event consumed by all downstream components.

    Both the Claude Code adapter and the OpenCode adapter must produce
    instances of this model.  No downstream component should inspect
    ``provider_event`` for routing decisions; use ``canonical_event`` instead.
    """

    # Mandatory fields
    provider: Provider
    canonical_event: CanonicalEventType

    # Provenance / debugging
    provider_event: str = Field(default="")
    session_id: str | None = Field(default=None)

    # Content
    tool_name: str | None = Field(default=None)
    label: str | None = Field(default=None)
    path: str | None = Field(default=None)
    status_text: str | None = Field(default=None)
    active: bool = Field(default=True)

    # Timestamp (Unix epoch, fractional seconds)
    ts: float = Field(default_factory=time.time)

    # Non-essential structured details (original payload, permission decision, etc.)
    meta: dict[str, Any] = Field(default_factory=dict)

    # ---------------------------------------------------------------------------
    # Convenience helpers
    # ---------------------------------------------------------------------------

    @property
    def tool_intent(self) -> str:
        """Return the normalised tool intent group for this event."""
        return get_tool_intent(self.tool_name or "")

    model_config = {"populate_by_name": True}
