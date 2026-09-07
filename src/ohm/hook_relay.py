"""
hook_relay.py — Short-lived entry point called synchronously by Claude Code
on every hook event.  Must complete in under 100 ms.

Behaviour
---------
1. Read JSON from stdin (provided by Claude Code).
2. Parse and validate the event using Pydantic.
3. Adapt the HookEvent to a CanonicalEvent via the Claude adapter.
4. Determine the alert_type for haptic feedback.
5. Send a CanonicalIpcMessage to ble_daemon via the local socket (non-blocking).
6. Always exit 0 — never crash or block Claude Code.

The watch display string is no longer computed here — the daemon now encodes
each canonical event into a binary frame (see :mod:`history_encoder`).

Alert-type mapping
------------------
session_idle                                    → ALERT_IDLE_WAITING (0x01)
session_stop                                    → ALERT_SESSION_DONE (0x02)
tool_start + is_destructive_command()           → ALERT_DESTRUCTIVE  (0x03)
tool_end   + agent intent                       → ALERT_AGENT_DONE   (0x04)
All other events                                → ALERT_NONE         (0x00)
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

from loguru import logger

from ohm.adapters.claude_adapter import adapt_claude_hook
from ohm.protocol import (
    ALERT_AGENT_DONE,
    ALERT_DESTRUCTIVE,
    ALERT_IDLE_WAITING,
    ALERT_NONE,
    ALERT_SESSION_DONE,
    CanonicalIpcMessage,
    HookEvent,
    send_to_daemon,
)
from ohm.status_formatter import is_destructive_command

# Silence loguru to stderr by default so Claude Code does not see noise.
logger.remove()

_IPC_TIMEOUT_SECONDS = 0.25


def _read_stdin() -> dict:
    """Read and parse JSON from stdin; return empty dict on any failure."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _determine_alert_type(event: HookEvent, payload: dict) -> int:
    """Return the alert_type integer for a given hook event.

    Uses canonical event type and tool intent for provider-agnostic routing.
    """
    ce = event.event
    tool = (event.tool_name or "").strip()
    inp = event.tool_input or {}

    if ce == "Notification" and event.notification_type == "agent_completed":
        return ALERT_AGENT_DONE

    if ce == "Notification":
        return ALERT_IDLE_WAITING

    if ce in ("SessionEnd", "SubagentStop"):
        return ALERT_AGENT_DONE

    if ce == "Stop":
        return ALERT_SESSION_DONE

    if ce == "PreToolUse" and is_destructive_command(tool, inp):
        return ALERT_DESTRUCTIVE

    # Agent-done: PostToolUse with an agent-intent tool
    from ohm.provider_types import get_tool_intent

    if ce == "PostToolUse" and get_tool_intent(tool) == "agent":
        return ALERT_AGENT_DONE

    return ALERT_NONE


async def relay_payload(payload: dict, provider: str = "claude") -> None:
    """Parse, adapt, and relay a single hook event to the daemon."""
    hook_event = HookEvent.model_validate(payload)
    canonical = adapt_claude_hook(hook_event, raw_payload=payload)
    alert_type = _determine_alert_type(hook_event, payload)

    if canonical.canonical_event.startswith("job_"):
        base_label = canonical.label or "job"
        canonical.label = f"{provider}:{base_label}"

    msg = CanonicalIpcMessage(
        provider=provider,
        provider_event=hook_event.event,
        canonical_event=canonical.canonical_event,
        session_id=hook_event.session_id,
        tool_name=hook_event.tool_name,
        label=canonical.label,
        path=canonical.path,
        active=canonical.active,
        alert_type=alert_type,
        ts=time.time(),
        meta={"raw": payload},
    )
    # Lifecycle hooks sit on provider shutdown paths. Bound the local-only IPC
    # write so a stale socket can never delay Claude Code or Codex completion.
    await asyncio.wait_for(send_to_daemon(msg), timeout=_IPC_TIMEOUT_SECONDS)


def main() -> None:
    """Entry point: always exits 0 regardless of errors."""
    payload = _read_stdin()
    try:
        asyncio.run(relay_payload(payload))
    except Exception:
        # Daemon not running, socket unavailable, or any other error —
        # fail silently so Claude Code is never blocked.
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
