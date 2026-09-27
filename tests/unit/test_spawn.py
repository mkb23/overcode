"""Tests for spawn.install(): posix_spawn as the default for child processes (#486)."""

import os
import subprocess
import warnings
from unittest.mock import patch

import pytest

from overcode import spawn


@pytest.fixture
def installed():
    """spawn.install() for one test, with stock Popen restored after."""
    original = subprocess.Popen.__init__
    spawn._installed = False
    spawn._paths.clear()
    spawn.install()
    yield
    subprocess.Popen.__init__ = original
    spawn._installed = False
    spawn._paths.clear()


def _spawned_with_posix_spawn(*args, **kwargs) -> bool:
    with patch.object(subprocess.Popen, "_posix_spawn", autospec=True,
                      side_effect=subprocess.Popen._posix_spawn) as posix_spawn:
        subprocess.run(*args, **kwargs)
    return posix_spawn.called


class TestResolve:
    def test_finds_and_remembers_a_path(self):
        spawn._paths.clear()
        with patch("overcode.spawn.shutil.which", return_value="/usr/bin/true") as which:
            assert spawn.resolve("true") == "/usr/bin/true"
            assert spawn.resolve("true") == "/usr/bin/true"
        assert which.call_count == 1

    def test_a_miss_is_not_remembered(self):
        spawn._paths.clear()
        with patch("overcode.spawn.shutil.which", side_effect=[None, "/opt/bin/tmux"]):
            assert spawn.resolve("tmux") is None
            assert spawn.resolve("tmux") == "/opt/bin/tmux"


class TestInstall:
    def test_stock_subprocess_forks(self):
        assert not _spawned_with_posix_spawn(["true"], capture_output=True)

    def test_bare_name_goes_through_posix_spawn(self, installed):
        assert _spawned_with_posix_spawn(["true"], capture_output=True, text=True)

    def test_output_and_exit_status_are_unchanged(self, installed):
        result = subprocess.run(["sh", "-c", "echo out; echo err >&2; exit 3"],
                                capture_output=True, text=True)
        assert (result.stdout, result.stderr, result.returncode) == ("out\n", "err\n", 3)

    def test_explicit_close_fds_is_respected(self, installed):
        assert not _spawned_with_posix_spawn(["true"], close_fds=True)

    def test_env_keeps_the_bare_name(self, installed):
        # A caller's own env may carry its own PATH: don't resolve on ours
        with patch.object(subprocess.Popen, "_execute_child", autospec=True) as child:
            subprocess.Popen(["true"], env={"PATH": "/nowhere"})
        assert child.call_args.args[1] == ["true"]

    def test_pass_fds_keeps_close_fds_without_warning(self, installed):
        r, w = os.pipe()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                subprocess.run(["true"], pass_fds=(w,))
        finally:
            os.close(r)
            os.close(w)

    def test_fds_are_not_leaked_to_children(self, installed):
        # PEP 446: our fds are non-inheritable, so close_fds=False hands the
        # child nothing beyond 0-2
        r, w = os.pipe()
        try:
            result = subprocess.run(["sh", "-c", f"test -e /dev/fd/{w} && echo leaked || echo ok"],
                                    capture_output=True, text=True)
        finally:
            os.close(r)
            os.close(w)
        assert result.stdout.strip() == "ok"

    def test_is_idempotent(self, installed):
        wrapped = subprocess.Popen.__init__
        spawn.install()
        assert subprocess.Popen.__init__ is wrapped
