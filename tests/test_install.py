"""
test_install.py — Tests for install.py (Claude Code settings patching).

Covers
------
- patch_claude_settings: creates file if absent, merges without duplicates,
  preserves existing hooks, handles malformed JSON, atomic write behaviour
- remove_claude_hooks: removes only our hooks, leaves others intact,
  handles missing file, handles malformed JSON
- _atomic_write_json: file is written atomically (no temp file left behind),
  content is valid JSON, trailing newline present
- _is_hook_present: detection of our hook command in various list shapes
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# We import the module under test after patching the settings path
from ohm.install import (
    _HOOK_COMMAND,
    _atomic_write_json,
    _is_hook_present,
    patch_claude_settings,
    remove_claude_hooks,
)


# ============================================================================
# Helpers
# ============================================================================


def _write_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================================
# _is_hook_present
# ============================================================================


class TestIsHookPresent:
    def test_empty_list(self):
        assert not _is_hook_present([])

    def test_our_hook_present(self):
        hooks_list = [
            {"matcher": "", "hooks": [{"type": "command", "command": _HOOK_COMMAND}]}
        ]
        assert _is_hook_present(hooks_list)

    def test_different_command_not_present(self):
        hooks_list = [{"hooks": [{"type": "command", "command": "other-tool hook"}]}]
        assert not _is_hook_present(hooks_list)

    def test_multiple_hooks_one_matches(self):
        hooks_list = [
            {"hooks": [{"type": "command", "command": "other-tool"}]},
            {"hooks": [{"type": "command", "command": _HOOK_COMMAND}]},
        ]
        assert _is_hook_present(hooks_list)

    def test_entry_with_no_hooks_key(self):
        hooks_list = [{"matcher": ""}]
        assert not _is_hook_present(hooks_list)

    def test_entry_with_empty_hooks_list(self):
        hooks_list = [{"hooks": []}]
        assert not _is_hook_present(hooks_list)

    def test_hook_with_extra_fields(self):
        hooks_list = [
            {"hooks": [{"type": "command", "command": _HOOK_COMMAND, "async": True}]}
        ]
        assert _is_hook_present(hooks_list)


# ============================================================================
# _atomic_write_json
# ============================================================================


class TestAtomicWriteJson:
    def test_creates_file(self, tmp_path):
        target = tmp_path / "settings.json"
        _atomic_write_json(target, {"key": "value"})
        assert target.exists()

    def test_content_is_valid_json(self, tmp_path):
        target = tmp_path / "settings.json"
        data = {"hooks": {"PreToolUse": []}, "version": 1}
        _atomic_write_json(target, data)
        loaded = json.loads(target.read_text())
        assert loaded == data

    def test_trailing_newline(self, tmp_path):
        target = tmp_path / "settings.json"
        _atomic_write_json(target, {})
        content = target.read_bytes()
        assert content.endswith(b"\n")

    def test_no_temp_file_left_behind(self, tmp_path):
        target = tmp_path / "settings.json"
        _atomic_write_json(target, {"x": 1})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Temp files left behind: {tmp_files}"

    def test_creates_parent_directory(self, tmp_path):
        target = tmp_path / "nested" / "dir" / "settings.json"
        _atomic_write_json(target, {"a": "b"})
        assert target.exists()

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "settings.json"
        _atomic_write_json(target, {"v": 1})
        _atomic_write_json(target, {"v": 2})
        assert _read_settings(target) == {"v": 2}

    def test_unicode_content_preserved(self, tmp_path):
        target = tmp_path / "settings.json"
        data = {"path": "/プロジェクト/ファイル.py", "name": "données"}
        _atomic_write_json(target, data)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == data


# ============================================================================
# patch_claude_settings
# ============================================================================


class TestPatchClaudeSettings:
    def test_creates_settings_file_if_absent(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
        assert settings_path.exists()
        data = _read_settings(settings_path)
        assert "hooks" in data

    def test_all_claude_hook_events_added(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
        data = _read_settings(settings_path)
        for event in (
            "PreToolUse",
            "PostToolUse",
            "Notification",
            "Stop",
            "SubagentStop",
        ):
            assert event in data["hooks"], f"Missing hook event: {event}"

    def test_hook_command_present_in_each_event(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
        data = _read_settings(settings_path)
        for event, entries in data["hooks"].items():
            found = any(
                hook.get("command") == _HOOK_COMMAND
                for entry in entries
                for hook in entry.get("hooks", [])
            )
            assert found, f"Hook command not found in event {event}"

    def test_async_flag_is_true(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
        data = _read_settings(settings_path)
        for event, entries in data["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    if hook.get("command") == _HOOK_COMMAND:
                        assert hook.get("async") is True, (
                            f"async flag not True in event {event}: {hook}"
                        )

    def test_idempotent_double_install(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
            patch_claude_settings()
        data = _read_settings(settings_path)
        # Each event should have exactly one entry with our hook
        for event, entries in data["hooks"].items():
            our_hooks = [
                hook
                for entry in entries
                for hook in entry.get("hooks", [])
                if hook.get("command") == _HOOK_COMMAND
            ]
            assert len(our_hooks) == 1, (
                f"Event {event} has {len(our_hooks)} copies of our hook (expected 1)"
            )

    def test_preserves_existing_hooks(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "other-tool"}],
                    }
                ]
            }
        }
        _write_settings(settings_path, existing)
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
        data = _read_settings(settings_path)
        # The existing hook must still be there
        pre_hooks = data["hooks"]["PreToolUse"]
        other_present = any(
            hook.get("command") == "other-tool"
            for entry in pre_hooks
            for hook in entry.get("hooks", [])
        )
        assert other_present, "Existing hook was removed by patch_claude_settings"

    def test_refuses_to_overwrite_malformed_json(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{not valid json}", encoding="utf-8")
        with (
            patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path),
            pytest.raises(ValueError, match="invalid JSON"),
        ):
            patch_claude_settings()
        assert settings_path.read_text(encoding="utf-8") == "{not valid json}"

    def test_preserves_non_hook_settings_keys(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        existing = {"version": 42, "theme": "dark", "hooks": {}}
        _write_settings(settings_path, existing)
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
        data = _read_settings(settings_path)
        assert data.get("version") == 42
        assert data.get("theme") == "dark"

    def test_preserves_statusline_options_while_replacing_command(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        existing = {
            "statusLine": {
                "type": "command",
                "command": "existing-status",
                "padding": 2,
                "refreshInterval": 300,
            }
        }
        _write_settings(settings_path, existing)
        with (
            patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path),
            patch("ohm.install._PREV_STATUSLINE_PATH", tmp_path / "prev"),
            patch("ohm.install._PREV_STATUSLINE_JSON_PATH", tmp_path / "prev.json"),
        ):
            from ohm.install import patch_claude_statusline

            patch_claude_statusline()
        result = _read_settings(settings_path)["statusLine"]
        assert result["padding"] == 2
        assert result["refreshInterval"] == 300

    def test_output_file_is_valid_json(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
        # Must parse without error
        json.loads(settings_path.read_text(encoding="utf-8"))


# ============================================================================
# remove_claude_hooks
# ============================================================================


class TestRemoveClaudeHooks:
    def test_removes_our_hooks(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
            remove_claude_hooks()
        data = _read_settings(settings_path)
        for event, entries in data.get("hooks", {}).items():
            our_hooks = [
                hook
                for entry in entries
                for hook in entry.get("hooks", [])
                if hook.get("command") == _HOOK_COMMAND
            ]
            assert our_hooks == [], f"Our hook still present in {event}"

    def test_preserves_other_hooks_after_removal(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        existing = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "other-tool"}],
                    },
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": _HOOK_COMMAND, "async": True}
                        ],
                    },
                ]
            }
        }
        _write_settings(settings_path, existing)
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            remove_claude_hooks()
        data = _read_settings(settings_path)
        pre_hooks = data["hooks"]["PreToolUse"]
        other_present = any(
            hook.get("command") == "other-tool"
            for entry in pre_hooks
            for hook in entry.get("hooks", [])
        )
        assert other_present

    def test_no_error_when_file_absent(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            remove_claude_hooks()  # must not raise

    def test_no_error_when_hooks_key_absent(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        _write_settings(settings_path, {"version": 1})
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            remove_claude_hooks()  # must not raise

    def test_refuses_to_modify_malformed_json(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{bad json}", encoding="utf-8")
        with (
            patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path),
            pytest.raises(ValueError, match="invalid JSON"),
        ):
            remove_claude_hooks()
        assert settings_path.read_text(encoding="utf-8") == "{bad json}"

    def test_idempotent_double_removal(self, tmp_path):
        settings_path = tmp_path / ".claude" / "settings.json"
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings_path):
            patch_claude_settings()
            remove_claude_hooks()
            remove_claude_hooks()  # second call must not raise
