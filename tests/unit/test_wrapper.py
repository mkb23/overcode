"""
Unit tests for wrapper resolution.
"""

import os
import stat
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.wrapper import resolve_wrapper, list_available_wrappers, _wrappers_dir


class TestResolveWrapperAbsolutePath:
    """Test resolution of absolute paths."""

    def test_existing_executable(self, tmp_path):
        script = tmp_path / "my-wrapper.sh"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        result = resolve_wrapper(str(script))
        assert result == str(script)

    def test_existing_non_executable(self, tmp_path):
        script = tmp_path / "not-exec.sh"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        # Don't set executable bit

        result = resolve_wrapper(str(script))
        assert result is None

    def test_nonexistent_absolute_path(self):
        result = resolve_wrapper("/nonexistent/path/wrapper.sh")
        assert result is None

    def test_directory_not_file(self, tmp_path):
        d = tmp_path / "a-dir"
        d.mkdir()

        result = resolve_wrapper(str(d))
        assert result is None


class TestResolveWrapperRelativePath:
    """Test resolution of relative paths (containing /)."""

    def test_relative_path_resolved(self, tmp_path, monkeypatch):
        subdir = tmp_path / "wrappers"
        subdir.mkdir()
        script = subdir / "test.sh"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        monkeypatch.chdir(tmp_path)
        result = resolve_wrapper("wrappers/test.sh")
        assert result == str(script)

    def test_relative_path_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = resolve_wrapper("./nonexistent.sh")
        assert result is None

    def test_dot_slash_prefix(self, tmp_path, monkeypatch):
        script = tmp_path / "local.sh"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        monkeypatch.chdir(tmp_path)
        result = resolve_wrapper("./local.sh")
        assert result == str(script)


class TestResolveWrapperBareName:
    """Test resolution of bare names via ~/.overcode/wrappers/."""

    def test_exact_name_match(self, tmp_path):
        wrappers = tmp_path / "wrappers"
        wrappers.mkdir(parents=True)
        script = wrappers / "devcontainer"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        with patch("overcode.wrapper._wrappers_dir", return_value=wrappers):
            result = resolve_wrapper("devcontainer")
        assert result == str(script)

    def test_sh_extension_fallback(self, tmp_path):
        wrappers = tmp_path / "wrappers"
        wrappers.mkdir(parents=True)
        script = wrappers / "devcontainer.sh"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        with patch("overcode.wrapper._wrappers_dir", return_value=wrappers):
            result = resolve_wrapper("devcontainer")
        assert result == str(script)

    def test_py_extension_fallback(self, tmp_path):
        wrappers = tmp_path / "wrappers"
        wrappers.mkdir(parents=True)
        script = wrappers / "custom.py"
        script.write_text("#!/usr/bin/env python3\nimport sys; sys.exit(0)")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        with patch("overcode.wrapper._wrappers_dir", return_value=wrappers):
            result = resolve_wrapper("custom")
        assert result == str(script)

    def test_exact_name_preferred_over_extension(self, tmp_path):
        """Exact name match takes priority over .sh extension."""
        wrappers = tmp_path / "wrappers"
        wrappers.mkdir(parents=True)
        exact = wrappers / "mywrap"
        exact.write_text("#!/bin/bash\n# exact")
        exact.chmod(exact.stat().st_mode | stat.S_IEXEC)
        with_ext = wrappers / "mywrap.sh"
        with_ext.write_text("#!/bin/bash\n# with ext")
        with_ext.chmod(with_ext.stat().st_mode | stat.S_IEXEC)

        with patch("overcode.wrapper._wrappers_dir", return_value=wrappers):
            result = resolve_wrapper("mywrap")
        assert result == str(exact)

    def test_bare_name_not_found(self, tmp_path):
        wrappers = tmp_path / "wrappers"
        wrappers.mkdir(parents=True)

        with patch("overcode.wrapper._wrappers_dir", return_value=wrappers):
            result = resolve_wrapper("nonexistent")
        assert result is None

    def test_wrappers_dir_missing(self, tmp_path):
        missing = tmp_path / "does-not-exist"

        with patch("overcode.wrapper._wrappers_dir", return_value=missing):
            result = resolve_wrapper("anything")
        assert result is None


class TestResolveWrapperEdgeCases:
    """Edge cases."""

    def test_empty_string(self):
        assert resolve_wrapper("") is None

    def test_whitespace_only(self):
        assert resolve_wrapper("   ") is None

    def test_whitespace_stripped(self, tmp_path):
        script = tmp_path / "wrap.sh"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        result = resolve_wrapper(f"  {script}  ")
        assert result == str(script)

    def test_none_handled(self):
        # resolve_wrapper requires str, but let's be safe
        assert resolve_wrapper(None) is None  # type: ignore[arg-type]


class TestListAvailableWrappers:
    """Test listing wrappers in ~/.overcode/wrappers/."""

    def test_lists_executables(self, tmp_path):
        wrappers = tmp_path / "wrappers"
        wrappers.mkdir(parents=True)

        for name in ["alpha.sh", "beta.py", "gamma"]:
            f = wrappers / name
            f.write_text("#!/bin/bash")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)

        # Also create a non-executable file (should be excluded)
        (wrappers / "not-exec.sh").write_text("#!/bin/bash")

        with patch("overcode.wrapper._wrappers_dir", return_value=wrappers):
            result = list_available_wrappers()

        names = [name for name, _ in result]
        assert "alpha" in names
        assert "beta" in names
        assert "gamma" in names
        assert "not-exec" not in names

    def test_empty_dir(self, tmp_path):
        wrappers = tmp_path / "wrappers"
        wrappers.mkdir(parents=True)

        with patch("overcode.wrapper._wrappers_dir", return_value=wrappers):
            result = list_available_wrappers()
        assert result == []

    def test_missing_dir(self, tmp_path):
        with patch("overcode.wrapper._wrappers_dir", return_value=tmp_path / "nope"):
            result = list_available_wrappers()
        assert result == []


class TestWrappersDir:
    """Test _wrappers_dir respects OVERCODE_DIR."""

    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("OVERCODE_DIR", raising=False)
        result = _wrappers_dir()
        assert result == Path.home() / ".overcode" / "wrappers"

    def test_overcode_dir_override(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_DIR", "/custom/overcode")
        result = _wrappers_dir()
        assert result == Path("/custom/overcode/wrappers")
