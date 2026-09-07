"""Codex hook adapter.

Codex and Claude Code use the same JSON hook envelope for lifecycle events.
The shared relay keeps their event semantics aligned while tagging Codex as a
separate provider on the local IPC wire.
"""

from __future__ import annotations

import asyncio
import sys

from ohm.hook_relay import _read_stdin, relay_payload


def main() -> None:
    payload = _read_stdin()
    try:
        asyncio.run(relay_payload(payload, provider="codex"))
    except Exception:
        pass
    # Codex command hooks parse stdout as a JSON response. An empty response
    # means no hook-specific control decision while still satisfying the hook
    # protocol on success.
    print("{}")
    sys.exit(0)


if __name__ == "__main__":
    main()
