"""
Unit tests for ClaudeLauncher wrapper support.

Tests that wrappers are correctly resolved, stored in session metadata,
and prepended to the command sent to tmux.
"""

import os
import stat
import pytest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.launcher import ClaudeLauncher
from overcode.tmux_manager import TmuxManager
from overcode.session_manager import SessionManager
from overcode.interfaces import MockTmux


@pytest.fixture(autouse=True)
def mock_dependency_checks():
    """Mock dependency checks and strip OVERCODE_* env vars."""
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_claude"), \
         patch.dict(os.environ, {}, clear=False) as patched_env:
        for key in ["OVERCODE_SESSION_NAME", "OVERCODE_TMUX_SESSION",
                     "OVERCODE_PARENT_SESSION_ID", "OVERCODE_PARENT_NAME"]:
            patched_env.pop(key, None)
        yield


def _make_launcher(tmp_path):
    mock_tmux = MockTmux()
    tmux_manager = TmuxManager("agents", tmux=mock_tmux)
    session_manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
    launcher = ClaudeLauncher(
        tmux_session="agents",
        tmux_manager=tmux_manager,
        session_manager=session_manager,
    )
    return launcher, mock_tmux


def _make_wrapper(tmp_path, name="wrapper.sh", content="#!/bin/bash\nexec \"$@\""):
    script = tmp_path / name
    script.write_text(content)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


class TestLaunchWithWrapper:
    """Test launching agents with a wrapper script."""

    def test_wrapper_prepended_to_command(self, tmp_path):
        """Wrapper path is prepended to the claude command in tmux."""
        launcher, mock_tmux = _make_launcher(tmp_path)
        wrapper_path = _make_wrapper(tmp_path)

        session = launcher.launch(name="wrapped-agent", wrapper=wrapper_path)

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        # Find the command that was sent to tmux
        cmd = next(c for c in sent_commands if "claude" in c)
        assert wrapper_path in cmd
        # Wrapper should appear before 'claude'
        assert cmd.index(wrapper_path) < cmd.index("claude")

    def test_wrapper_dir_env_set(self, tmp_path):
        """OVERCODE_WRAPPER_DIR is set in the env prefix when wrapper is active."""
        launcher, mock_tmux = _make_launcher(tmp_path)
        wrapper_path = _make_wrapper(tmp_path)

        session = launcher.launch(
            name="wrapped-agent",
            start_directory="/some/project",
            wrapper=wrapper_path,
        )

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        cmd = next(c for c in sent_commands if "claude" in c)
        assert "OVERCODE_WRAPPER_DIR=" in cmd

    def test_no_wrapper_dir_without_wrapper(self, tmp_path):
        """OVERCODE_WRAPPER_DIR is NOT set when no wrapper is used."""
        launcher, mock_tmux = _make_launcher(tmp_path)

        session = launcher.launch(name="plain-agent")

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        cmd = next(c for c in sent_commands if "claude" in c)
        assert "OVERCODE_WRAPPER_DIR" not in cmd

    def test_wrapper_stored_in_session(self, tmp_path):
        """Wrapper path is persisted in the session metadata."""
        launcher, _ = _make_launcher(tmp_path)
        wrapper_path = _make_wrapper(tmp_path)

        session = launcher.launch(name="wrapped-agent", wrapper=wrapper_path)

        assert session is not None
        assert session.wrapper == wrapper_path

    def test_no_wrapper_field_is_none(self, tmp_path):
        """Session.wrapper is None when no wrapper is used."""
        launcher, _ = _make_launcher(tmp_path)

        session = launcher.launch(name="plain-agent")

        assert session is not None
        assert session.wrapper is None

    def test_invalid_wrapper_returns_none(self, tmp_path):
        """Launch fails gracefully when wrapper doesn't exist."""
        launcher, _ = _make_launcher(tmp_path)

        session = launcher.launch(name="bad-wrapper", wrapper="/nonexistent/wrapper.sh")

        assert session is None

    def test_non_executable_wrapper_returns_none(self, tmp_path):
        """Launch fails when wrapper exists but isn't executable."""
        launcher, _ = _make_launcher(tmp_path)
        script = tmp_path / "noexec.sh"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        # Don't make it executable

        session = launcher.launch(name="bad-wrapper", wrapper=str(script))

        assert session is None

    def test_wrapper_with_permissions(self, tmp_path):
        """Wrapper works with skip_permissions flag."""
        launcher, mock_tmux = _make_launcher(tmp_path)
        wrapper_path = _make_wrapper(tmp_path)

        session = launcher.launch(
            name="perms-agent",
            skip_permissions=True,
            wrapper=wrapper_path,
        )

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        cmd = next(c for c in sent_commands if "claude" in c)
        # Both wrapper and permissions should be present
        assert wrapper_path in cmd
        assert "--permission-mode" in cmd

    def test_wrapper_with_model(self, tmp_path):
        """Wrapper works alongside model selection."""
        launcher, mock_tmux = _make_launcher(tmp_path)
        wrapper_path = _make_wrapper(tmp_path)

        session = launcher.launch(
            name="model-agent",
            model="sonnet",
            wrapper=wrapper_path,
        )

        assert session is not None
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        cmd = next(c for c in sent_commands if "claude" in c)
        assert wrapper_path in cmd
        assert "--model sonnet" in cmd


class TestWrapperBareName:
    """Test wrapper resolution from ~/.overcode/wrappers/ via bare name."""

    def test_bare_name_resolved(self, tmp_path):
        """A bare name like 'devcontainer' resolves from the wrappers dir."""
        wrappers_dir = tmp_path / "global_wrappers"
        wrappers_dir.mkdir()
        script = wrappers_dir / "devcontainer.sh"
        script.write_text("#!/bin/bash\nexec \"$@\"")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        launcher, mock_tmux = _make_launcher(tmp_path)

        with patch("overcode.wrapper._wrappers_dir", return_value=wrappers_dir):
            session = launcher.launch(name="dc-agent", wrapper="devcontainer")

        assert session is not None
        assert session.wrapper == str(script)
        sent_commands = [k[2] for k in mock_tmux.sent_keys]
        cmd = next(c for c in sent_commands if "claude" in c)
        assert str(script) in cmd


class TestSessionWrapperPersistence:
    """Test that wrapper field round-trips through session serialization."""

    def test_wrapper_serialization(self, tmp_path):
        """Wrapper field survives to_dict/from_dict round-trip."""
        from overcode.session_manager import Session
        from datetime import datetime

        session = Session(
            id="test-id",
            name="test",
            tmux_session="agents",
            tmux_window="test-1234",
            command=["claude"],
            start_directory="/tmp",
            start_time=datetime.now().isoformat(),
            wrapper="/path/to/wrapper.sh",
        )

        data = session.to_dict()
        restored = Session.from_dict(data)

        assert restored is not None
        assert restored.wrapper == "/path/to/wrapper.sh"

    def test_wrapper_none_serialization(self, tmp_path):
        """Session without wrapper round-trips correctly."""
        from overcode.session_manager import Session
        from datetime import datetime

        session = Session(
            id="test-id",
            name="test",
            tmux_session="agents",
            tmux_window="test-1234",
            command=["claude"],
            start_directory="/tmp",
            start_time=datetime.now().isoformat(),
        )

        data = session.to_dict()
        restored = Session.from_dict(data)

        assert restored is not None
        assert restored.wrapper is None

    def test_backward_compat_no_wrapper_field(self, tmp_path):
        """Sessions serialized before wrapper was added still load fine."""
        from overcode.session_manager import Session

        old_data = {
            "id": "old-id",
            "name": "old-agent",
            "tmux_session": "agents",
            "tmux_window": "old-1234",
            "command": ["claude"],
            "start_directory": "/tmp",
            "start_time": "2025-01-01T00:00:00",
        }

        session = Session.from_dict(old_data)

        assert session is not None
        assert session.wrapper is None
