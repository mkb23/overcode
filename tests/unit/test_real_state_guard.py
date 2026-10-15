"""The real-state guard (paths.guard_real_state).

A script that imports overcode as a library inside an overcode agent must
not read or write the real ~/.overcode; overcode's own entry points may.
Every "real" tree here is the fake HOME's ~/.overcode (tests/unit/conftest.py
moves HOME into the run dir, and each test moves it again into tmp_path),
never the user's.
"""

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from overcode import paths

SRC = Path(__file__).resolve().parents[2] / "src"
AGENT_VARS = ("OVERCODE_SESSION_ID", "OVERCODE_SESSION_NAME", "OVERCODE_TMUX_SESSION")


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A HOME of this test's own, default paths, no agent, no entry-point mark."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("OVERCODE_DIR", "OVERCODE_STATE_DIR", "OVERCODE_ALLOW_REAL_STATE",
                *AGENT_VARS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(paths, "_entry_point", False)
    return home


@pytest.fixture
def in_agent(fake_home, monkeypatch):
    """As if this process were a script run in an overcode agent's shell."""
    monkeypatch.setenv("OVERCODE_SESSION_ID", "11111111-2222-3333-4444-555555555555")
    monkeypatch.setenv("OVERCODE_SESSION_NAME", "reviewer")
    monkeypatch.setenv("OVERCODE_TMUX_SESSION", "agents")
    return fake_home


# --- refusal -------------------------------------------------------------

def test_default_paths_are_refused_inside_an_agent(in_agent):
    for resolve in (paths.overcode_dir, paths.sessions_root, paths.sessions_registry_dir,
                    paths.jobs_dir, paths.config_file, paths.ledger_dir, paths.archive_dir):
        with pytest.raises(paths.RealStateRefused, match="refusing to use the real state dir"):
            resolve()


def test_either_agent_variable_is_enough(fake_home, monkeypatch):
    monkeypatch.setenv("OVERCODE_SESSION_NAME", "reviewer")
    with pytest.raises(paths.RealStateRefused):
        paths.overcode_dir()
    monkeypatch.delenv("OVERCODE_SESSION_NAME")
    monkeypatch.setenv("OVERCODE_SESSION_ID", "abc")
    with pytest.raises(paths.RealStateRefused):
        paths.sessions_root()


def test_an_override_that_names_the_real_tree_is_refused(in_agent, monkeypatch):
    monkeypatch.setenv("OVERCODE_DIR", str(in_agent / ".overcode"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(in_agent / ".overcode" / "sessions"))
    with pytest.raises(paths.RealStateRefused):
        paths.sessions_root()
    with pytest.raises(paths.RealStateRefused):
        paths.overcode_dir()


def test_state_dir_alone_still_refuses_the_real_data_dir(in_agent, tmp_path, monkeypatch):
    """OVERCODE_STATE_DIR moves sessions, not config.yaml/jobs: those stay real."""
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "state"))
    assert paths.sessions_root() == tmp_path / "state"
    assert paths.ledger_dir() == tmp_path / "state" / "ledger"
    with pytest.raises(paths.RealStateRefused):
        paths.overcode_dir()


def test_a_pre_06_legacy_registry_in_the_real_tree_is_refused(in_agent, tmp_path, monkeypatch):
    """OVERCODE_DIR elsewhere must not fall back to the real ~/.overcode/sessions."""
    legacy = in_agent / ".overcode" / "sessions"
    legacy.mkdir(parents=True)
    (legacy / "sessions.json").write_text("{}")
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
    with pytest.raises(paths.RealStateRefused):
        paths.sessions_registry_dir()


def test_session_manager_from_a_script_is_refused(in_agent):
    from overcode.session_manager import SessionManager

    with pytest.raises(paths.RealStateRefused):
        SessionManager()
    assert not (in_agent / ".overcode").exists()


# --- allowed -------------------------------------------------------------

def test_outside_an_agent_library_use_works(fake_home):
    assert paths.overcode_dir() == fake_home / ".overcode"
    assert paths.sessions_root() == fake_home / ".overcode" / "sessions"


def test_temp_dirs_inside_an_agent_work(in_agent, tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / "oc"))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "oc" / "sessions"))
    assert paths.overcode_dir() == tmp_path / "oc"
    assert paths.sessions_registry_dir() == tmp_path / "oc" / "sessions"


def test_a_sibling_named_like_the_real_tree_is_not_real(in_agent, monkeypatch):
    monkeypatch.setenv("OVERCODE_DIR", str(in_agent / ".overcode-scratch"))
    assert paths.overcode_dir() == in_agent / ".overcode-scratch"


def test_bypass(in_agent, monkeypatch):
    monkeypatch.setenv("OVERCODE_ALLOW_REAL_STATE", "1")
    assert paths.overcode_dir() == in_agent / ".overcode"
    monkeypatch.setenv("OVERCODE_ALLOW_REAL_STATE", "0")
    with pytest.raises(paths.RealStateRefused):
        paths.overcode_dir()


def test_mark_entry_point_allows(in_agent):
    paths.mark_entry_point()
    assert paths.overcode_dir() == in_agent / ".overcode"


def test_python_dash_m_overcode_module_is_allowed(in_agent, monkeypatch):
    main = types.ModuleType("__main__")
    main.__spec__ = types.SimpleNamespace(name="overcode.some_new_daemon")
    monkeypatch.setitem(sys.modules, "__main__", main)
    assert paths.overcode_dir() == in_agent / ".overcode"
    main.__spec__ = types.SimpleNamespace(name="overcodex.tool")
    with pytest.raises(paths.RealStateRefused):
        paths.overcode_dir()


# --- each entry point marks the process ------------------------------------

class _Marked(Exception):
    pass


@pytest.fixture
def mark_spy(monkeypatch):
    """mark_entry_point stops the entry point the moment it is called."""
    def spy():
        raise _Marked
    monkeypatch.setattr(paths, "mark_entry_point", spy)


def test_entrypoint_marks_before_the_hook_fast_path(in_agent, monkeypatch):
    import overcode.hook_handler
    from overcode import entrypoint

    seen = []
    monkeypatch.setattr(overcode.hook_handler, "handle_hook_event",
                        lambda backend=None: seen.append(paths.overcode_dir()))
    monkeypatch.setattr(sys, "argv", ["overcode", "hook-handler"])
    entrypoint.main()
    assert seen == [in_agent / ".overcode"]


def test_entrypoint_marks_before_the_cli(in_agent, monkeypatch):
    import overcode.cli
    from overcode import entrypoint

    seen = []
    monkeypatch.setattr(overcode.cli, "main", lambda: seen.append(paths.overcode_dir()))
    monkeypatch.setattr(sys, "argv", ["overcode", "list"])
    entrypoint.main()
    assert seen == [in_agent / ".overcode"]


def test_cli_main_marks(in_agent, monkeypatch):
    import overcode.cli

    seen = []
    monkeypatch.setattr(overcode.cli, "app", lambda: seen.append(paths.overcode_dir()))
    overcode.cli.main()
    assert seen == [in_agent / ".overcode"]


@pytest.mark.parametrize("module", [
    "overcode.monitor_daemon",
    "overcode.supervisor_daemon",
    "overcode.web_server_runner",
    "overcode.presence_logger",
])
def test_daemon_and_server_mains_mark_first(module, mark_spy, monkeypatch):
    import importlib

    monkeypatch.setattr(sys, "argv", [module, "--help"])
    with pytest.raises(_Marked):
        importlib.import_module(module).main()


def test_run_tui_marks_first(mark_spy):
    from overcode.tui import run_tui

    with pytest.raises(_Marked):
        run_tui("agents", "agents-view")


# --- real processes ------------------------------------------------------

def _agent_env(home: Path, **extra) -> dict:
    """A fake agent's environment whose "real default" tree is home/.overcode."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("OVERCODE_DIR", "OVERCODE_STATE_DIR", "OVERCODE_ALLOW_REAL_STATE",
                        "TMUX", "TMUX_PANE")}
    env.update(
        HOME=str(home),
        PYTHONPATH=str(SRC),
        OVERCODE_SESSION_ID="11111111-2222-3333-4444-555555555555",
        OVERCODE_SESSION_NAME="reviewer",
        OVERCODE_TMUX_SESSION="agents",
        OVERCODE_ACTIVITY="0",
    )
    env.update(extra)
    return env


def _run(args, env, stdin=""):
    return subprocess.run([sys.executable, *args], env=env, input=stdin,
                          capture_output=True, text=True, timeout=60)


SCRIPT = ("from overcode.session_manager import SessionManager\n"
          "SessionManager()\n"
          "print('ok')\n")


def test_subprocess_script_is_refused(tmp_path):
    result = _run(["-c", SCRIPT], _agent_env(tmp_path))
    assert result.returncode != 0
    assert "refusing to use the real state dir" in result.stderr
    assert not (tmp_path / ".overcode").exists()


def test_subprocess_script_with_bypass_runs(tmp_path):
    result = _run(["-c", SCRIPT], _agent_env(tmp_path, OVERCODE_ALLOW_REAL_STATE="1"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_subprocess_script_outside_an_agent_runs(tmp_path):
    env = _agent_env(tmp_path)
    for var in AGENT_VARS:
        env.pop(var)
    result = _run(["-c", SCRIPT], env)
    assert result.returncode == 0, result.stderr


def test_subprocess_cli_help_and_report_are_allowed(tmp_path):
    env = _agent_env(tmp_path)
    assert _run(["-m", "overcode.cli", "--help"], env).returncode == 0
    result = _run(["-m", "overcode.cli", "report", "--status", "success"], env)
    assert result.returncode == 0, result.stderr + result.stdout
    agent = tmp_path / ".overcode" / "sessions" / "agents" / "agents" / env["OVERCODE_SESSION_ID"]
    assert (agent / "report.json").exists()


def test_subprocess_hook_handler_fast_path_is_allowed(tmp_path):
    env = _agent_env(tmp_path)
    run_entrypoint = ("import sys\n"
                      "sys.argv = ['overcode', 'hook-handler']\n"
                      "from overcode.entrypoint import main\n"
                      "main()\n")
    payload = json.dumps({"hook_event_name": "Stop", "session_id": "claude-abc"})
    result = _run(["-c", run_entrypoint], env, stdin=payload)
    assert result.returncode == 0, result.stderr
    agent = tmp_path / ".overcode" / "sessions" / "agents" / "agents" / env["OVERCODE_SESSION_ID"]
    assert (agent / "hook_state.json").exists()
