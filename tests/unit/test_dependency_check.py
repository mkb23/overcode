"""
Tests for dependency checking and graceful degradation.

Tests the dependency_check module which provides utilities
for checking and handling missing external dependencies.
"""

import pytest
from unittest.mock import patch, MagicMock

from overcode.dependency_check import (
    find_executable,
    check_tmux,
    check_claude,
    require_tmux,
    require_claude,
)
from overcode.exceptions import TmuxNotFoundError, ClaudeNotFoundError


class TestFindExecutable:
    """Tests for find_executable."""

    def test_finds_existing_executable(self):
        """Should find an executable that exists."""
        # 'python' or 'python3' should exist
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/python"
            result = find_executable("python")
            assert result == "/usr/bin/python"

    def test_returns_none_for_missing(self):
        """Should return None for missing executable."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            result = find_executable("nonexistent_binary_xyz")
            assert result is None


class TestCheckTmux:
    """Tests for check_tmux."""

    def test_tmux_available(self):
        """Should return True when tmux is available."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/tmux"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="tmux 3.4"
                )
                available, path, version = check_tmux()
                assert available is True
                assert path == "/usr/bin/tmux"
                assert version == "tmux 3.4"

    def test_tmux_not_found(self):
        """Should return False when tmux is not found."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            available, path, version = check_tmux()
            assert available is False
            assert path is None
            assert version is None

    def test_tmux_version_fails(self):
        """Should return True but no version if version check fails."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/tmux"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stdout=""
                )
                available, path, version = check_tmux()
                assert available is True
                assert path == "/usr/bin/tmux"
                assert version is None


class TestCheckClaude:
    """Tests for check_claude."""

    def test_claude_available(self):
        """Should return True when claude is available."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/claude"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Claude Code v2.0.75"
                )
                available, path, version = check_claude()
                assert available is True
                assert path == "/usr/local/bin/claude"
                assert version == "Claude Code v2.0.75"

    def test_claude_not_found(self):
        """Should return False when claude is not found."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            available, path, version = check_claude()
            assert available is False
            assert path is None
            assert version is None


class TestRequireTmux:
    """Tests for require_tmux."""

    def test_returns_path_when_available(self):
        """Should return path when tmux is available."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/tmux"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="tmux 3.4")
                path = require_tmux()
                assert path == "/usr/bin/tmux"

    def test_raises_when_not_found(self):
        """Should raise TmuxNotFoundError when tmux missing."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            with pytest.raises(TmuxNotFoundError) as exc_info:
                require_tmux()
            assert "tmux is required but not found" in str(exc_info.value)


class TestRequireClaude:
    """Tests for require_claude."""

    def test_returns_path_when_available(self):
        """Should return path when claude is available."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/local/bin/claude"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="Claude Code v2.0.75")
                path = require_claude()
                assert path == "/usr/local/bin/claude"

    def test_raises_when_not_found(self):
        """Should raise ClaudeNotFoundError when claude missing."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            with pytest.raises(ClaudeNotFoundError) as exc_info:
                require_claude()
            assert "Claude Code CLI is required but not found" in str(exc_info.value)


