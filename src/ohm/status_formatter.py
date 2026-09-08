"""
Destructive-command detection for the haptic alert path.
"""

from __future__ import annotations

import re

__all__ = ["is_destructive_command"]

# ---------------------------------------------------------------------------
# Destructive command detection
# ---------------------------------------------------------------------------

DESTRUCTIVE_PATTERNS: list[str] = [
    r"\brm\b",  # rm, rm -rf
    r"\brmdir\b",
    r"\bDROP\b",  # SQL DROP TABLE/DATABASE
    r"\bTRUNCATE\b",  # SQL TRUNCATE
    r"--force\b",  # git push --force, etc.
    r"\bformat\b",
    r"\bmkfs\b",
    r"\bdd\b.*of=",  # dd writing to a device
    r"\bshred\b",
    r"\bchmod\s+777\b",
    r"\bkill\s+-9\b",
    r">\s*/dev/",  # writing to device files
]

_DESTRUCTIVE_RE = re.compile(
    "|".join(DESTRUCTIVE_PATTERNS),
    re.IGNORECASE,
)


def is_destructive_command(tool_name: str, tool_input: dict | None) -> bool:
    """Return True when a Bash/shell PreToolUse command matches a destructive pattern.

    Only applies to ``tool_name`` in the shell intent group.  All other tools
    return False.
    """
    from ohm.provider_types import get_tool_intent

    if get_tool_intent(tool_name) != "shell":
        return False
    command = tool_input.get("command", "") if tool_input else ""
    if not command:
        return False
    return bool(_DESTRUCTIVE_RE.search(command))
