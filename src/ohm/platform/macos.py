"""
platform/macos.py — macOS-specific service management via launchd.

The daemon is registered as a LaunchAgent so it starts automatically on
user login and can be controlled with launchctl.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PLIST_LABEL = "com.nelson.oh-my-wrist"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCH_AGENTS_DIR / f"{PLIST_LABEL}.plist"

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{executable}</string>
        <string>start</string>
        <string>--foreground</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <false/>

    <key>StandardOutPath</key>
    <string>{log_dir}/daemon.stdout.log</string>

    <key>StandardErrorPath</key>
    <string>{log_dir}/daemon.stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{user_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
"""


def install_service() -> None:
    """Install and load the launchd LaunchAgent plist."""
    executable = shutil.which("oh-my-wrist") or sys.executable
    log_dir = Path.home() / ".oh-my-wrist"
    log_dir.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    plist_content = _PLIST_TEMPLATE.format(
        label=PLIST_LABEL,
        executable=executable,
        log_dir=log_dir,
        user_bin=Path.home() / ".local" / "bin",
    )
    PLIST_PATH.write_text(plist_content)

    # Unload first in case it was already loaded (ignore errors)
    subprocess.run(
        ["launchctl", "unload", str(PLIST_PATH)],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "load", "-w", str(PLIST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"launchctl load failed: {result.stderr.strip()}")


def uninstall_service() -> None:
    """Unload and remove the launchd LaunchAgent plist."""
    if PLIST_PATH.exists():
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            capture_output=True,
        )
        PLIST_PATH.unlink(missing_ok=True)


def service_status() -> str:
    """Return a human-readable status string for the launchd service."""
    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return f"launchd service loaded:\n{result.stdout.strip()}"
    return "launchd service not loaded"
