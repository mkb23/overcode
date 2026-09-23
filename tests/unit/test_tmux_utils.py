"""Tests for tmux_utils module."""

import os
import pytest
from unittest.mock import patch, MagicMock, call
import subprocess

from overcode.tmux_utils import (
    DAEMON_CLAUDE_WINDOW_NAME,
    EMPTY_PLACEHOLDER_WINDOW,
    SSH_PROXY_WINDOW_PREFIX,
    is_overcode_owned_window,
    send_text_to_tmux_window,
    get_tmux_pane_content,
    exit_copy_mode_if_active,
    untracked_window_names,
)


class TestSendTextToTmuxWindow:
    """Tests for send_text_to_tmux_window."""

    def test_sends_single_line_text(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0")
            result = send_text_to_tmux_window("agents", 1, "hello world")

        assert result is True
        # 4 calls: copy-mode probe, load-buffer, paste-buffer, send-keys (Enter)
        assert mock_run.call_count == 4

    def test_sends_without_enter(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0")
            result = send_text_to_tmux_window("agents", 1, "hello", send_enter=False)

        assert result is True
        # 3 calls: copy-mode probe, load-buffer, paste-buffer (no send-keys)
        assert mock_run.call_count == 3

    def test_handles_multiline_text_batching(self):
        # Create text with more than 10 lines to trigger batching
        lines = [f"line {i}" for i in range(15)]
        text = "\n".join(lines)

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0")
            with patch("overcode.tmux_utils.time.sleep"):
                result = send_text_to_tmux_window("agents", 1, text)

        assert result is True
        # copy-mode probe + 2 batches * 2 calls (load-buffer + paste-buffer) + 1 send-keys = 6
        assert mock_run.call_count == 6

    def test_returns_false_on_load_buffer_failure(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("tmux not running")
            result = send_text_to_tmux_window("agents", 1, "hello")

        assert result is False

    def test_returns_false_on_send_keys_failure(self):
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return MagicMock(returncode=0)
            raise subprocess.SubprocessError("send-keys failed")

        with patch("overcode.tmux_utils.subprocess.run", side_effect=side_effect):
            result = send_text_to_tmux_window("agents", 1, "hello")

        assert result is False

    def test_uses_custom_socket_from_env(self):
        with patch.dict(os.environ, {"OVERCODE_TMUX_SOCKET": "test-socket"}):
            with patch("overcode.tmux_utils.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                send_text_to_tmux_window("agents", 1, "hello")

            # First call should include -L test-socket
            first_call_args = mock_run.call_args_list[0][0][0]
            assert "-L" in first_call_args
            assert "test-socket" in first_call_args

    def test_no_socket_in_env(self):
        with patch.dict(os.environ, {}, clear=True):
            # Make sure OVERCODE_TMUX_SOCKET isn't set
            os.environ.pop("OVERCODE_TMUX_SOCKET", None)
            with patch("overcode.tmux_utils.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                send_text_to_tmux_window("agents", 1, "hello")

            first_call_args = mock_run.call_args_list[0][0][0]
            assert "-L" not in first_call_args

    def test_startup_delay(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("overcode.tmux_utils.time.sleep") as mock_sleep:
                send_text_to_tmux_window("agents", 1, "hello", startup_delay=2.0)

            # First sleep call should be the startup delay
            mock_sleep.assert_any_call(2.0)

    def test_tempfile_cleanup_on_failure(self):
        """Temp files should be cleaned up even on failure."""
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.SubprocessError("fail")
            with patch("overcode.tmux_utils.os.unlink") as mock_unlink:
                send_text_to_tmux_window("agents", 1, "hello")
                # unlink should have been called to clean up tempfile
                assert mock_unlink.called


class TestExitCopyModeIfActive:
    """Tests for exit_copy_mode_if_active (#401)."""

    def test_sends_cancel_when_pane_in_copy_mode(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1\n")
            exit_copy_mode_if_active("agents", "w1")

        # Two calls: display-message probe, then send-keys -X cancel
        assert mock_run.call_count == 2
        second_args = mock_run.call_args_list[1][0][0]
        assert "-X" in second_args and "cancel" in second_args

    def test_noop_when_not_in_copy_mode(self):
        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="0")
            exit_copy_mode_if_active("agents", "w1")

        # Only the display-message probe; no cancel
        assert mock_run.call_count == 1

    def test_swallows_errors(self):
        """Probe failures must never crash the caller — heartbeats still run."""
        with patch("overcode.tmux_utils.subprocess.run",
                   side_effect=subprocess.SubprocessError("tmux gone")):
            exit_copy_mode_if_active("agents", "w1")  # no exception


class TestGetTmuxPaneContent:
    """Tests for get_tmux_pane_content."""

    def test_returns_content_on_success(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "line 1\nline 2\n"

        with patch("overcode.tmux_utils.subprocess.run", return_value=result):
            content = get_tmux_pane_content("agents", 1, lines=50)

        assert content == "line 1\nline 2"

    def test_returns_none_on_nonzero_exit(self):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""

        with patch("overcode.tmux_utils.subprocess.run", return_value=result):
            content = get_tmux_pane_content("agents", 1)

        assert content is None

    def test_returns_none_on_exception(self):
        with patch("overcode.tmux_utils.subprocess.run",
                   side_effect=subprocess.SubprocessError("tmux gone")):
            content = get_tmux_pane_content("agents", 1)

        assert content is None

    def test_uses_custom_socket(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "content\n"

        with patch.dict(os.environ, {"OVERCODE_TMUX_SOCKET": "my-socket"}):
            with patch("overcode.tmux_utils.subprocess.run", return_value=result) as mock_run:
                get_tmux_pane_content("agents", 1)

            cmd = mock_run.call_args[0][0]
            assert "-L" in cmd
            assert "my-socket" in cmd

    def test_custom_line_count(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "content\n"

        with patch("overcode.tmux_utils.subprocess.run", return_value=result) as mock_run:
            get_tmux_pane_content("agents", 1, lines=100)

        cmd = mock_run.call_args[0][0]
        assert "-100" in cmd


class TestUntrackedWindowNames:
    """The one predicate behind the daemon's untracked count and cleanup --untracked (#344)."""

    def test_window_zero_is_never_untracked(self):
        assert untracked_window_names([{"index": 0, "name": "bash"}], set()) == []
        assert untracked_window_names([{"index": "0", "name": "bash"}], set()) == []

    def test_tracked_windows_are_skipped(self):
        windows = [{"index": 1, "name": "agent-a"}, {"index": 2, "name": "agent-b"}]
        assert untracked_window_names(windows, {"agent-a", "agent-b"}) == []
        assert untracked_window_names(windows, {"agent-a"}) == ["agent-b"]

    def test_overcode_owned_windows_are_skipped(self):
        """Placeholder (#457), supervisor claude and SSH proxies are overcode's own."""
        windows = [
            {"index": 0, "name": "bash"},
            {"index": 1, "name": EMPTY_PLACEHOLDER_WINDOW},
            {"index": 2, "name": DAEMON_CLAUDE_WINDOW_NAME},
            {"index": 3, "name": f"{SSH_PROXY_WINDOW_PREFIX}desktop:remote-agent"},
            {"index": 4, "name": "rogue"},
        ]
        assert untracked_window_names(windows, set()) == ["rogue"]

    def test_index_may_be_int_or_str_and_order_is_kept(self):
        windows = [
            {"index": "3", "name": "c"},
            {"index": 1, "name": "a"},
            {"index": "2", "name": "b"},
        ]
        assert untracked_window_names(windows, set()) == ["c", "a", "b"]

    def test_is_overcode_owned_window(self):
        assert is_overcode_owned_window(EMPTY_PLACEHOLDER_WINDOW)
        assert is_overcode_owned_window(DAEMON_CLAUDE_WINDOW_NAME)
        assert is_overcode_owned_window(f"{SSH_PROXY_WINDOW_PREFIX}host:name")
        assert not is_overcode_owned_window("my-agent-1a2b")
        assert not is_overcode_owned_window("oc-empty2")

    def test_names_are_the_ones_other_modules_use(self):
        """The constants must match what creates the windows, or the skip is dead."""
        from overcode.supervisor_daemon import SupervisorDaemon
        from overcode import tmux_manager

        assert SupervisorDaemon.DAEMON_CLAUDE_WINDOW_NAME == DAEMON_CLAUDE_WINDOW_NAME
        assert tmux_manager.EMPTY_PLACEHOLDER_WINDOW == EMPTY_PLACEHOLDER_WINDOW


# ── one list-panes for a whole session (audit R7 / R11) ─────────────────


class TestParsePaneListing:
    """``parse_pane_listing`` over ``list-panes -s -F PANE_LISTING_FORMAT`` rows."""

    ROW = "agent-01\t1\t4242\t1790136432\t8\t28\t4\tclaude\t1"

    def test_fields(self):
        from overcode.tmux_utils import parse_pane_listing

        panes = parse_pane_listing([self.ROW])
        info = panes["agent-01"]
        assert (info.window_name, info.window_index, info.pane_pid) == ("agent-01", 1, 4242)
        assert (info.activity, info.history_size, info.cursor_x, info.cursor_y) == (
            1790136432, 8, 28, 4,
        )
        assert info.current_command == "claude" and info.session_attached == 1
        assert info.signature == (1790136432, 8, 28, 4, "claude")

    def test_session_attached_is_not_part_of_the_signature(self):
        """Attaching a client changes no pane, so it must not read as a change."""
        from overcode.tmux_utils import parse_pane_listing

        a = parse_pane_listing([self.ROW])["agent-01"]
        b = parse_pane_listing([self.ROW.rsplit("\t", 1)[0] + "\t3"])["agent-01"]
        assert a.signature == b.signature and a.session_attached != b.session_attached

    def test_first_pane_per_window_wins(self):
        """A split window lists a row per pane; RealTmux addresses ``panes[0]``."""
        from overcode.tmux_utils import parse_pane_listing

        rows = [
            "w2\t2\t100\t10\t0\t0\t3\tbash\t0",
            "w2\t2\t200\t10\t0\t0\t0\tsleep\t0",
            "w3\t3\t300\t10\t0\t0\t0\tzsh\t0",
        ]
        panes = parse_pane_listing(rows)
        assert [p.pane_pid for p in panes.values()] == [100, 300]
        assert panes["w2"].current_command == "bash"

    def test_window_name_with_a_tab_and_malformed_rows(self):
        from overcode.tmux_utils import parse_pane_listing

        rows = [
            "odd\tname\t4\t400\t10\t0\t0\t0\tzsh\t0",  # a tab inside the name
            "short\t1\t2",  # too few fields
            "bad\t1\tnot-a-pid\t10\t0\t0\t0\tzsh\t0",  # unparsable pid
            "",
        ]
        panes = parse_pane_listing(rows)
        assert list(panes) == ["odd\tname"]
        assert panes["odd\tname"].window_index == 4

    def test_format_has_one_field_per_parsed_column(self):
        from overcode.tmux_utils import PANE_LISTING_FORMAT

        fields = PANE_LISTING_FORMAT.split("\t")
        assert fields == [
            "#{window_name}", "#{window_index}", "#{pane_pid}", "#{window_activity}",
            "#{history_size}", "#{cursor_x}", "#{cursor_y}", "#{pane_current_command}",
            "#{session_attached}",
        ]


class TestTuiPaneTarget:
    def test_pane_id_from_the_tmux_environment(self):
        from overcode.tmux_utils import tui_pane_target

        assert tui_pane_target({"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%7"}) == "%7"

    def test_none_outside_tmux_or_without_a_pane_id(self):
        from overcode.tmux_utils import tui_pane_target

        assert tui_pane_target({}) is None
        assert tui_pane_target({"TMUX_PANE": "%7"}) is None  # TMUX unset: not inside tmux
        assert tui_pane_target({"TMUX": "x", "TMUX_PANE": "7"}) is None

    def test_own_socket_is_the_first_field_of_tmux(self):
        from overcode.tmux_utils import tui_tmux_socket

        assert tui_tmux_socket({"TMUX": "/private/tmp/tmux-1/default,24475,0"}) == (
            "/private/tmp/tmux-1/default"
        )
        assert tui_tmux_socket({}) is None
        assert tui_tmux_socket({"TMUX": ""}) is None


class TestTmuxCmdTargetsOwnServer:
    """Does ``-L $OVERCODE_TMUX_SOCKET`` (or a bare tmux) reach this pane's server?"""

    def test_bare_tmux_follows_the_tmux_variable(self):
        from overcode.tmux_utils import tmux_cmd_targets_own_server

        assert tmux_cmd_targets_own_server({"TMUX": "/tmp/tmux-1/default,1,0"}) is True
        assert tmux_cmd_targets_own_server({}) is False  # no own server outside tmux

    def test_label_resolved_under_tmpdir_and_compared_by_real_path(self, tmp_path):
        from overcode.tmux_utils import tmux_cmd_targets_own_server

        uid = os.getuid()
        real = tmp_path / "real"
        (real / f"tmux-{uid}").mkdir(parents=True)
        link = tmp_path / "link"
        link.symlink_to(real)  # /tmp -> /private/tmp on macOS
        env = {
            "TMUX_TMPDIR": str(link),
            "OVERCODE_TMUX_SOCKET": "agents",
            "TMUX": f"{real}/tmux-{uid}/agents,1,0",
        }
        assert tmux_cmd_targets_own_server(env) is True
        env["OVERCODE_TMUX_SOCKET"] = "other"
        assert tmux_cmd_targets_own_server(env) is False
        env["TMUX_TMPDIR"] = str(tmp_path / "elsewhere")
        env["OVERCODE_TMUX_SOCKET"] = "agents"
        assert tmux_cmd_targets_own_server(env) is False

    def test_default_directory_is_tmp(self):
        from overcode.tmux_utils import tmux_cmd_targets_own_server

        uid = os.getuid()
        env = {"OVERCODE_TMUX_SOCKET": "sock", "TMUX": f"/tmp/tmux-{uid}/sock,1,0"}
        assert tmux_cmd_targets_own_server(env) is True
        env["TMUX"] = f"/tmp/tmux-{uid}/default,1,0"
        assert tmux_cmd_targets_own_server(env) is False


class TestQueryPaneAttended:
    """One display-message per call; None whenever tmux cannot answer."""

    def test_one_command_returns_session_and_attached_count(self):
        from overcode.tmux_utils import query_pane_attended

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="agents\t2\n")
            assert query_pane_attended("%3") == ("agents", 2)
        assert mock_run.call_count == 1
        cmd = mock_run.call_args.args[0]
        assert cmd[-5:] == ["display-message", "-p", "-t", "%3", "#{session_name}\t#{session_attached}"]

    def test_zero_clients_and_a_tab_in_the_session_name(self):
        from overcode.tmux_utils import query_pane_attended

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="odd\tname\t0\n")
            assert query_pane_attended("%3") == ("odd\tname", 0)

    def test_unresolvable_pane_is_none_not_zero(self):
        """tmux 3.5 prints an empty line with status 0 for a target it cannot find."""
        from overcode.tmux_utils import query_pane_attended

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="\n")
            assert query_pane_attended("%999") is None
            mock_run.return_value = MagicMock(returncode=0, stdout="agents\tnope\n")
            assert query_pane_attended("%3") is None

    def test_no_server_or_timeout_is_none(self):
        from overcode.tmux_utils import query_pane_attended

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error connecting")
            assert query_pane_attended("%3") is None
        with patch(
            "overcode.tmux_utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired("tmux", 2),
        ):
            assert query_pane_attended("%3") is None

    def test_respects_socket_env(self):
        from overcode.tmux_utils import query_pane_attended

        with patch.dict(os.environ, {"OVERCODE_TMUX_SOCKET": "sock"}):
            with patch("overcode.tmux_utils.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="agents\t1\n")
                query_pane_attended("%3")
        assert mock_run.call_args.args[0][:3] == ["tmux", "-L", "sock"]

    def test_socket_path_addresses_that_server_with_dash_s(self):
        """The TUI's pane id is only meaningful on its own server; with
        OVERCODE_TMUX_SOCKET set, -L would resolve it on another one."""
        from overcode.tmux_utils import query_pane_attended

        with patch.dict(os.environ, {"OVERCODE_TMUX_SOCKET": "sock"}):
            with patch("overcode.tmux_utils.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="work\t1\n")
                result = query_pane_attended("%3", socket_path="/private/tmp/tmux-1/default")
        assert result == ("work", 1)
        cmd = mock_run.call_args.args[0]
        assert cmd[:3] == ["tmux", "-S", "/private/tmp/tmux-1/default"]
        assert "-L" not in cmd


class TestListPanes:
    """``list_panes`` / ``list_pane_pids``: one subprocess, None when tmux cannot answer."""

    def test_one_list_panes_command_for_the_session(self):
        from overcode.tmux_utils import PANE_LISTING_FORMAT, list_panes

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="a\t1\t10\t5\t0\t0\t0\tclaude\t0\nb\t2\t20\t5\t0\t0\t0\tclaude\t0\n"
            )
            panes = list_panes("agents")
        assert mock_run.call_count == 1
        cmd = mock_run.call_args.args[0]
        assert cmd[-6:] == ["list-panes", "-s", "-t", "agents", "-F", PANE_LISTING_FORMAT]
        assert {n: p.pane_pid for n, p in panes.items()} == {"a": 10, "b": 20}

    def test_respects_socket_env(self):
        from overcode.tmux_utils import list_panes

        with patch.dict(os.environ, {"OVERCODE_TMUX_SOCKET": "sock"}):
            with patch("overcode.tmux_utils.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="")
                list_panes("agents")
        assert mock_run.call_args.args[0][:3] == ["tmux", "-L", "sock"]

    def test_missing_session_or_server_is_none(self):
        from overcode.tmux_utils import list_pane_pids, list_panes

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="can't find session")
            assert list_panes("agents") is None
            assert list_pane_pids("agents") is None
        with patch(
            "overcode.tmux_utils.subprocess.run",
            side_effect=subprocess.TimeoutExpired("tmux", 5),
        ):
            assert list_panes("agents") is None

    def test_pids_projection(self):
        from overcode.tmux_utils import list_pane_pids

        with patch("overcode.tmux_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="a\t1\t10\t5\t0\t0\t0\tclaude\t0\n"
            )
            assert list_pane_pids("agents") == {"a": 10}


class TestPaneForWindow:
    def _panes(self):
        from overcode.tmux_utils import parse_pane_listing

        return parse_pane_listing(
            ["bash\t0\t1\t5\t0\t0\t0\tzsh\t0", "agent-a\t3\t30\t5\t0\t0\t0\tclaude\t0"]
        )

    def test_by_name(self):
        from overcode.tmux_utils import pane_for_window

        assert pane_for_window(self._panes(), "agent-a").pane_pid == 30

    def test_legacy_digit_window_falls_back_to_the_index(self):
        """Pre-name-based sessions stored the window index; RealTmux._get_window
        tries the name first, then the index, and so does the listing lookup."""
        from overcode.tmux_utils import pane_for_window

        assert pane_for_window(self._panes(), "3").pane_pid == 30
        assert pane_for_window(self._panes(), "7") is None

    def test_a_name_that_is_not_a_digit_string_never_scans_indices(self):
        from overcode.tmux_utils import pane_for_window

        assert pane_for_window(self._panes(), "missing") is None
