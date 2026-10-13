"""overcode.paths: the one place overcode's paths are built."""

from pathlib import Path

import pytest

from overcode import paths


@pytest.fixture
def no_env(monkeypatch, tmp_path):
    """A default install: neither variable set, HOME in tmp."""
    monkeypatch.delenv("OVERCODE_DIR", raising=False)
    monkeypatch.delenv("OVERCODE_STATE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


class TestDefaultInstall:
    def test_layout(self, no_env):
        home = no_env / ".overcode"
        assert paths.overcode_dir() == home
        assert paths.sessions_root() == home / "sessions"
        assert paths.session_dir("agents") == home / "sessions" / "agents"
        assert paths.sessions_registry_dir() == home / "sessions"
        assert paths.jobs_dir() == home / "jobs"
        assert paths.config_file() == home / "config.yaml"

    def test_legacy_agent_files(self, no_env):
        d = no_env / ".overcode" / "sessions" / "agents"
        assert paths.legacy_agent_file("agents", "a1", "hook_state") == d / "hook_state_a1.json"
        assert paths.legacy_agent_file("agents", "a1", "hook_events") == d / "hook_events_a1.jsonl"
        assert paths.legacy_agent_file("agents", "a1", "report") == d / "report_a1.json"
        assert paths.legacy_agent_file("agents", "a1", "episodes") == d / "episodes_a1.jsonl"
        assert paths.legacy_agent_file("agents", "a1", "rename_notice") == d / "rename_notice_a1.txt"
        assert paths.legacy_agent_file("agents", "a1", "heartbeat") == d / "heartbeat_a1.last"
        assert paths.legacy_agent_file_name("a1", "hook_state") == "hook_state_a1.json"

    def test_unknown_kind_raises(self, no_env):
        with pytest.raises(KeyError):
            paths.agent_file("agents", "sid-1", "nope")


class TestOvercodeDir:
    def test_moves_everything(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
        monkeypatch.delenv("OVERCODE_STATE_DIR", raising=False)
        assert paths.overcode_dir() == tmp_path / "oc"
        assert paths.sessions_root() == tmp_path / "oc" / "sessions"
        assert paths.jobs_dir() == tmp_path / "oc" / "jobs"
        assert paths.config_file() == tmp_path / "oc" / "config.yaml"


class TestStateDir:
    """OVERCODE_STATE_DIR is the sessions root, and only that."""

    @pytest.fixture
    def env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        return tmp_path

    def test_is_the_sessions_root(self, env):
        assert paths.sessions_root() == env / "state"
        assert paths.session_dir("agents") == env / "state" / "agents"
        assert paths.sessions_registry_dir() == env / "state"
        assert paths.legacy_agent_file("agents", "a", "hook_state") == \
            env / "state" / "agents" / "hook_state_a.json"

    def test_jobs_stay_under_overcode_dir(self, env):
        assert paths.jobs_dir() == env / "oc" / "jobs"
        assert paths.config_file() == env / "oc" / "config.yaml"

    def test_reads_a_tree_with_sessions_under_the_root(self, env):
        legacy = env / "state" / "sessions"
        legacy.mkdir(parents=True)
        (legacy / "sessions.json").write_text("{}")
        assert paths.sessions_registry_dir() == legacy
        # Once the root has its own file, that wins
        (env / "state" / "sessions.json").write_text("{}")
        assert paths.sessions_registry_dir() == env / "state"

    def test_reads_a_tree_with_jobs_under_the_root(self, env):
        legacy = env / "state" / "jobs"
        legacy.mkdir(parents=True)
        (legacy / "jobs.json").write_text("{}")
        assert paths.jobs_dir() == legacy
        current = env / "oc" / "jobs"
        current.mkdir(parents=True)
        (current / "jobs.json").write_text("{}")
        assert paths.jobs_dir() == current

    def test_legacy_layout_ignored_without_the_variable(self, no_env):
        # A tmux session called "sessions" or "jobs" is just a session dir
        root = no_env / ".overcode" / "sessions"
        (root / "sessions").mkdir(parents=True)
        (root / "sessions" / "sessions.json").write_text("{}")
        (root / "jobs").mkdir()
        (root / "jobs" / "jobs.json").write_text("{}")
        assert paths.sessions_registry_dir() == root
        assert paths.jobs_dir() == no_env / ".overcode" / "jobs"


class TestAgentDirs:
    """Each agent's files live in agents/<session id>/ (item 35)."""

    def test_layout(self, no_env):
        d = no_env / ".overcode" / "sessions" / "agents" / "agents" / "sid-1"
        assert paths.agents_dir("agents") == d.parent
        assert paths.agent_dir("agents", "sid-1") == d
        assert paths.agent_file("agents", "sid-1", "hook_state") == d / "hook_state.json"
        assert paths.agent_file("agents", "sid-1", "hook_events") == d / "hook_events.jsonl"
        assert paths.agent_file("agents", "sid-1", "report") == d / "report.json"
        assert paths.agent_file("agents", "sid-1", "episodes") == d / "episodes.jsonl"
        assert paths.agent_file("agents", "sid-1", "rename_notice") == d / "rename_notice.txt"
        assert paths.agent_file("agents", "sid-1", "heartbeat") == d / "heartbeat.last"
        assert set(paths.AGENT_DIR_FILES) == set(paths.AGENT_FILES)

    def test_agent_path_own_dir_when_it_exists(self, no_env):
        own = paths.agent_file("agents", "sid-1", "hook_state")
        own.parent.mkdir(parents=True)
        # A file a dead agent of the same name left is never read
        legacy = paths.legacy_agent_file("agents", "a1", "hook_state")
        legacy.write_text("{}")
        assert paths.agent_path("agents", "sid-1", "a1", "hook_state") == own
        own.write_text("{}")
        assert paths.agent_path("agents", "sid-1", "a1", "hook_state") == own

    def test_agent_path_falls_back_for_an_agent_launched_before(self, no_env):
        # No agents/<sid>/: launched by an older overcode, its hooks write by name
        assert paths.agent_path("agents", "sid-1", "a1", "report") == \
            paths.legacy_agent_file("agents", "a1", "report")
        assert paths.agent_path("agents", None, "a1", "report") == \
            paths.legacy_agent_file("agents", "a1", "report")

    def test_agent_path_without_a_name_is_the_own_file(self, no_env):
        assert paths.agent_path("agents", "sid-1", None, "report") == \
            paths.agent_file("agents", "sid-1", "report")
        with pytest.raises(ValueError):
            paths.agent_path("agents", None, None, "report")

    def test_agent_path_on_another_base(self, tmp_path):
        assert paths.agent_path("x", "sid-1", None, "report", base=tmp_path) == \
            tmp_path / "agents" / "sid-1" / "report.json"
        assert paths.agent_path("x", "sid-1", "a", "report", base=tmp_path) == \
            tmp_path / "report_a.json"

    def test_bundles(self, no_env):
        d = no_env / ".overcode" / "sessions" / "agents" / "bundles"
        assert paths.bundles_dir("agents") == d
        assert paths.bundle_path("agents", "2026-10-12") == d / "2026-10-12.agents.gz"


class TestArchiveAndLedger:
    """The archive and the ledger never sit where a tmux session's directory would."""

    def test_default_install_beside_sessions(self, no_env):
        home = no_env / ".overcode"
        assert paths.archive_dir() == home / "archive"
        assert paths.archive_month_path("2026-10") == home / "archive" / "2026-10.jsonl"
        assert paths.ledger_dir() == home / "ledger"
        assert paths.ledger_month_path("2026-10") == home / "ledger" / "2026-10.jsonl"
        assert paths.ledger_totals_path() == home / "ledger" / "totals.json"

    def test_overcode_dir_moves_them(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
        monkeypatch.delenv("OVERCODE_STATE_DIR", raising=False)
        assert paths.archive_dir() == tmp_path / "oc" / "archive"
        assert paths.ledger_dir() == tmp_path / "oc" / "ledger"

    def test_custom_state_dir_keeps_them_inside_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        assert paths.archive_dir() == tmp_path / "state" / "archive"
        assert paths.ledger_dir() == tmp_path / "state" / "ledger"
        assert {"archive", "ledger"} <= paths.RESERVED_DIR_NAMES

    def test_state_dir_called_sessions(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "x" / "sessions"))
        assert paths.archive_dir() == tmp_path / "x" / "archive"

    def test_explicit_registry(self, tmp_path):
        assert paths.archive_dir(tmp_path / "reg") == tmp_path / "reg" / "archive"
        assert paths.ledger_month_path("2026-01", tmp_path / "sessions") == \
            tmp_path / "ledger" / "2026-01.jsonl"


def test_imports_nothing_from_overcode():
    """The hook handler imports it on every agent event: keep it light."""
    source = Path(paths.__file__).read_text()
    assert "from ." not in source and "import overcode" not in source


class TestManagersUseThem:
    @pytest.fixture
    def env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
        return tmp_path

    def test_session_manager_writes_at_the_root(self, env):
        from overcode.session_manager import SessionManager
        sm = SessionManager(skip_git_detection=True)
        assert sm.state_file == env / "state" / "sessions.json"

    def test_session_manager_reads_an_older_tree(self, env):
        from overcode.session_manager import SessionManager
        old = SessionManager(state_dir=env / "state" / "sessions", skip_git_detection=True)
        old.create_session(name="a", tmux_session="agents", tmux_window="a-1",
                           command=["claude"])
        sm = SessionManager(skip_git_detection=True)
        assert [s.name for s in sm.list_sessions()] == ["a"]

    def test_job_files_live_together(self, env):
        from overcode.job_launcher import JobLauncher
        from overcode.job_manager import JobManager
        jobs = JobManager()
        assert jobs.state_file == env / "oc" / "jobs" / "jobs.json"
        launcher = JobLauncher(tmux_manager=object(), job_manager=jobs)
        assert launcher._exit_code_path("j1") == env / "oc" / "jobs" / "exit-j1"
