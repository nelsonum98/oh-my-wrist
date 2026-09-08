"""
main.py — Click CLI entry point for oh-my-wrist.

Commands
--------
  start       Start the BLE daemon in the background
  stop        Stop the daemon
  status      Show daemon status, BLE advertising state, connected devices
  install     Auto-configure Claude Code hooks + register system service
  uninstall   Remove hooks and service registration
  hook        Entry point called by Claude Code hook events (stdin JSON)
  test        Send a test status string to simulate Claude Code activity
  logs        Tail the daemon log
  config      View or update haptic / quiet-hours configuration
  set-id      Set the BLE connection ID used for daemon/watch matching
  opencode    OpenCode plugin management subcommands
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import click

from ohm.protocol import send_to_daemon

_CONFIG_DIR = Path.home() / ".oh-my-wrist"
_LOG_PATH = _CONFIG_DIR / "daemon.log"
_PID_PATH = _CONFIG_DIR / "daemon.pid"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_pid() -> int | None:
    """Return the daemon PID from the pid file, or None if not running."""
    try:
        return int(_PID_PATH.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_running(pid: int) -> bool:
    """Return True if a process with *pid* is alive."""
    if sys.platform == "win32":
        # os.kill(pid, 0) raises WinError 87 on Windows because signal 0
        # is not a valid Windows signal.  Use OpenProcess instead.
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(package_name="oh-my-wrist")
def cli() -> None:
    """oh-my-wrist — Display AI coding assistant activity on your Garmin watch.

    Supports Claude Code, Codex, and OpenCode as providers.
    """


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--foreground",
    "-f",
    is_flag=True,
    default=False,
    help="Run in the foreground (do not daemonise).",
)
def start(foreground: bool) -> None:
    """Start the BLE daemon."""
    pid = _read_pid()
    if pid and _is_running(pid):
        click.echo(f"Daemon is already running (PID {pid}).")
        return

    if foreground:
        click.echo("Starting BLE daemon in the foreground…")
        from ohm.ble_daemon import run_daemon

        run_daemon()
    else:
        cmd = [sys.executable, "-m", "ohm.ble_daemon"]
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kwargs,
        )
        time.sleep(0.8)
        click.echo(f"BLE daemon started (PID {proc.pid}).")


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


@cli.command()
def stop() -> None:
    """Stop the BLE daemon."""
    pid = _read_pid()
    if pid is None or not _is_running(pid):
        click.echo("Daemon is not running.")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        click.echo(f"Sent SIGTERM to daemon (PID {pid}).")
    except ProcessLookupError:
        click.echo("Daemon process not found.")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
def status() -> None:
    """Show daemon status and BLE advertising state."""
    pid = _read_pid()
    if pid and _is_running(pid):
        click.echo(f"Daemon status : RUNNING (PID {pid})")
    else:
        click.echo("Daemon status : STOPPED")

    from ohm.config import load_config

    cfg = load_config()
    click.echo(f"Connection ID : {cfg.connection_id}")

    from ohm.install import get_service_status

    click.echo("\nSystem service status:")
    click.echo(get_service_status())

    # Claude Code hooks
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            hooks = data.get("hooks", {})
            hook_events = list(hooks.keys())
            click.echo(f"\nClaude Code hooks registered: {hook_events or 'none'}")
            sl = data.get("statusLine")
            sl_cmd = sl.get("command") if isinstance(sl, dict) else None
            from ohm.install import _STATUSLINE_COMMAND

            if sl_cmd == _STATUSLINE_COMMAND:
                click.echo("Claude Code statusLine : oh-my-wrist (usage bars)")
            elif sl_cmd:
                click.echo(f"Claude Code statusLine : other ({sl_cmd})")
            else:
                click.echo("Claude Code statusLine : not configured")
        except Exception:
            click.echo("\nCould not read Claude Code settings.json")
    else:
        click.echo(
            "\nClaude Code settings.json not found — run `oh-my-wrist install` first"
        )

    # OpenCode plugin
    from ohm.install import (
        _OPENCODE_GLOBAL_PLUGINS_DIR,
        OPENCODE_PLUGIN_FILENAME,
        find_opencode_project_root,
    )

    global_plugin = _OPENCODE_GLOBAL_PLUGINS_DIR / OPENCODE_PLUGIN_FILENAME
    installed = global_plugin.exists()
    click.echo(
        f"\nOpenCode plugin  : {'installed (' + str(global_plugin) + ')' if installed else 'NOT installed'}"
    )
    root = find_opencode_project_root()
    if root:
        click.echo(f"OpenCode project : {root}")


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--provider",
    type=click.Choice(
        ["claude", "codex", "opencode", "agents", "both", "all"],
        case_sensitive=False,
    ),
    default="all",
    show_default=True,
    help="Which provider(s) to configure.",
)
@click.option(
    "--project-root",
    "project_root",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="OpenCode project root (auto-detected if omitted).",
)
def install(provider: str, project_root: Path | None) -> None:
    """Configure AI provider hooks and register the system service."""
    from ohm.install import (
        install_service,
        patch_claude_settings,
        patch_claude_statusline,
        patch_codex_hooks,
        install_opencode_plugin,
    )

    step = 1
    total = (
        (1 if provider in ("claude", "agents", "both", "all") else 0)
        + (1 if provider in ("codex", "agents", "all") else 0)
        + (1 if provider in ("opencode", "both", "all") else 0)
        + 1
    )

    if provider in ("claude", "agents", "both", "all"):
        click.echo(f"Step {step}/{total} — Patching Claude Code settings.json…")
        try:
            patch_claude_settings()
            patch_claude_statusline()
            click.echo("  ✔ Claude Code hooks + statusLine configured.")
        except Exception as exc:
            click.echo(f"  ✗ Failed to patch settings: {exc}", err=True)
        step += 1

    if provider in ("codex", "agents", "all"):
        click.echo(f"Step {step}/{total} — Patching Codex hooks.json…")
        try:
            patch_codex_hooks()
            click.echo("  ✔ Codex lifecycle hooks configured; notify preserved.")
        except Exception as exc:
            click.echo(f"  ✗ Failed to patch Codex hooks: {exc}", err=True)
        step += 1

    if provider in ("opencode", "both", "all"):
        click.echo(f"Step {step}/{total} — Installing OpenCode plugin…")
        try:
            ok = install_opencode_plugin(project_root)
            if ok:
                click.echo("  ✔ OpenCode plugin installed.")
            else:
                click.echo("  ℹ No OpenCode project detected — skipped.")
        except Exception as exc:
            click.echo(f"  ✗ Failed to install OpenCode plugin: {exc}", err=True)
        step += 1

    click.echo(f"Step {step}/{total} — Registering system service…")
    try:
        install_service()
        click.echo("  ✔ Service registered.")
    except Exception as exc:
        click.echo(f"  ✗ Failed to register service: {exc}", err=True)

    click.echo("\nInstallation complete. Run `oh-my-wrist start` to begin.")


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--provider",
    type=click.Choice(
        ["claude", "codex", "opencode", "agents", "both", "all"],
        case_sensitive=False,
    ),
    default="all",
    show_default=True,
    help="Which provider(s) to remove.",
)
@click.option(
    "--project-root",
    "project_root",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="OpenCode project root (auto-detected if omitted).",
)
def uninstall(provider: str, project_root: Path | None) -> None:
    """Remove AI provider hooks and the system service registration."""
    from ohm.install import (
        remove_claude_hooks,
        remove_claude_statusline,
        remove_codex_hooks,
        remove_opencode_plugin,
        uninstall_service,
    )

    if provider in ("claude", "agents", "both", "all"):
        click.echo("Removing Claude Code hooks…")
        try:
            remove_claude_hooks()
            remove_claude_statusline()
            click.echo("  ✔ Hooks + statusLine removed.")
        except Exception as exc:
            click.echo(f"  ✗ {exc}", err=True)

    if provider in ("codex", "agents", "all"):
        click.echo("Removing Codex hooks…")
        try:
            remove_codex_hooks()
            click.echo("  ✔ Codex hooks removed; notify preserved.")
        except Exception as exc:
            click.echo(f"  ✗ {exc}", err=True)

    if provider in ("opencode", "both", "all"):
        click.echo("Removing OpenCode plugin…")
        try:
            remove_opencode_plugin(project_root)
            click.echo("  ✔ OpenCode plugin removed.")
        except Exception as exc:
            click.echo(f"  ✗ {exc}", err=True)

    click.echo("Removing system service…")
    try:
        uninstall_service()
        click.echo("  ✔ Service removed.")
    except Exception as exc:
        click.echo(f"  ✗ {exc}", err=True)

    click.echo("\nUninstall complete.")


# ---------------------------------------------------------------------------
# hook  (called by Claude Code on every hook event)
# ---------------------------------------------------------------------------


@cli.command(name="hook")
def hook_cmd() -> None:
    """Entry point invoked by Claude Code hook events (reads JSON from stdin)."""
    from ohm.hook_relay import main as relay_main

    relay_main()


@cli.command(name="codex-hook", hidden=True)
def codex_hook_cmd() -> None:
    """Entry point invoked by Codex lifecycle hooks (reads JSON from stdin)."""
    from ohm.codex_hook_relay import main as relay_main

    relay_main()


# ---------------------------------------------------------------------------
# statusline  (called by Claude Code statusLine command)
# ---------------------------------------------------------------------------


@cli.command(name="statusline")
def statusline_cmd() -> None:
    """Entry point for the Claude Code statusLine (reads JSON from stdin)."""
    from ohm.statusline_relay import main as relay_main

    relay_main()


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("message", default="✏️ test.py")
@click.option(
    "--provider",
    type=click.Choice(["claude", "codex", "opencode"], case_sensitive=False),
    default="claude",
    show_default=True,
    help="Simulate a message from this provider.",
)
def test(message: str, provider: str) -> None:
    """Send a test status string to the running daemon."""
    from ohm.protocol import CanonicalIpcMessage

    msg = CanonicalIpcMessage(
        provider=provider,
        provider_event=(
            "PreToolUse" if provider == "claude" else "tool.execute.before"
        ),
        canonical_event="tool_start",
        label=message,
        active=True,
        ts=time.time(),
        meta={"status": message},
    )

    try:
        asyncio.run(send_to_daemon(msg))
        click.echo(f"Sent test status [{provider}]: '{message}'")
    except Exception as exc:
        click.echo(
            f"Failed to send test message (is the daemon running?): {exc}", err=True
        )


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@cli.command(name="config")
@click.option(
    "--haptic",
    type=click.Choice(["on", "off"]),
    default=None,
    help="Enable or disable haptic alerts.",
)
@click.option(
    "--quiet-start",
    "quiet_start",
    default=None,
    metavar="HH:MM",
    help="Start of quiet hours (haptic suppressed).",
)
@click.option(
    "--quiet-end",
    "quiet_end",
    default=None,
    metavar="HH:MM",
    help="End of quiet hours.",
)
@click.option(
    "--show", is_flag=True, default=False, help="Print the current configuration."
)
def config_cmd(
    haptic: str | None,
    quiet_start: str | None,
    quiet_end: str | None,
    show: bool,
) -> None:
    """View or update oh-my-wrist configuration."""
    from ohm.config import (
        load_config,
        set_haptic,
        set_quiet_end,
        set_quiet_start,
    )

    changed = False

    if haptic is not None:
        cfg = set_haptic(haptic == "on")
        click.echo(f"Haptic alerts: {'enabled' if cfg.haptic_enabled else 'disabled'}")
        changed = True

    if quiet_start is not None:
        cfg = set_quiet_start(quiet_start)
        click.echo(f"Quiet start: {cfg.quiet_start}")
        changed = True

    if quiet_end is not None:
        cfg = set_quiet_end(quiet_end)
        click.echo(f"Quiet end: {cfg.quiet_end}")
        changed = True

    if show or not changed:
        cfg = load_config()
        click.echo(f"haptic_enabled : {cfg.haptic_enabled}")
        click.echo(f"quiet_start    : {cfg.quiet_start}")
        click.echo(f"quiet_end      : {cfg.quiet_end}")
        click.echo(f"connection_id  : {cfg.connection_id}")


# ---------------------------------------------------------------------------
# set-id
# ---------------------------------------------------------------------------


@cli.command(name="set-id")
@click.argument("connection_id", type=click.IntRange(0, 255))
def set_id(connection_id: int) -> None:
    """Set the BLE connection ID used to match daemon and watch."""
    from ohm.config import get_control_token, set_connection_id
    from ohm.protocol import CanonicalIpcMessage

    cfg = set_connection_id(connection_id)
    control_token = get_control_token()
    click.echo(f"Connection ID: {cfg.connection_id}")

    pid = _read_pid()
    if pid is None or not _is_running(pid):
        click.echo("Daemon is not running; ID will apply on next daemon start.")
        return

    msg = CanonicalIpcMessage(
        provider="claude",
        provider_event="oh-my-wrist.config",
        canonical_event="config_update",
        active=False,
        meta={"connection_id": cfg.connection_id, "control_token": control_token},
    )

    try:
        asyncio.run(send_to_daemon(msg))
        click.echo("Running daemon update queued.")
    except Exception as exc:
        click.echo(
            "Saved, but failed to update running daemon "
            f"({exc}); ID will apply on next daemon start.",
            err=True,
        )


# ---------------------------------------------------------------------------
# opencode subcommand group
# ---------------------------------------------------------------------------


@cli.group(name="opencode")
def opencode_group() -> None:
    """Manage the OpenCode plugin integration."""


@opencode_group.command(name="install")
@click.option(
    "--project-root",
    "project_root",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="OpenCode project root (auto-detected if omitted).",
)
def opencode_install(project_root: Path | None) -> None:
    """Install the OpenCode plugin into the current project."""
    from ohm.install import install_opencode_plugin

    ok = install_opencode_plugin(project_root)
    if ok:
        click.echo("OpenCode plugin installed successfully.")
    else:
        click.echo(
            "No OpenCode project detected in the current directory tree.\n"
            "Create a .opencode/ directory first, or pass --project-root.",
            err=True,
        )
        raise SystemExit(1)


@opencode_group.command(name="uninstall")
@click.option(
    "--project-root",
    "project_root",
    default=None,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="OpenCode project root (auto-detected if omitted).",
)
def opencode_uninstall(project_root: Path | None) -> None:
    """Remove the OpenCode plugin from the current project."""
    from ohm.install import remove_opencode_plugin

    ok = remove_opencode_plugin(project_root)
    if ok:
        click.echo("OpenCode plugin removed.")
    else:
        click.echo("No OpenCode project found.", err=True)


@opencode_group.command(name="status")
def opencode_status() -> None:
    """Show OpenCode plugin installation status."""
    from ohm.install import find_opencode_project_root, OPENCODE_PLUGIN_FILENAME

    root = find_opencode_project_root()
    if root is None:
        click.echo("No OpenCode project detected in the current directory tree.")
        return
    plugin_path = root / ".opencode" / "plugins" / OPENCODE_PLUGIN_FILENAME
    click.echo(f"Project root : {root}")
    click.echo(f"Plugin path  : {plugin_path}")
    click.echo(f"Installed    : {'yes' if plugin_path.exists() else 'NO'}")


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--lines", "-n", default=50, help="Number of lines to show initially.")
def logs(lines: int) -> None:
    """Tail the daemon log file."""
    if not _LOG_PATH.exists():
        click.echo(f"Log file not found: {_LOG_PATH}")
        return

    if sys.platform == "win32":
        subprocess.run(
            [
                "powershell",
                "-Command",
                f"Get-Content -Path '{_LOG_PATH}' -Tail {lines} -Wait",
            ],
        )
    else:
        subprocess.run(["tail", f"-n{lines}", "-f", str(_LOG_PATH)])


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
