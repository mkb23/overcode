"""Tests for sandbox_detect module."""

import subprocess
from unittest.mock import patch, MagicMock

from overcode import sandbox_detect
from overcode.sandbox_detect import (
    detect_sandbox_states,
    is_sandbox_enabled,
    _parse_loopback_counts,
    _run_lsof,
)


def _lsof_result(stdout: str, returncode: int = 0):
    return MagicMock(stdout=stdout, returncode=returncode)


class TestParseLoopbackCounts:
    def test_empty_input(self):
        assert _parse_loopback_counts("") == {}

    def test_single_pid_two_listeners(self):
        stdout = "p1234\nf22\nnlocalhost:65470\nf23\nnlocalhost:65471\n"
        assert _parse_loopback_counts(stdout) == {1234: 2}

    def test_multiple_pids(self):
        stdout = (
            "p1111\nnlocalhost:8000\nnlocalhost:8001\n"
            "p2222\nnlocalhost:9000\n"
            "p3333\nn127.0.0.1:7000\nn127.0.0.1:7001\n"
        )
        assert _parse_loopback_counts(stdout) == {1111: 2, 2222: 1, 3333: 2}

    def test_pid_with_no_matching_listeners_still_present(self):
        # A `p` section with zero `n` lines means "pid scanned, no listeners".
        stdout = "p1111\np2222\nnlocalhost:9000\n"
        assert _parse_loopback_counts(stdout) == {1111: 0, 2222: 1}

    def test_ipv4_and_localhost_mixed(self):
        stdout = "p1234\nnlocalhost:8080\nn127.0.0.1:8081\n"
        assert _parse_loopback_counts(stdout) == {1234: 2}

    def test_ignores_non_loopback_addresses(self):
        stdout = (
            "p1234\n"
            "n192.168.1.5:22\n"
            "n*:8080\n"
            "n[::1]:3000\n"  # IPv6 loopback — not counted since sandbox uses IPv4
            "nlocalhost:9000\n"
        )
        assert _parse_loopback_counts(stdout) == {1234: 1}

    def test_ignores_f_lines(self):
        # -F pn may interleave f<fd> lines; they carry no address info.
        stdout = "p1234\nf22\nnlocalhost:1\nf23\nnlocalhost:2\n"
        assert _parse_loopback_counts(stdout) == {1234: 2}

    def test_orphan_n_lines_before_any_p_section(self):
        # Should not crash; just ignored because no current pid.
        stdout = "nlocalhost:1\np1234\nnlocalhost:2\n"
        assert _parse_loopback_counts(stdout) == {1234: 1}

    def test_garbage_pid_skipped(self):
        stdout = "pXYZ\nnlocalhost:1\np1234\nnlocalhost:2\n"
        # pXYZ resets current to None so its n-line is ignored.
        assert _parse_loopback_counts(stdout) == {1234: 1}

    def test_blank_lines_ignored(self):
        stdout = "\np1234\n\nnlocalhost:1\n\n"
        assert _parse_loopback_counts(stdout) == {1234: 1}


class TestRunLsof:
    def test_success_returns_stdout(self):
        with patch("subprocess.run", return_value=_lsof_result("p1\nnlocalhost:1\n")):
            assert _run_lsof([1]) == "p1\nnlocalhost:1\n"

    def test_rc_1_treated_as_empty_success(self):
        # macOS lsof exits 1 when one or more target pids have no matches.
        with patch("subprocess.run", return_value=_lsof_result("", returncode=1)):
            assert _run_lsof([1]) == ""

    def test_rc_2_treated_as_failure(self):
        with patch("subprocess.run", return_value=_lsof_result("", returncode=2)):
            assert _run_lsof([1]) is None

    def test_timeout_returns_none(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("lsof", 3)):
            assert _run_lsof([1]) is None

    def test_missing_binary_returns_none(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _run_lsof([1]) is None

    def test_empty_pid_list_short_circuits(self):
        with patch("subprocess.run") as mock_run:
            assert _run_lsof([]) == ""
            mock_run.assert_not_called()

    def test_single_batched_call_for_multiple_pids(self):
        with patch("subprocess.run", return_value=_lsof_result("")) as mock_run:
            _run_lsof([1111, 2222, 3333])
            assert mock_run.call_count == 1
            args = mock_run.call_args[0][0]
            # Comma-separated pid list in the -p argument
            assert "-p" in args
            pid_arg = args[args.index("-p") + 1]
            assert set(pid_arg.split(",")) == {"1111", "2222", "3333"}

    def test_deduplicates_pids(self):
        with patch("subprocess.run", return_value=_lsof_result("")) as mock_run:
            _run_lsof([1111, 1111, 2222])
            args = mock_run.call_args[0][0]
            pid_arg = args[args.index("-p") + 1]
            assert sorted(pid_arg.split(",")) == ["1111", "2222"]


class TestDetectSandboxStates:
    def test_empty_returns_empty(self):
        assert detect_sandbox_states([]) == {}

    def test_non_darwin_returns_unknowns(self):
        with patch.object(sandbox_detect, "sys") as mock_sys:
            mock_sys.platform = "linux"
            assert detect_sandbox_states([1, 2]) == {1: None, 2: None}

    def test_lsof_failure_returns_unknowns_for_all(self):
        with patch.object(sandbox_detect, "sys") as mock_sys, \
             patch.object(sandbox_detect, "_run_lsof", return_value=None):
            mock_sys.platform = "darwin"
            assert detect_sandbox_states([1, 2, 3]) == {1: None, 2: None, 3: None}

    def test_per_pid_classification(self):
        # pid 1111 has sandbox on (2 listeners), 2222 has one, 3333 none.
        stdout = (
            "p1111\nnlocalhost:1\nnlocalhost:2\n"
            "p2222\nnlocalhost:3\n"
        )
        with patch.object(sandbox_detect, "sys") as mock_sys, \
             patch.object(sandbox_detect, "_run_lsof", return_value=stdout):
            mock_sys.platform = "darwin"
            result = detect_sandbox_states([1111, 2222, 3333])
        assert result == {1111: True, 2222: False, 3333: False}

    def test_missing_pid_treated_as_off_not_unknown(self):
        # When lsof succeeds but a pid isn't in output (no fds match), that
        # is a confirmed "no listeners" — not unknown.
        with patch.object(sandbox_detect, "sys") as mock_sys, \
             patch.object(sandbox_detect, "_run_lsof", return_value=""):
            mock_sys.platform = "darwin"
            assert detect_sandbox_states([42]) == {42: False}

    def test_single_call_regardless_of_pid_count(self):
        with patch.object(sandbox_detect, "sys") as mock_sys, \
             patch.object(sandbox_detect, "_run_lsof", return_value="") as mock_run:
            mock_sys.platform = "darwin"
            detect_sandbox_states([1, 2, 3, 4, 5])
            assert mock_run.call_count == 1


class TestIsSandboxEnabled:
    def test_none_pid_returns_none(self):
        assert is_sandbox_enabled(None) is None

    def test_delegates_to_batch(self):
        with patch.object(sandbox_detect, "detect_sandbox_states", return_value={1234: True}):
            assert is_sandbox_enabled(1234) is True

    def test_missing_from_batch_returns_none(self):
        with patch.object(sandbox_detect, "detect_sandbox_states", return_value={}):
            assert is_sandbox_enabled(1234) is None
