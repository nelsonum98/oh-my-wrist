"""
session_state.py — Provider-agnostic session state and statistics engine.

This module replaces the earlier Claude-specific tracker with a unified engine
that accepts :class:`CanonicalEvent` objects from any provider.

It tracks:
- Whether a session is currently active.
- Which provider last sent an event.
- The last status text displayed on the watch.
- The last update timestamp.
- Per-provider event counts.
- Per-intent tool usage counts.
- Idle time (time between session_idle and the next tool_start/tool_end).
- Last session completion time.
- Edited file paths (deduplicated).
- Bash/shell command count.

The :meth:`to_ble_payload` method produces a compact, all-integer JSON
payload for the Garmin app's ``StatsModel.mc`` to parse without any string
fields (avoids substring/truncation hazards under MTU pressure).

Compact JSON keys (≤ MAX_STATS_LEN bytes total):
    d  — session duration in seconds (int)
    t  — total tool calls (int)
    e  — unique files edited (int)
    b  — bash/shell commands (int)
    i  — idle seconds accumulated (int)
    s  — seconds since last completion, or -1 if never (int)
    p  — last provider id: 0=none, 1=claude, 2=opencode (int)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ohm.protocol import MAX_STATS_LEN
from ohm.provider_types import CanonicalEvent


@dataclass
class SessionState:
    """Tracks live session state and statistics across all providers."""

    # Session lifecycle
    start_time: float = field(default_factory=time.time)
    is_active: bool = False
    last_provider: str = ""  # "claude" or "opencode"
    last_status_text: str = ""
    last_update_ts: float = 0.0

    # Per-provider event counts
    provider_counts: dict[str, int] = field(default_factory=dict)

    # Per-intent tool usage counts
    intent_counts: dict[str, int] = field(default_factory=dict)

    # Statistics
    tool_calls: int = 0
    edited_files: set[str] = field(default_factory=set)
    bash_count: int = 0
    idle_seconds: float = 0.0
    last_idle_start: float | None = None
    last_completion_time: float | None = None

    # ---------------------------------------------------------------------------
    # Event ingestion
    # ---------------------------------------------------------------------------

    def on_event(self, event: CanonicalEvent) -> None:
        """Update state from a canonical event.

        This is the single ingestion point for all providers.  It must not
        contain any provider-specific branching after normalization.
        """
        now = time.time()
        self.last_update_ts = now
        self.last_provider = event.provider
        self.tool_calls += 1

        # Per-provider count
        self.provider_counts[event.provider] = (
            self.provider_counts.get(event.provider, 0) + 1
        )

        # Per-intent count
        intent = event.tool_intent
        self.intent_counts[intent] = self.intent_counts.get(intent, 0) + 1

        ce = event.canonical_event

        # Session lifecycle transitions
        if ce == "session_start":
            self.is_active = True
            self.start_time = now
            # Reset per-session stats
            self.tool_calls = 1
            self.edited_files = set()
            self.bash_count = 0
            self.idle_seconds = 0.0
            self.last_idle_start = None
            self.last_completion_time = None
            self.provider_counts = {event.provider: 1}
            self.intent_counts = {}

        elif ce in ("session_stop", "session_error"):
            self.is_active = False
            self.last_completion_time = now
            self._stop_idle_timer(now)

        elif ce == "session_idle":
            # Start idle timer if not already running
            if self.last_idle_start is None:
                self.last_idle_start = now

        elif ce == "tool_start":
            self.is_active = True
            self._stop_idle_timer(now)

            # Track shell commands
            if event.tool_intent == "shell":
                self.bash_count += 1

            # Track edited files (use path first, fall back to label)
            if event.tool_intent == "edit":
                file_ref = event.path or event.label
                if file_ref:
                    self.edited_files.add(file_ref)

        elif ce == "tool_end":
            self._stop_idle_timer(now)

        elif ce == "file_edit":
            if event.path:
                self.edited_files.add(event.path)

        # Update last status text
        if event.status_text:
            self.last_status_text = event.status_text
        elif event.label:
            self.last_status_text = event.label

    # ---------------------------------------------------------------------------
    # Idle timer helpers
    # ---------------------------------------------------------------------------

    def _stop_idle_timer(self, now: float) -> None:
        """Accumulate elapsed idle time and clear the idle start marker."""
        if self.last_idle_start is not None:
            self.idle_seconds += now - self.last_idle_start
            self.last_idle_start = None

    def _current_idle(self) -> float:
        """Return total idle seconds including any open window."""
        if self.last_idle_start is not None:
            return self.idle_seconds + (time.time() - self.last_idle_start)
        return self.idle_seconds

    # ---------------------------------------------------------------------------
    # Reset
    # ---------------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all state to initial values (called on session_start)."""
        self.__init__()  # type: ignore[misc]

    # ---------------------------------------------------------------------------
    # BLE payload
    # ---------------------------------------------------------------------------

    def to_ble_payload(self) -> bytes:
        """Serialise current stats to compact JSON ≤ MAX_STATS_LEN bytes.

        All values are integers so the watch parser never has to handle
        quoted strings (avoids substring/truncation hazards under MTU pressure).

        Keys:
            d  duration seconds
            t  total tool calls
            e  unique files edited
            b  bash/shell commands
            i  idle seconds
            s  seconds since last completion (-1 = never)
            p  last provider id (0 = none, 1 = claude, 2 = opencode)
        """
        now = time.time()
        duration = int(now - self.start_time)
        idle = int(self._current_idle())

        if self.last_completion_time is None:
            completion_secs = -1
        else:
            completion_secs = int(now - self.last_completion_time)

        if self.last_provider == "claude":
            provider_id = 1
        elif self.last_provider == "opencode":
            provider_id = 2
        else:
            provider_id = 0

        data = {
            "d": duration,
            "t": self.tool_calls,
            "e": len(self.edited_files),
            "b": self.bash_count,
            "i": idle,
            "s": completion_secs,
            "p": provider_id,
        }

        payload = json.dumps(data, separators=(",", ":")).encode("utf-8")

        # Last-resort guard if counters somehow blow up.
        if len(payload) > MAX_STATS_LEN:
            data.pop("p", None)
            data.pop("s", None)
            payload = json.dumps(data, separators=(",", ":")).encode("utf-8")

        return payload


# ---------------------------------------------------------------------------
# Multi-provider session state
# ---------------------------------------------------------------------------


_KNOWN_PROVIDERS = ("claude", "opencode")


class MultiProviderSessionState:
    """Owns an isolated :class:`SessionState` per provider.

    Routes incoming canonical events to the matching provider's state so that
    Claude and OpenCode counters don't commingle, and a ``session_start`` from
    one provider doesn't wipe the other provider's accumulated stats.
    """

    def __init__(self) -> None:
        self._states: dict[str, SessionState] = {
            name: SessionState() for name in _KNOWN_PROVIDERS
        }

    def _get(self, provider: str) -> SessionState:
        state = self._states.get(provider)
        if state is None:
            state = SessionState()
            self._states[provider] = state
        return state

    def on_event(self, event: CanonicalEvent) -> None:
        """Route a canonical event to the matching provider's state.

        ``SessionState.on_event`` already handles the per-instance reset on
        ``session_start``, so isolation is achieved by holding one instance
        per provider — no cross-provider mutation happens here.
        """
        self._get(event.provider).on_event(event)

    def reset(self, provider: str) -> None:
        """Reset only the named provider's state."""
        self._get(provider).reset()

    def state_for(self, provider: str) -> SessionState:
        return self._get(provider)

    def payload_for(self, provider: str) -> bytes:
        return self._get(provider).to_ble_payload()

    def any_active(self) -> bool:
        return any(s.is_active for s in self._states.values())
