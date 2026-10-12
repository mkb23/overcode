"""TmuxManager: one tmux session's facade over a tmux client.

The behaviour is tested against ``MockTmux`` (what the session ends up
holding), not against libtmux call shapes; RealTmux's own libtmux use is
tested in test_implementations, and its timeouts in test_daemon_robustness.
"""

from unittest.mock import patch

import pytest

from overcode.implementations import TMUX_COMMAND_TIMEOUT_SECONDS, RealTmux
from overcode.protocols import TmuxTimeoutError
from overcode.tmux_manager import TmuxManager, ssh_proxy_command
from tests.tmux_doubles import MockTmux


@pytest.fixture
def tmux():
    return MockTmux()


@pytest.fixture
def manager(tmux):
    return TmuxManager("agents", tmux=tmux)


class TestDefaultClient:
    def test_is_a_bounded_uncached_real_tmux(self):
        client = TmuxManager("agents", socket="oc-sock").client
        assert isinstance(client, RealTmux)
        assert client._command_timeout == TMUX_COMMAND_TIMEOUT_SECONDS
        # it creates and kills windows, so it never reuses a stale object
        assert client._cache_ttl == 0
        assert client._socket_name == "oc-sock"

    def test_socket_defaults_to_the_environment(self, monkeypatch):
        monkeypatch.setenv("OVERCODE_TMUX_SOCKET", "env-socket")
        manager = TmuxManager("agents")
        assert manager.socket == "env-socket"
        assert manager.client._socket_name == "env-socket"


class TestSession:
    def test_exists_only_once_created(self, manager):
        assert manager.session_exists() is False
        assert manager.ensure_session() is True
        assert manager.session_exists() is True

    def test_ensure_session_keeps_an_existing_session(self, manager, tmux):
        tmux.new_session("agents")
        tmux.set_pane_content("agents", "w", "hello")
        assert manager.ensure_session() is True
        assert tmux.capture_pane("agents", "w") == "hello"

    def test_kill_session(self, manager, tmux):
        manager.ensure_session()
        assert manager.kill_session() is True
        assert not tmux.has_session("agents")
        assert manager.kill_session() is False

    def test_managers_of_different_sessions_do_not_mix(self, tmux):
        one = TmuxManager("one", tmux=tmux)
        two = TmuxManager("two", tmux=tmux)
        one.create_window("w1")
        two.create_window("w2")
        assert [w["name"] for w in one.list_windows()] == ["w1"]
        assert [w["name"] for w in two.list_windows()] == ["w2"]


class TestWindows:
    def test_create_window_makes_the_session_and_a_ready_shell(self, manager, tmux):
        assert manager.create_window("agent-1", start_directory="/tmp/x") == "agent-1"
        assert tmux.has_session("agents")
        created = tmux.windows_created[-1]
        assert created["cwd"] == "/tmp/x"
        # the launch command is typed next: the shell must be at its prompt
        assert created["wait_for_prompt"] is True
        assert created["command"] is None

    def test_list_windows_in_creation_order(self, manager):
        for name in ("a", "b", "c"):
            manager.create_window(name)
        assert [(w["index"], w["name"]) for w in manager.list_windows()] == [
            (0, "a"), (1, "b"), (2, "c")]

    def test_no_session_lists_no_windows(self, manager):
        assert manager.list_windows() == []
        assert manager.window_exists("anything") is False

    def test_window_exists_by_name_or_legacy_index(self, manager):
        manager.create_window("shell")
        manager.create_window("agent-1")
        assert manager.window_exists("agent-1") is True
        assert manager.window_exists("agent") is False  # never a prefix match
        # pre-name-based sessions stored the window index as a digit string
        assert manager.window_exists("1") is True
        assert manager.window_exists("7") is False

    def test_kill_window(self, manager):
        manager.create_window("doomed")
        assert manager.kill_window("doomed") is True
        assert manager.window_exists("doomed") is False
        assert manager.kill_window("doomed") is False

    def test_kill_windows_by_predicate(self, manager):
        for name in ("ssh:a:x", "agent-1", "ssh:b:y"):
            manager.create_window(name)
        killed = manager.kill_windows(lambda n: n.startswith("ssh:"))
        assert sorted(killed) == ["ssh:a:x", "ssh:b:y"]
        assert [w["name"] for w in manager.list_windows()] == ["agent-1"]

    def test_rename_window_keeps_its_content(self, manager, tmux):
        manager.create_window("old")
        tmux.set_pane_content("agents", "old", "$ claude")
        assert manager.rename_window("old", "new") is True
        assert manager.capture_pane("new") == "$ claude"
        assert manager.rename_window("gone", "x") is False

    def test_capture_pane_last_lines(self, manager, tmux):
        tmux.set_pane_content("agents", "w", "1\n2\n3")
        assert manager.capture_pane("w", lines=2) == "2\n3"
        assert manager.capture_pane("missing") is None


class TestSshProxyWindow:
    def test_creates_a_tagged_window_running_the_proxy(self, manager, tmux):
        name = manager.create_ssh_proxy_window(
            "ssh:desk:agent", "me@desk", "agents", "agent")

        assert name == "ssh:desk:agent"
        assert tmux.has_session("agents")
        created = tmux.windows_created[-1]
        # tagged so scrollback keys go to the remote tmux
        assert created["options"] == {"@is_ssh_proxy": "on"}
        assert created["command"] == [ssh_proxy_command("me@desk", "agents", "agent")]
        assert created["wait_for_prompt"] is False

    def test_proxy_attaches_a_private_linked_session(self):
        cmd = ssh_proxy_command("me@desk", "agents", "agent")
        assert cmd.startswith("ssh -t ")
        assert "me@desk" in cmd
        assert "grep -m1 ^agent" in cmd
        assert "tmux new-session -d -s $LS -t agents" in cmd
        assert 'client-detached "kill-session -t $LS"' in cmd
        assert cmd.endswith("tmux attach-session -t $LS'")

    def test_target_is_shell_quoted(self):
        assert "'me@desk; rm -rf /'" in ssh_proxy_command("me@desk; rm -rf /", "agents", "a")


class TestKeys:
    def test_send_keys_to_the_bound_session(self, manager, tmux):
        manager.create_window("w")
        assert manager.send_keys("w", "echo hello") is True
        assert manager.send_keys("w", "partial", enter=False) is True
        assert tmux.sent_keys == [
            ("agents", "w", "echo hello", True),
            ("agents", "w", "partial", False),
        ]


class TestUnresponsiveServer:
    """A wedged server is never read as "gone"."""

    def test_reads_raise_instead_of_answering_gone(self, manager, tmux):
        manager.create_window("w")
        tmux.unresponsive = True
        with pytest.raises(TmuxTimeoutError):
            manager.window_exists("w")
        with pytest.raises(TmuxTimeoutError):
            manager.session_exists()
        with pytest.raises(TmuxTimeoutError):
            manager.capture_pane("w")
        with pytest.raises(TmuxTimeoutError):
            manager.get_pane_pid("w")


class TestAttach:
    @pytest.mark.parametrize("window, socket, argv", [
        (None, None, ["tmux", "attach-session", "-t", "agents"]),
        ("my-window", None, ["tmux", "attach-session", "-t", "agents:=my-window"]),
        (None, "mysock", ["tmux", "-L", "mysock", "attach-session", "-t", "agents"]),
    ])
    def test_attach_replaces_this_process_with_tmux(self, monkeypatch, window, socket, argv):
        if socket:
            monkeypatch.setenv("OVERCODE_TMUX_SOCKET", socket)
        else:
            monkeypatch.delenv("OVERCODE_TMUX_SOCKET", raising=False)
        with patch("overcode.implementations.os.execvp") as execvp:
            TmuxManager("agents").attach_session(window=window)
        execvp.assert_called_once_with("tmux", argv)

    def test_bare_attach_uses_the_managers_socket(self):
        with patch("overcode.tmux_utils.attach_bare") as attach_bare:
            TmuxManager("agents", socket="oc-sock").attach_session(window="w", bare=True)
        attach_bare.assert_called_once_with("agents", "w", socket_path="oc-sock")
