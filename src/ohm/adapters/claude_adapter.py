"""
claude_adapter.py — Converts Claude Code hook events into CanonicalEvent.

Claude Code fires hook events as JSON on stdin.  The hook_relay module reads
that JSON, validates it as a HookEvent, then calls ``adapt_claude_hook()`` to
produce the provider-agnostic CanonicalEvent consumed by the rest of the
pipeline.

Mapping table
-------------
PreToolUse / Bash            → tool_start  (tool_intent=shell)
PreToolUse / Edit|Write|…    → tool_start  (tool_intent=edit)
PreToolUse / Read            → tool_start  (tool_intent=read)
PreToolUse / WebFetch|…      → tool_start  (tool_intent=web)
PreToolUse / TodoWrite       → tool_start  (tool_intent=todo)
PreToolUse / Agent|…         → tool_start  (tool_intent=agent)
PreToolUse / other           → tool_start  (tool_intent=unknown)
PostToolUse                  → tool_end
Notification                 → session_idle
Stop                         → session_stop
SessionStart                 → session_start
Unknown / other              → unknown
"""

from __future__ import annotations

import os
import time

from ohm.protocol import HookEvent
from ohm.provider_types import CanonicalEvent, CanonicalEventType

# ---------------------------------------------------------------------------
# Event mapping
# ---------------------------------------------------------------------------

_CLAUDE_EVENT_MAP: dict[str, CanonicalEventType] = {
    "PreToolUse": "tool_start",
    "PostToolUse": "tool_end",
    "Notification": "session_idle",
    "Stop": "session_stop",
    "SessionStart": "session_start",
    "SessionEnd": "job_done",
    "SubagentStop": "job_done",
}


def adapt_claude_hook(
    event: HookEvent, raw_payload: dict | None = None
) -> CanonicalEvent:
    """Convert a validated :class:`HookEvent` into a :class:`CanonicalEvent`.

    Parameters
    ----------
    event:
        Validated Claude Code hook event.
    raw_payload:
        The original stdin dict, preserved in ``meta["raw"]`` for debugging.
    """
    canonical: CanonicalEventType = _CLAUDE_EVENT_MAP.get(event.event, "unknown")
    inp = event.tool_input or {}

    # Derive a short human-readable label
    label: str | None = None
    path: str | None = None

    if event.event == "Notification" and event.notification_type == "agent_completed":
        canonical = "job_done"
    elif event.event == "Notification" and event.notification_type == "agent_needs_input":
        canonical = "job_needs_input"

    if event.event == "PreToolUse":
        tool = (event.tool_name or "").strip()
        if tool == "Bash":
            label = inp.get("command", "")
        elif tool in ("Edit", "Write", "MultiEdit"):
            path = inp.get("path", inp.get("file_path", ""))
            label = path
        elif tool == "Read":
            path = inp.get("path", inp.get("file_path", ""))
            label = path
        elif tool in ("WebFetch", "WebSearch"):
            label = inp.get("url", inp.get("query", ""))
        elif tool == "TodoWrite":
            label = "todo update"
        else:
            label = event.tool_name
    elif canonical.startswith("job_"):
        label = (
            event.agent_type
            or event.message
            or (os.path.basename(event.cwd) if event.cwd else None)
            or "job"
        )

    meta: dict = {}
    if raw_payload is not None:
        meta["raw"] = raw_payload

    return CanonicalEvent(
        provider="claude",
        provider_event=event.event,
        canonical_event=canonical,
        session_id=event.session_id,
        tool_name=event.tool_name,
        label=label,
        path=path,
        active=(
            canonical
            not in ("session_stop", "session_error", "job_done", "job_failed")
        ),
        ts=time.time(),
        meta=meta,
    )
