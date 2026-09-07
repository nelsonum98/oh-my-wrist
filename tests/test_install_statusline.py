"""
Tests for statusLine management in install.py

Covers:
- Fresh install (no prior statusLine) sets ours, saves nothing.
- Install over an existing statusLine saves the original and sets ours.
- Re-install is idempotent (does not clobber the saved original).
- Uninstall restores the saved original and clears the saved copy.
- Uninstall with no prior removes our statusLine entirely.
- Uninstall leaves a foreign statusLine untouched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from ohm.install import (
    _STATUSLINE_COMMAND,
    patch_claude_statusline,
    remove_claude_statusline,
)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _paths(tmp_path):
    return (
        tmp_path / ".claude" / "settings.json",
        tmp_path / ".oh-my-wrist" / "prev_statusline",
    )


class TestPatchStatusline:
    def test_fresh_install_sets_ours(self, tmp_path):
        settings, prev = _paths(tmp_path)
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings):
            patch_claude_statusline()
        assert _read(settings)["statusLine"]["command"] == _STATUSLINE_COMMAND
        assert not prev.exists()

    def test_install_over_existing_saves_original(self, tmp_path):
        settings, prev = _paths(tmp_path)
        _write(settings, {"statusLine": {"type": "command", "command": "my-bar.sh"}})
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings):
            patch_claude_statusline()
        assert _read(settings)["statusLine"]["command"] == _STATUSLINE_COMMAND
        assert prev.read_text(encoding="utf-8") == "my-bar.sh"

    def test_reinstall_is_idempotent(self, tmp_path):
        settings, prev = _paths(tmp_path)
        _write(settings, {"statusLine": {"type": "command", "command": "my-bar.sh"}})
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings):
            patch_claude_statusline()
            patch_claude_statusline()  # second call must not overwrite saved original
        assert prev.read_text(encoding="utf-8") == "my-bar.sh"


class TestRemoveStatusline:
    def test_uninstall_restores_original(self, tmp_path):
        settings, prev = _paths(tmp_path)
        _write(settings, {"statusLine": {"type": "command", "command": "my-bar.sh"}})
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings):
            patch_claude_statusline()
            remove_claude_statusline()
        assert _read(settings)["statusLine"]["command"] == "my-bar.sh"
        assert not prev.exists()

    def test_uninstall_no_prior_removes_key(self, tmp_path):
        settings, prev = _paths(tmp_path)
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings):
            patch_claude_statusline()
            remove_claude_statusline()
        assert "statusLine" not in _read(settings)

    def test_uninstall_leaves_foreign_statusline(self, tmp_path):
        settings, prev = _paths(tmp_path)
        _write(settings, {"statusLine": {"type": "command", "command": "other-tool"}})
        with patch("ohm.install.CLAUDE_SETTINGS_PATH", settings):
            remove_claude_statusline()
        assert _read(settings)["statusLine"]["command"] == "other-tool"
