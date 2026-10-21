"""Nothing converts 0.5 data until the person has agreed (upgrade_consent)."""

import io
import json

import pytest

from overcode import paths, upgrade_consent as uc
from overcode.session_manager import SessionManager

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    """A state directory per test: consent is a file, and must not leak between them."""
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "home"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "home" / "sessions"))


def _old_archive(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "archive.jsonl").write_text(json.dumps({"id": "old-1", "name": "old"}) + "\n")


class TestDetection:

    def test_a_fresh_install_is_never_asked(self):
        assert uc.unconverted() == [] and not uc.needs_consent()

    def test_an_old_archive_needs_consent_until_granted(self):
        _old_archive(paths.sessions_registry_dir())
        assert uc.needs_consent()
        uc.grant()
        assert not uc.needs_consent()

    def test_name_keyed_files_without_the_layout_marker(self):
        d = paths.session_dir("agents")
        d.mkdir(parents=True)
        (d / "hook_state_old.json").write_text("{}")
        assert uc.needs_consent()
        (d / ".layout").write_text("2")
        assert not uc.needs_consent()


class TestAsking:

    def _ask(self, monkeypatch, answer=None, tty=True, assume_yes=False):
        _old_archive(paths.sessions_registry_dir())
        monkeypatch.setattr("sys.stdin", io.StringIO())
        monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
        said = []
        code = None
        try:
            uc.confirm_or_exit(said.append, ask=lambda prompt: answer, assume_yes=assume_yes)
        except SystemExit as e:
            code = e.code
        return code, "\n".join(said)

    def test_it_explains_and_suggests_a_backup(self, monkeypatch):
        code, said = self._ask(monkeypatch, answer="y")
        assert code is None and uc.granted()
        assert "cp -a" in said and ".bak-0.5" in said and "deleted" in said

    @pytest.mark.parametrize("answer", ["", "n", "no", "maybe"])
    def test_anything_but_yes_converts_nothing(self, monkeypatch, answer):
        code, _ = self._ask(monkeypatch, answer=answer)
        assert code == 0 and not uc.granted()

    def test_without_a_terminal_it_says_how_and_stops(self, monkeypatch):
        code, said = self._ask(monkeypatch, tty=False)
        assert code == 1 and not uc.granted() and "in a terminal" in said

    def test_yes_flag_converts_without_asking(self, monkeypatch):
        code, said = self._ask(monkeypatch, tty=False, assume_yes=True)
        assert code is None and uc.granted() and "cp -a" in said

    def test_nothing_to_convert_says_nothing(self, monkeypatch):
        said = []
        uc.confirm_or_exit(said.append, ask=lambda p: pytest.fail("asked"))
        assert said == []


class TestNothingConvertsWithoutConsent:

    def test_the_archive_is_left_until_agreed(self, tmp_path):
        d = tmp_path / "registry"
        _old_archive(d)
        sm = SessionManager(state_dir=d, skip_git_detection=True)
        sm.list_archived_sessions()
        assert (d / "archive.jsonl").exists()
        uc.grant(d)
        sm.list_archived_sessions()
        assert not (d / "archive.jsonl").exists()           # converted now
        assert any(sm.archive_dir.iterdir())

    def test_the_daemon_does_not_start(self, tmp_path):
        from overcode.monitor_daemon import MonitorDaemon
        _old_archive(paths.sessions_registry_dir())
        daemon = MonitorDaemon.for_test(tmp_path / "daemon")
        with pytest.raises(SystemExit) as e:
            daemon.run()
        assert e.value.code == 1
        assert not daemon.pid_path.exists()
        assert (paths.sessions_registry_dir() / "archive.jsonl").exists()

    def test_overcode_asks_before_touching_tmux(self, monkeypatch):
        from overcode.cli import split
        _old_archive(paths.sessions_registry_dir())
        monkeypatch.setattr("sys.stdin", io.StringIO())
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        monkeypatch.setattr(split, "_tmux_check", lambda *a: pytest.fail("tmux touched"))
        with pytest.raises(SystemExit) as e:
            split.open_split("agents")
        assert e.value.code == 0 and not uc.granted()
