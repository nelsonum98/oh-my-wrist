"""
test_install_extended.py — Tests for the OpenCode-related parts of install.py.

Covers project discovery, plugin installation/removal, and atomic write helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ohm.install import (
    OPENCODE_PLUGIN_FILENAME,
    _OPENCODE_PLUGIN_ENTRY,
    _atomic_write_json,
    _atomic_write_text,
    _get_plugin_source,
    find_opencode_project_root,
    install_opencode_plugin,
    remove_opencode_plugin,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal OpenCode project structure."""
    (tmp_path / ".opencode").mkdir()
    return tmp_path


@pytest.fixture
def nested_project(tmp_path: Path) -> tuple[Path, Path]:
    """Create a project with a nested subdirectory."""
    root = tmp_path / "myproject"
    root.mkdir()
    (root / ".opencode").mkdir()
    subdir = root / "src" / "pkg"
    subdir.mkdir(parents=True)
    return root, subdir


@pytest.fixture
def legacy_layout_project(tmp_path: Path) -> Path:
    """Create a project using the legacy `opencode/` scaffold."""
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "opencode" / "plugins").mkdir(parents=True)
    (root / "opencode" / "opencode.json").write_text(
        json.dumps({"plugins": [{"name": "legacy", "path": "./plugins/legacy.ts"}]}),
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# find_opencode_project_root()
# ---------------------------------------------------------------------------


class TestFindOpencodeProjectRoot:
    def test_finds_root_at_start(self, tmp_project):
        result = find_opencode_project_root(tmp_project)
        assert result == tmp_project

    def test_finds_root_from_subdirectory(self, nested_project):
        root, subdir = nested_project
        result = find_opencode_project_root(subdir)
        assert result == root

    def test_finds_root_from_legacy_layout(self, legacy_layout_project):
        nested = legacy_layout_project / "src" / "pkg"
        nested.mkdir(parents=True)
        result = find_opencode_project_root(nested)
        assert result == legacy_layout_project

    def test_returns_none_when_not_found(self, tmp_path):
        # tmp_path has no .opencode directory
        result = find_opencode_project_root(tmp_path)
        assert result is None

    def test_stops_at_filesystem_root(self, tmp_path):
        # Ensure we don't loop forever
        result = find_opencode_project_root(
            Path("/nonexistent/path/that/does/not/exist")
        )
        assert result is None

    def test_deeply_nested_subdir(self, nested_project):
        root, _ = nested_project
        deep = root / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        result = find_opencode_project_root(deep)
        assert result == root


# ---------------------------------------------------------------------------
# install_opencode_plugin()
# ---------------------------------------------------------------------------


class TestInstallOpencodePlugin:
    @pytest.fixture(autouse=True)
    def _patch_global_dir(self, tmp_path, monkeypatch):
        """Redirect global plugin dir to a temp path for testing."""
        self.global_plugins_dir = tmp_path / ".config" / "opencode" / "plugins"
        # Create the parent so is_opencode_installed() returns True
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        monkeypatch.setattr(
            "ohm.install._OPENCODE_GLOBAL_PLUGINS_DIR",
            self.global_plugins_dir,
        )

    def test_creates_plugin_file(self, tmp_project):
        result = install_opencode_plugin(tmp_project)
        assert result is True
        plugin_path = self.global_plugins_dir / OPENCODE_PLUGIN_FILENAME
        assert plugin_path.exists()

    def test_plugin_file_contains_typescript(self, tmp_project):
        install_opencode_plugin(tmp_project)
        plugin_path = self.global_plugins_dir / OPENCODE_PLUGIN_FILENAME
        content = plugin_path.read_text(encoding="utf-8")
        assert "OhMyWristPlugin" in content
        assert "sendToDaemon" in content
        assert "export default OhMyWristPlugin" in content
        assert "export const OhMyWristPlugin" not in content

    def test_plugin_file_uses_private_unix_socket_path(self, tmp_project):
        install_opencode_plugin(tmp_project)
        plugin_path = self.global_plugins_dir / OPENCODE_PLUGIN_FILENAME
        content = plugin_path.read_text(encoding="utf-8")
        assert "oh-my-wrist" in content
        assert "ohm.sock" in content
        assert "/tmp/ohm.sock" not in content
        assert "os.tmpdir" not in content

    def test_missing_bundled_plugin_fails_clearly(self, tmp_path, monkeypatch):
        missing = tmp_path / "missing" / OPENCODE_PLUGIN_FILENAME
        monkeypatch.setattr("ohm.install._PLUGIN_SOURCE_PATH", missing)

        with pytest.raises(FileNotFoundError, match=str(missing)):
            _get_plugin_source()

    def test_idempotent_install(self, tmp_project):
        install_opencode_plugin(tmp_project)
        install_opencode_plugin(tmp_project)
        plugin_path = self.global_plugins_dir / OPENCODE_PLUGIN_FILENAME
        assert plugin_path.exists()

    def test_returns_false_when_opencode_not_installed(self, tmp_path, monkeypatch):
        # Point to a non-existent parent dir so is_opencode_installed() is False
        monkeypatch.setattr(
            "ohm.install._OPENCODE_GLOBAL_PLUGINS_DIR",
            tmp_path / "nonexistent" / "plugins",
        )
        with patch("shutil.which", return_value=None):
            result = install_opencode_plugin()
        assert result is False

    def test_creates_plugins_dir_if_missing(self, tmp_project):
        install_opencode_plugin(tmp_project)
        assert self.global_plugins_dir.exists()


# ---------------------------------------------------------------------------
# remove_opencode_plugin()
# ---------------------------------------------------------------------------


class TestRemoveOpencodePlugin:
    @pytest.fixture(autouse=True)
    def _patch_global_dir(self, tmp_path, monkeypatch):
        """Redirect global plugin dir to a temp path for testing."""
        self.global_plugins_dir = tmp_path / ".config" / "opencode" / "plugins"
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        monkeypatch.setattr(
            "ohm.install._OPENCODE_GLOBAL_PLUGINS_DIR",
            self.global_plugins_dir,
        )

    def test_removes_plugin_file(self, tmp_project):
        install_opencode_plugin(tmp_project)
        remove_opencode_plugin(tmp_project)
        plugin_path = self.global_plugins_dir / OPENCODE_PLUGIN_FILENAME
        assert not plugin_path.exists()

    def test_removes_legacy_per_project_file(self, tmp_project):
        # Simulate a legacy per-project install
        legacy_path = tmp_project / ".opencode" / "plugins" / OPENCODE_PLUGIN_FILENAME
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text("// legacy", encoding="utf-8")
        remove_opencode_plugin(tmp_project)
        assert not legacy_path.exists()

    def test_removes_entry_from_opencode_json(self, tmp_project):
        config_path = tmp_project / ".opencode" / "opencode.json"
        config_path.write_text(
            json.dumps({"plugin": [_OPENCODE_PLUGIN_ENTRY]}), encoding="utf-8"
        )
        remove_opencode_plugin(tmp_project)
        data = json.loads(config_path.read_text())
        assert _OPENCODE_PLUGIN_ENTRY not in data.get("plugin", [])

    def test_preserves_other_plugins(self, tmp_project):
        config_path = tmp_project / ".opencode" / "opencode.json"
        config_path.write_text(
            json.dumps({"plugin": ["./plugins/other.ts", _OPENCODE_PLUGIN_ENTRY]}),
            encoding="utf-8",
        )
        remove_opencode_plugin(tmp_project)
        data = json.loads(config_path.read_text())
        plugins = data.get("plugin", [])
        assert "./plugins/other.ts" in plugins
        assert _OPENCODE_PLUGIN_ENTRY not in plugins

    def test_returns_false_when_nothing_to_remove(self, tmp_path):
        with patch("ohm.install.find_opencode_project_root", return_value=None):
            result = remove_opencode_plugin()
        assert result is False

    def test_no_error_when_plugin_file_missing(self, tmp_project):
        # Nothing installed — should not raise
        result = remove_opencode_plugin(tmp_project)
        assert result is False  # nothing was removed

    def test_handles_corrupt_opencode_json_on_remove(self, tmp_project):
        install_opencode_plugin(tmp_project)
        config_path = tmp_project / ".opencode" / "opencode.json"
        config_path.write_text("{corrupt", encoding="utf-8")
        # Should not raise
        remove_opencode_plugin(tmp_project)


# ---------------------------------------------------------------------------
# Atomic write helpers
# ---------------------------------------------------------------------------


class TestAtomicWriteHelpers:
    def test_atomic_write_json_creates_file(self, tmp_path):
        path = tmp_path / "test.json"
        _atomic_write_json(path, {"key": "value"})
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["key"] == "value"

    def test_atomic_write_json_overwrites(self, tmp_path):
        path = tmp_path / "test.json"
        _atomic_write_json(path, {"a": 1})
        _atomic_write_json(path, {"b": 2})
        data = json.loads(path.read_text())
        assert "a" not in data
        assert data["b"] == 2

    def test_atomic_write_json_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "file.json"
        _atomic_write_json(path, {"x": 1})
        assert path.exists()

    def test_atomic_write_text_creates_file(self, tmp_path):
        path = tmp_path / "test.ts"
        _atomic_write_text(path, "const x = 1;")
        assert path.read_text() == "const x = 1;"

    def test_atomic_write_text_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "c.ts"
        _atomic_write_text(path, "// hello")
        assert path.exists()

    def test_atomic_write_json_unicode(self, tmp_path):
        path = tmp_path / "unicode.json"
        _atomic_write_json(path, {"accented": "données", "japanese": "ファイル"})
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["accented"] == "données"
        assert data["japanese"] == "ファイル"
