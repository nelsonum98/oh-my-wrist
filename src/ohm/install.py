"""
install.py — Auto-installer for oh-my-wrist.

Step A: Patch Claude Code's global ~/.claude/settings.json with the
        required hook configuration (atomic write to prevent corruption).
Step B: Install the OpenCode plugin into the project's .opencode/plugins/
        directory (if an OpenCode project is detected).
Step C: Register the BLE daemon as a system service appropriate for the
        current platform (launchd / systemd / Task Scheduler).

Provider detection
------------------
- Claude Code is always installed (global ~/.claude/settings.json).
- OpenCode is installed when the current working directory (or any ancestor
  up to the filesystem root) contains a `.opencode/` directory.
- For compatibility with older repository layouts, we also treat a project as
  OpenCode-enabled when it contains `opencode/opencode.json` or
  `opencode/plugins/` and bootstrap `.opencode/` during installation.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from loguru import logger

# ---------------------------------------------------------------------------
# Claude Code hook configuration
# ---------------------------------------------------------------------------


def _ohm_command(subcommand: str) -> str:
    """Return an absolute command string for invoking ``oh-my-wrist <subcommand>``.

    Claude Code spawns hook and statusLine commands through a shell whose PATH
    does **not** necessarily include the directory holding the ``oh-my-wrist``
    console script — typically a project/virtualenv ``bin/`` that is only on
    PATH when the venv is activated.  A bare ``oh-my-wrist`` therefore fails
    with exit 127 (command not found) and the hook silently never runs.

    We resolve an absolute command at install time instead:
      1. the console script sitting next to the running interpreter, if present
         (covers venv and pipx installs — the script lives in the same dir as
         ``sys.executable``);
      2. otherwise ``"<python>" -m ohm <subcommand>``, which only requires that
         this same interpreter can import ``ohm`` (see ``ohm/__main__.py``).
    """
    script = Path(sys.executable).parent / (
        "oh-my-wrist.exe" if sys.platform == "win32" else "oh-my-wrist"
    )
    if script.exists():
        base = f'"{script}"' if " " in str(script) else str(script)
    else:
        py = f'"{sys.executable}"' if " " in sys.executable else sys.executable
        base = f"{py} -m ohm"
    return f"{base} {subcommand}"


def _is_ohm_command(command: str | None, subcommand: str) -> bool:
    """True if *command* invokes our *subcommand* in any install form.

    Recognises the legacy bare ``oh-my-wrist hook``, the absolute
    console-script path written by current installs, and the
    ``<python> -m ohm hook`` fallback — so re-install heals stale entries and
    uninstall removes entries written by older versions.
    """
    if not command:
        return False
    parts = command.split()
    if not parts or parts[-1] != subcommand:
        return False
    return "oh-my-wrist" in command or "-m ohm" in command


_HOOK_COMMAND = _ohm_command("hook")

_HOOK_ENTRY = {"type": "command", "command": _HOOK_COMMAND, "async": True}

_HOOK_EVENTS = {
    "PreToolUse": [{"matcher": "", "hooks": [_HOOK_ENTRY]}],
    "PostToolUse": [{"matcher": "", "hooks": [_HOOK_ENTRY]}],
    "Notification": [{"hooks": [_HOOK_ENTRY]}],
    "Stop": [{"hooks": [_HOOK_ENTRY]}],
    "SubagentStop": [{"hooks": [_HOOK_ENTRY]}],
}

CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# statusLine integration — forwards /usage quota to the watch.  Unlike hooks
# (a list), statusLine is single-valued, so we save any pre-existing command
# and chain to it from our relay (see statusline_relay.py).
_STATUSLINE_COMMAND = _ohm_command("statusline")
_STATUSLINE_ENTRY = {"type": "command", "command": _STATUSLINE_COMMAND}
_PREV_STATUSLINE_PATH = Path.home() / ".oh-my-wrist" / "prev_statusline"
_PREV_STATUSLINE_JSON_PATH = Path.home() / ".oh-my-wrist" / "prev_statusline.json"


def _statusline_state_paths() -> tuple[Path, Path]:
    """Return state paths rooted beside the active Claude config home."""
    config_root = CLAUDE_SETTINGS_PATH.parent.parent
    state_root = config_root / ".oh-my-wrist"
    return state_root / "prev_statusline", state_root / "prev_statusline.json"

CODEX_HOOKS_PATH = Path.home() / ".codex" / "hooks.json"
_CODEX_HOOK_COMMAND = _ohm_command("codex-hook")
_CODEX_HOOK_ENTRY = {
    "type": "command",
    "command": _CODEX_HOOK_COMMAND,
    "timeout": 2,
}
_CODEX_HOOK_EVENTS = {
    "Stop": [{"hooks": [_CODEX_HOOK_ENTRY]}],
    "SubagentStop": [{"hooks": [_CODEX_HOOK_ENTRY]}],
}

# ---------------------------------------------------------------------------
# OpenCode plugin configuration
# ---------------------------------------------------------------------------

# Name of the plugin file placed inside ~/.config/opencode/plugins/
OPENCODE_PLUGIN_FILENAME = "oh_my_wrist_opencode.ts"

# Bundled plugin source shipped with this package
_PLUGIN_SOURCE_PATH = (
    Path(__file__).parent.parent.parent
    / "opencode"
    / "plugins"
    / OPENCODE_PLUGIN_FILENAME
)

# Global plugin directory — auto-loaded by OpenCode at startup
_OPENCODE_GLOBAL_PLUGINS_DIR = Path.home() / ".config" / "opencode" / "plugins"

# Legacy per-project entry (kept for removal/migration)
_OPENCODE_PLUGIN_ENTRY = f"./plugins/{OPENCODE_PLUGIN_FILENAME}"


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write *data* as JSON to *path* using an atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* using an atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _backup_file(path: Path, label: str) -> Path | None:
    """Copy an existing config into the private oh-my-wrist backup directory."""
    if not path.exists():
        return None
    # Keep isolated tests and alternate config roots isolated too. For the
    # normal ~/.claude and ~/.codex paths this resolves to the real home.
    config_root = path.parent.parent if path.parent.name.startswith(".") else Path.home()
    backup_dir = config_root / ".oh-my-wrist" / "backups"
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = backup_dir / (
        f"{label}-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000}.bak"
    )
    backup.write_bytes(path.read_bytes())
    os.chmod(backup, 0o600)
    return backup


# ---------------------------------------------------------------------------
# Claude Code hook patching
# ---------------------------------------------------------------------------


def _is_hook_present(hooks_list: list[dict]) -> bool:
    """Return True if an oh-my-wrist hook command is present in *hooks_list*.

    Matches any install form (legacy bare command, absolute path, or the
    ``-m ohm`` fallback) so re-install can prune and replace stale entries.
    """
    for entry in hooks_list:
        for hook in entry.get("hooks", []):
            if _is_ohm_command(hook.get("command"), "hook"):
                return True
    return False


def patch_claude_settings() -> None:
    """Merge the oh-my-wrist hook entries into Claude Code's settings.json."""
    settings = _load_claude_settings()

    hooks = settings.setdefault("hooks", {})
    changed = False

    for event, entries in _HOOK_EVENTS.items():
        existing = hooks.setdefault(event, [])
        # Prune any prior oh-my-wrist entries (legacy bare command or a stale
        # absolute path from an older install) so re-running install heals the
        # command path, then append the canonical entry exactly once.
        pruned = [e for e in existing if not _is_hook_present([e])]
        new_list = pruned + list(entries)
        if new_list != existing:
            hooks[event] = new_list
            changed = True
            logger.info("Configured oh-my-wrist hook for event '{}'", event)
        else:
            logger.info("Hook for event '{}' already current — skipping", event)

    if changed:
        _backup_file(CLAUDE_SETTINGS_PATH, "claude-settings")
        _atomic_write_json(CLAUDE_SETTINGS_PATH, settings)
        logger.info("Claude Code settings patched at {}", CLAUDE_SETTINGS_PATH)
    else:
        logger.info("No changes needed to Claude Code settings")


def remove_claude_hooks() -> None:
    """Remove oh-my-wrist hook entries from Claude Code's settings.json."""
    settings = _load_claude_settings()

    hooks = settings.get("hooks", {})
    changed = False

    for event in list(hooks.keys()):
        original = hooks[event]
        filtered = [entry for entry in original if not _is_hook_present([entry])]
        if len(filtered) != len(original):
            hooks[event] = filtered
            changed = True
            logger.info("Removed hook for event '{}'", event)

    if changed:
        _backup_file(CLAUDE_SETTINGS_PATH, "claude-settings")
        _atomic_write_json(CLAUDE_SETTINGS_PATH, settings)
        logger.info("Claude Code hooks removed from {}", CLAUDE_SETTINGS_PATH)


def patch_codex_hooks() -> None:
    """Add lifecycle hooks without changing Codex's occupied notify command."""
    if CODEX_HOOKS_PATH.exists():
        try:
            data = json.loads(CODEX_HOOKS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Codex hooks file is invalid JSON: {exc}") from exc
    else:
        data = {}
    hooks = data.setdefault("hooks", {})
    changed = False
    # Migrate entries written by older oh-my-wrist versions. Codex SessionEnd
    # means the conversation was closed/archived or sat idle for 30 minutes;
    # normal turn completion is Stop. Remove only our entries before installing
    # the current event set, leaving every unrelated hook untouched.
    for event in list(hooks):
        if event in _CODEX_HOOK_EVENTS:
            continue
        existing = hooks[event]
        pruned = _remove_hook_handlers(existing, "codex-hook")
        if pruned != existing:
            if pruned:
                hooks[event] = pruned
            else:
                del hooks[event]
            changed = True
    for event, entries in _CODEX_HOOK_EVENTS.items():
        existing = hooks.setdefault(event, [])
        pruned = _remove_hook_handlers(existing, "codex-hook")
        replacement = pruned + entries
        if replacement != existing:
            hooks[event] = replacement
            changed = True
    if changed:
        _backup_file(CODEX_HOOKS_PATH, "codex-hooks")
        _atomic_write_json(CODEX_HOOKS_PATH, data)


def remove_codex_hooks() -> None:
    if not CODEX_HOOKS_PATH.exists():
        return
    data = json.loads(CODEX_HOOKS_PATH.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    changed = False
    for event in list(hooks):
        filtered = _remove_hook_handlers(hooks[event], "codex-hook")
        if filtered != hooks[event]:
            if filtered:
                hooks[event] = filtered
            else:
                del hooks[event]
            changed = True
    if changed:
        _backup_file(CODEX_HOOKS_PATH, "codex-hooks")
        _atomic_write_json(CODEX_HOOKS_PATH, data)


def _is_hook_present_for(entry: dict, subcommand: str) -> bool:
    return any(
        _is_ohm_command(hook.get("command"), subcommand)
        for hook in entry.get("hooks", [])
    )


def _remove_hook_handlers(entries: list[dict], subcommand: str) -> list[dict]:
    """Remove only matching handlers while preserving their containing groups."""
    result: list[dict] = []
    for entry in entries:
        handlers = entry.get("hooks")
        if not isinstance(handlers, list):
            result.append(entry)
            continue
        filtered = [
            handler
            for handler in handlers
            if not _is_ohm_command(handler.get("command"), subcommand)
        ]
        if filtered == handlers:
            result.append(entry)
        elif filtered:
            updated = dict(entry)
            updated["hooks"] = filtered
            result.append(updated)
    return result


# ---------------------------------------------------------------------------
# Claude Code statusLine patching (usage quota → watch)
# ---------------------------------------------------------------------------


def _load_claude_settings() -> dict:
    """Return the parsed settings.json, or {} if absent.

    Invalid user configuration is never overwritten. The caller surfaces the
    parse error so the user can repair or restore it explicitly.
    """
    if not CLAUDE_SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(CLAUDE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude settings file is invalid JSON: {exc}") from exc


def patch_claude_statusline() -> None:
    """Set our statusLine command, chaining any pre-existing one.

    A user's existing statusLine (single-valued, unlike hooks) is saved to
    ``_PREV_STATUSLINE_PATH`` so the relay can run it and pass its output
    through; uninstall restores it.
    """
    settings = _load_claude_settings()
    prev_path, prev_json_path = _statusline_state_paths()
    existing = settings.get("statusLine")
    existing_cmd = existing.get("command") if isinstance(existing, dict) else None

    if existing_cmd == _STATUSLINE_COMMAND:
        # A prior 0.1.x install may have replaced the whole object. Heal any
        # saved presentation/timing keys without changing the relay command.
        saved = {}
        if prev_json_path.exists():
            candidate = json.loads(
                prev_json_path.read_text(encoding="utf-8")
            )
            if isinstance(candidate, dict):
                saved = candidate
        replacement = {**saved, **existing}
        if replacement != existing:
            settings["statusLine"] = replacement
            _backup_file(CLAUDE_SETTINGS_PATH, "claude-settings")
            _atomic_write_json(CLAUDE_SETTINGS_PATH, settings)
            logger.info("Restored saved statusLine options")
        else:
            logger.info("statusLine already configured — skipping")
        return

    # An older oh-my-wrist install (bare command or stale absolute path) is
    # ours, not the user's: upgrade it in place without saving it as the
    # "previous" command to restore on uninstall.
    if _is_ohm_command(existing_cmd, "statusline"):
        replacement = dict(existing) if isinstance(existing, dict) else {}
        replacement.update(_STATUSLINE_ENTRY)
        settings["statusLine"] = replacement
        _backup_file(CLAUDE_SETTINGS_PATH, "claude-settings")
        _atomic_write_json(CLAUDE_SETTINGS_PATH, settings)
        logger.info("Upgraded oh-my-wrist statusLine to an absolute command")
        return

    # Preserve the user's prior statusLine command for chaining.
    if isinstance(existing_cmd, str):
        _atomic_write_text(prev_path, existing_cmd)
        _atomic_write_json(prev_json_path, existing)
        logger.info("Saved existing statusLine for chaining")

    replacement = dict(existing) if isinstance(existing, dict) else {}
    replacement.update(_STATUSLINE_ENTRY)
    settings["statusLine"] = replacement
    _backup_file(CLAUDE_SETTINGS_PATH, "claude-settings")
    _atomic_write_json(CLAUDE_SETTINGS_PATH, settings)
    logger.info("Claude Code statusLine patched at {}", CLAUDE_SETTINGS_PATH)


def remove_claude_statusline() -> None:
    """Restore the saved statusLine (or remove ours) and clear the saved copy."""
    settings = _load_claude_settings()
    prev_path, prev_json_path = _statusline_state_paths()
    current = settings.get("statusLine")
    current_cmd = current.get("command") if isinstance(current, dict) else None

    # Only touch the setting if it is ours (any install form).
    if _is_ohm_command(current_cmd, "statusline"):
        if prev_path.exists():
            if prev_json_path.exists():
                settings["statusLine"] = json.loads(
                    prev_json_path.read_text(encoding="utf-8")
                )
            else:
                prev = prev_path.read_text(encoding="utf-8").strip()
                settings["statusLine"] = {"type": "command", "command": prev}
            logger.info("Restored previous statusLine")
        else:
            settings.pop("statusLine", None)
            logger.info("Removed oh-my-wrist statusLine")
        _backup_file(CLAUDE_SETTINGS_PATH, "claude-settings")
        _atomic_write_json(CLAUDE_SETTINGS_PATH, settings)

    if prev_path.exists():
        prev_path.unlink()
    if prev_json_path.exists():
        prev_json_path.unlink()


# ---------------------------------------------------------------------------
# OpenCode project detection
# ---------------------------------------------------------------------------


def _has_legacy_opencode_layout(root: Path) -> bool:
    """Return True if *root* uses the legacy `opencode/` scaffold.

    Older project templates stored OpenCode files under `opencode/` in the
    repository rather than `.opencode/` in the project root.
    """
    legacy_config = root / "opencode" / "opencode.json"
    legacy_plugins_dir = root / "opencode" / "plugins"
    return legacy_config.is_file() or legacy_plugins_dir.is_dir()


def find_opencode_project_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) to find an OpenCode project root.

    A root is considered OpenCode-enabled if it contains either:
      - `.opencode/` (current layout), or
      - `opencode/opencode.json` / `opencode/plugins/` (legacy layout).

    Returns the project root Path if found, otherwise None.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / ".opencode").is_dir() or _has_legacy_opencode_layout(current):
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def is_opencode_project(start: Path | None = None) -> bool:
    """Return True if the current directory is inside an OpenCode project."""
    return find_opencode_project_root(start) is not None


def is_opencode_installed() -> bool:
    """Return True if OpenCode appears to be installed on this system."""
    # Check for the global config directory or the opencode binary
    if _OPENCODE_GLOBAL_PLUGINS_DIR.parent.exists():
        return True
    # Check PATH for the opencode binary
    import shutil

    return shutil.which("opencode") is not None


# ---------------------------------------------------------------------------
# OpenCode plugin installation
# ---------------------------------------------------------------------------


def _get_plugin_source() -> str:
    """Return the TypeScript plugin source text.

    Tries the bundled file first; falls back to an embedded minimal stub
    so that installation never fails even if the package is partially
    installed.
    """
    if _PLUGIN_SOURCE_PATH.exists():
        return _PLUGIN_SOURCE_PATH.read_text(encoding="utf-8")

    # Fallback: inline minimal plugin stub
    return _OPENCODE_PLUGIN_STUB


_OPENCODE_PLUGIN_STUB = """\
// oh_my_wrist_opencode.ts — auto-generated stub
// Full source is in opencode/plugins/ of the oh-my-wrist package.
import * as net from "net";
import * as os from "os";
import * as path from "path";

function unixSocketPath(): string {
  const uid = typeof process.getuid === "function"
    ? process.getuid()
    : os.userInfo().uid;
  return path.join("/tmp", `oh-my-wrist-${uid}`, "ohm.sock");
}

const SOCKET_PATH = process.platform === "win32"
  ? String.raw`\\\\.\\pipe\\ohm`
  : unixSocketPath();

async function sendToDaemon(payload: object): Promise<void> {
  const json = JSON.stringify(payload) + "\\n";
  return new Promise<void>((resolve) => {
    try {
      const sock = net.createConnection(SOCKET_PATH);
      sock.on("connect", () => { sock.write(json, "utf8", () => { sock.end(); resolve(); }); });
      sock.on("error", () => resolve());
      sock.setTimeout(500, () => { sock.destroy(); resolve(); });
    } catch { resolve(); }
  });
}

const OhMyWristPlugin = async (_ctx: unknown) => {
  return {
    "tool.execute.before": async (input: any, output: any) => {
      await sendToDaemon({
        provider: "opencode", provider_event: "tool.execute.before",
        canonical_event: "tool_start", session_id: input?.sessionID ?? null,
        tool_name: input?.tool ?? null, label: null, path: null,
        status_text: null, active: true, alert_type: 0,
        ts: Date.now() / 1000, meta: {},
      });
    },
    "tool.execute.after": async (input: any, output: any) => {
      await sendToDaemon({
        provider: "opencode", provider_event: "tool.execute.after",
        canonical_event: "tool_end", session_id: input?.sessionID ?? null,
        tool_name: input?.tool ?? null, label: null, path: null,
        status_text: null, active: false, alert_type: 0,
        ts: Date.now() / 1000, meta: {},
      });
    },
    event: async ({ event }: { event: any }) => {
      if (!event?.type) return;
      const type = event.type;
      if (type === "session.created" || type === "session.idle" || type === "session.error") {
        const ce = type === "session.created" ? "session_start"
          : type === "session.idle" ? "session_idle" : "session_error";
        await sendToDaemon({
          provider: "opencode", provider_event: type,
          canonical_event: ce, session_id: event.sessionId ?? null,
          tool_name: null, label: null, path: null,
          status_text: null, active: type === "session.created",
          alert_type: type === "session.idle" ? 0x01 : type === "session.error" ? 0x02 : 0,
          ts: Date.now() / 1000, meta: {},
        });
      }
    },
  };
};

export default OhMyWristPlugin;
"""


def install_opencode_plugin(project_root: Path | None = None) -> bool:
    """Install the TypeScript plugin globally at ~/.config/opencode/plugins/.

    This makes the plugin available to all OpenCode sessions regardless of
    which project directory they run in.

    Parameters
    ----------
    project_root:
        Ignored (kept for API compatibility). The plugin is always installed
        globally.

    Returns
    -------
    bool
        True if the plugin was installed; False if OpenCode is not detected.
    """
    if not is_opencode_installed():
        logger.info("OpenCode not detected — skipping plugin installation")
        return False

    _OPENCODE_GLOBAL_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    dest = _OPENCODE_GLOBAL_PLUGINS_DIR / OPENCODE_PLUGIN_FILENAME

    # Remove superseded plugin filename from prior project name.
    legacy_global = _OPENCODE_GLOBAL_PLUGINS_DIR / "claude_garmin_opencode.ts"
    if legacy_global.exists():
        legacy_global.unlink()
        logger.info("Removed legacy OpenCode plugin at {}", legacy_global)

    source = _get_plugin_source()
    _atomic_write_text(dest, source)
    logger.info("OpenCode plugin installed globally at {}", dest)
    return True


def _normalise_plugin_array(config: dict) -> list[str]:
    """Return a writable `plugin` array, migrating legacy shapes when needed."""
    raw_plugins = config.get("plugin")

    if isinstance(raw_plugins, list):
        plugins = [p for p in raw_plugins if isinstance(p, str) and p]
        if len(plugins) != len(raw_plugins):
            logger.warning(
                "Ignoring non-string entries in opencode.json 'plugin' array"
            )
        # De-duplicate while preserving order
        config["plugin"] = list(dict.fromkeys(plugins))
        return config["plugin"]

    migrated: list[str] = []
    legacy_plugins = config.get("plugins")
    if isinstance(legacy_plugins, list):
        for entry in legacy_plugins:
            if isinstance(entry, str) and entry:
                migrated.append(entry)
                continue
            if isinstance(entry, dict):
                path_val = entry.get("path")
                if isinstance(path_val, str) and path_val:
                    migrated.append(path_val)
        if migrated:
            logger.info(
                "Migrated {} plugin path(s) from legacy 'plugins' key",
                len(migrated),
            )

    config["plugin"] = list(dict.fromkeys(migrated))
    return config["plugin"]


def _patch_opencode_json(project_root: Path) -> None:
    """Add the oh-my-wrist plugin entry to .opencode/opencode.json."""
    config_path = project_root / ".opencode" / "opencode.json"

    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                logger.warning("opencode.json root is not an object — creating fresh")
                config = {}
        except json.JSONDecodeError:
            logger.warning("opencode.json is not valid JSON — creating fresh")
            config = {}
    else:
        config = {}

    plugins = _normalise_plugin_array(config)

    if _OPENCODE_PLUGIN_ENTRY in plugins:
        logger.info("OpenCode plugin already registered in opencode.json")
        return

    plugins.append(_OPENCODE_PLUGIN_ENTRY)
    _atomic_write_json(config_path, config)
    logger.info("OpenCode plugin registered in {}", config_path)


def remove_opencode_plugin(project_root: Path | None = None) -> bool:
    """Remove the TypeScript plugin from the global plugins directory.

    Also cleans up any legacy per-project installation if found.

    Returns True if removal was performed, False if nothing to remove.
    """
    removed = False

    # Remove from global location
    global_path = _OPENCODE_GLOBAL_PLUGINS_DIR / OPENCODE_PLUGIN_FILENAME
    if global_path.exists():
        global_path.unlink()
        logger.info("OpenCode plugin removed from {}", global_path)
        removed = True

    # Clean up legacy per-project installation if present
    root = project_root or find_opencode_project_root()
    if root is not None:
        legacy_path = root / ".opencode" / "plugins" / OPENCODE_PLUGIN_FILENAME
        if legacy_path.exists():
            legacy_path.unlink()
            logger.info("Legacy per-project plugin removed from {}", legacy_path)
            removed = True

        # Remove from opencode.json if present
        config_path = root / ".opencode" / "opencode.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(config, dict):
                    plugins = config.get("plugin")
                    if isinstance(plugins, list):
                        new_plugins = [
                            p for p in plugins if p != _OPENCODE_PLUGIN_ENTRY
                        ]
                        if len(new_plugins) != len(plugins):
                            config["plugin"] = new_plugins
                            _atomic_write_json(config_path, config)
                            logger.info("Plugin entry removed from {}", config_path)
            except json.JSONDecodeError:
                pass

    return removed


# ---------------------------------------------------------------------------
# Combined install / uninstall
# ---------------------------------------------------------------------------


def install_all(provider: str = "all", project_root: Path | None = None) -> None:
    """Install hooks/plugins for the specified provider(s).

    Parameters
    ----------
    provider:
        ``"claude"`` — Claude Code only.
        ``"opencode"`` — OpenCode only.
        ``"both"`` (default) — both providers.
    """
    if provider in ("claude", "both", "all"):
        patch_claude_settings()
        patch_claude_statusline()

    if provider in ("codex", "all"):
        patch_codex_hooks()

    if provider in ("opencode", "both", "all"):
        install_opencode_plugin(project_root)

    install_service()
    logger.info("Installation complete (provider={})", provider)


def uninstall_all(provider: str = "all", project_root: Path | None = None) -> None:
    """Remove hooks/plugins for the specified provider(s)."""
    if provider in ("claude", "both", "all"):
        remove_claude_hooks()
        remove_claude_statusline()

    if provider in ("codex", "all"):
        remove_codex_hooks()

    if provider in ("opencode", "both", "all"):
        remove_opencode_plugin(project_root)

    uninstall_service()
    logger.info("Uninstallation complete (provider={})", provider)


# ---------------------------------------------------------------------------
# Service registration (platform dispatch)
# ---------------------------------------------------------------------------


def install_service() -> None:
    """Register the BLE daemon as a system service for the current platform."""
    if sys.platform == "darwin":
        from ohm.platform.macos import install_service as _install
    elif sys.platform == "win32":
        from ohm.platform.windows import install_service as _install  # type: ignore[assignment]
    else:
        from ohm.platform.linux import install_service as _install  # type: ignore[assignment]
    _install()
    logger.info("System service registered")


def uninstall_service() -> None:
    """Remove the system service registration."""
    if sys.platform == "darwin":
        from ohm.platform.macos import uninstall_service as _uninstall
    elif sys.platform == "win32":
        from ohm.platform.windows import uninstall_service as _uninstall  # type: ignore[assignment]
    else:
        from ohm.platform.linux import uninstall_service as _uninstall  # type: ignore[assignment]
    _uninstall()
    logger.info("System service removed")


def get_service_status() -> str:
    """Return a human-readable service status string."""
    if sys.platform == "darwin":
        from ohm.platform.macos import service_status
    elif sys.platform == "win32":
        from ohm.platform.windows import service_status  # type: ignore[assignment]
    else:
        from ohm.platform.linux import service_status  # type: ignore[assignment]
    return service_status()
