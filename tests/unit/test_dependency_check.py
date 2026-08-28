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
    check_agent_cli,
    require_tmux,
    require_claude,
    require_agent_cli,
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
            with patch("overcode.dependency_check._find_in_fallback_dirs", return_value=None):
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
            with patch("overcode.dependency_check._find_in_fallback_dirs", return_value=None):
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
            with patch("overcode.dependency_check._find_in_fallback_dirs", return_value=None):
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
            with patch("overcode.dependency_check._find_in_fallback_dirs", return_value=None):
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
            with patch("overcode.dependency_check._find_in_fallback_dirs", return_value=None):
                with pytest.raises(ClaudeNotFoundError) as exc_info:
                    require_claude()
                assert "Claude Code CLI is required but not found" in str(exc_info.value)


class TestCheckAgentCliRespectsOverride:
    """check_agent_cli() probes resolved.executable(), not the bare binary name.

    A backend's launch-time override env var (CLAUDE_COMMAND/OPENCODE_COMMAND/
    CODEX_COMMAND/GROK_COMMAND) is what actually gets exec'd at launch, so the
    pre-flight check must validate *that*, not just whether the real binary
    happens to be on PATH.
    """

    def test_no_override_probes_the_bare_binary_name(self, monkeypatch):
        from overcode.backends import get_backend
        monkeypatch.delenv("CODEX_COMMAND", raising=False)
        backend = get_backend("codex")

        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/opt/homebrew/bin/codex"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="codex-cli 0.150.1")
                available, path, version = check_agent_cli(backend)

        mock_which.assert_called_with("codex")
        assert available is True
        assert path == "/opt/homebrew/bin/codex"

    def test_override_is_probed_instead_of_the_binary_name(self, monkeypatch):
        from overcode.backends import get_backend
        monkeypatch.setenv("CODEX_COMMAND", "/tmp/mock_codex.py")
        backend = get_backend("codex")

        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/tmp/mock_codex.py"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="mock-codex 1.0")
                available, path, version = check_agent_cli(backend)

        mock_which.assert_called_with("/tmp/mock_codex.py")
        assert available is True
        assert path == "/tmp/mock_codex.py"

    def test_bad_override_is_reported_unavailable_even_if_real_binary_exists(self, monkeypatch):
        """A CODEX_COMMAND pointing at nothing must not silently fall back
        to checking whether the real `codex` happens to be on PATH — that
        would defeat the point of validating the override at all."""
        from overcode.backends import get_backend
        monkeypatch.setenv("CODEX_COMMAND", "/nonexistent/mock_codex")
        backend = get_backend("codex")

        def which_side_effect(name, *args, **kwargs):
            if name == "codex":
                return "/opt/homebrew/bin/codex"  # the real binary IS on PATH
            return None

        with patch("shutil.which", side_effect=which_side_effect):
            available, path, version = check_agent_cli(backend)

        assert available is False
        assert path is None
        assert version is None

    def test_respect_override_false_ignores_the_override(self, monkeypatch):
        """installed_version()-style doctor probes must always target the
        real binary, never a dev/test override — respect_override=False is
        how they opt out of the new default."""
        from overcode.backends import get_backend
        monkeypatch.setenv("CODEX_COMMAND", "/nonexistent/mock_codex")
        backend = get_backend("codex")

        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/opt/homebrew/bin/codex"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="codex-cli 0.150.1")
                available, path, version = check_agent_cli(backend, respect_override=False)

        mock_which.assert_called_with("codex")
        assert available is True
        assert path == "/opt/homebrew/bin/codex"

    def test_require_agent_cli_raises_when_override_points_nowhere(self, monkeypatch):
        from overcode.backends import get_backend
        from overcode.exceptions import AgentCliNotFoundError

        monkeypatch.setenv("CODEX_COMMAND", "/nonexistent/mock_codex")
        backend = get_backend("codex")

        with patch("shutil.which") as mock_which, \
             patch("overcode.dependency_check._find_in_fallback_dirs", return_value=None):
            mock_which.return_value = None
            with pytest.raises(AgentCliNotFoundError):
                require_agent_cli(backend)

    def test_claude_command_override_is_respected_too(self, monkeypatch):
        """Sanity-check that the fix isn't codex-specific."""
        from overcode.backends import get_backend
        monkeypatch.setenv("CLAUDE_COMMAND", "/tmp/mock_claude.py")
        backend = get_backend("claude-code")

        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/tmp/mock_claude.py"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="mock-claude 1.0")
                available, path, version = check_agent_cli(backend)

        mock_which.assert_called_with("/tmp/mock_claude.py")
        assert available is True


