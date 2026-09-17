"""Opencode2Backend: identity, version handling for dev builds, registration,
live-verified key-sequence shapes (graceful exit, clear, approve, reject),
and the verified bypass outcome (no env override beats a project deny)."""
import pytest

from overcode.backends import get_backend, list_backends
from overcode.backends.opencode2 import (
    Opencode2Backend,
    installed_version,
    parse_version,
    version_findings,
)


def test_registered_in_registry():
    assert "opencode2" in list_backends()
    backend = get_backend("opencode2")
    assert isinstance(backend, Opencode2Backend)


def test_identity():
    backend = Opencode2Backend()
    assert backend.name == "opencode2"
    assert backend.binary == "opencode2"
    assert backend.version_args == ("--version",)
    assert "opencode2" in backend.install_hint


def test_executable_honours_override(monkeypatch):
    monkeypatch.setenv("OPENCODE2_COMMAND", "/path/to/mock-opencode2")
    assert Opencode2Backend().executable() == "/path/to/mock-opencode2"
    monkeypatch.delenv("OPENCODE2_COMMAND")
    assert Opencode2Backend().executable() == "opencode2"


def test_parse_version_handles_v_prefix_and_dev_suffix():
    # Real output of `opencode2 --version`: "opencode2 v0.0.0-dev-19272"
    assert parse_version("opencode2 v0.0.0-dev-19272") == (0, 0, 0)
    assert parse_version("opencode2 v2.1.3") == (2, 1, 3)
    assert parse_version("garbage") is None


def test_version_findings_warn_for_preview(monkeypatch):
    monkeypatch.setattr(
        "overcode.backends.opencode2.installed_version",
        lambda: "opencode2 v0.0.0-dev-19272",
    )
    findings = version_findings()
    assert any("preview" in f.lower() for f in findings)


def test_capabilities_no_fork():
    from overcode.backends.base import BackendCapability
    caps = Opencode2Backend().capabilities
    assert not caps & BackendCapability.FORK
    assert caps & BackendCapability.RESUME
    assert caps & BackendCapability.HOOK_EVENTS
    assert caps & BackendCapability.TRANSCRIPT_STATS


def test_graceful_exit_keys_shape():
    # Verified against v0.0.0-dev-19272, Sep 15 2026: two Escapes arm and
    # fire the interrupt ("esc interrupt" -> "esc again to interrupt"),
    # but v2's command autocomplete consumes the first Enter to accept the
    # highlighted row — /exit needs a trailing bare Enter to execute.
    keys = Opencode2Backend().graceful_exit_keys()
    assert all(k.delay_after is not None and k.delay_after > 0 for k in keys)
    assert [(k.keys, k.enter) for k in keys] == [
        ("Escape", False),
        ("Escape", False),
        ("/exit", True),
        ("", True),
    ]


def test_approve_keys_shape():
    # "Allow once" is preselected and a bare Enter confirms it — verified
    # live against v0.0.0-dev-19272, Sep 16 2026: with a project
    # opencode.json forcing {"action": "shell", "resource": "*",
    # "effect": "ask"}, a bare Enter on the "△ Permission required"
    # dialog approved "Allow once", the dialog cleared, and the pane
    # returned to the idle input box.
    keys = Opencode2Backend().approve_keys()
    assert [(k.keys, k.enter) for k in keys] == [("", True)]


def test_reject_keys_nonempty():
    # Escape alone dismisses the v2 permission dialog and rejects the tool
    # call (pane then shows "The user declined this tool call") — verified
    # against v0.0.0-dev-19272, Sep 15 2026.
    keys = Opencode2Backend().reject_keys()
    assert keys  # a documented dismiss sequence exists
    assert [(k.keys, k.enter) for k in keys] == [("Escape", False)]


def test_clear_conversation_keys_shape():
    # /new has the same autocomplete quirk as /exit (verified against
    # v0.0.0-dev-19272, Sep 15 2026): the first Enter accepts the
    # highlighted row, the trailing bare Enter runs it and resets the
    # pane to the banner + empty input box.
    keys = Opencode2Backend().clear_conversation_keys()
    assert all(k.delay_after is not None and k.delay_after > 0 for k in keys)
    assert [(k.keys, k.enter) for k in keys] == [
        ("/new", True),
        ("", True),
    ]


def test_bypass_env_unset_no_override_verified():
    # No env blob (v2 or v1 grammar) overrides a v2 project deny —
    # `--auto` is the whole bypass. The branch must therefore leave
    # OPENCODE_PERMISSION unset.
    from overcode.backends.base import LaunchSpec

    env = Opencode2Backend().env_prefix(
        LaunchSpec(dangerously_skip_permissions=True)
    )
    assert "OPENCODE_PERMISSION" not in env


def test_doctor_emits_opencode2_findings():
    # Mirrors test_doctor_cli.py's patch harness (AgentLauncher,
    # snapshot_process_table, inspect_agent, is_monitor_daemon_running) plus
    # the stats/skills stubs test_cross_backend_sweep.py adds, driving the
    # real `overcode doctor` CLI over an opencode2-only fleet. The doctor
    # block gates on fleet membership, so the preview finding only renders
    # when the backend-name dispatch actually recognises the session.
    from unittest.mock import MagicMock, patch

    from typer.testing import CliRunner

    from overcode.cli import app
    from overcode.doctor import AgentHealth, VERDICT_OK
    from overcode.session_manager import Session, SessionStats

    runner = CliRunner()
    sess = Session(
        id="sid-oc2-agent",
        name="oc2-agent",
        tmux_session="agents",
        tmux_window="oc2-agent-0",
        command=["opencode2", "--standalone"],
        start_directory="/tmp",
        start_time="2026-09-16T12:00:00",
        stats=SessionStats(),
        status="running",
        backend="opencode2",
    )
    health = AgentHealth(
        name="oc2-agent",
        tmux_window="oc2-agent-0",
        launcher_version="0.5.1",
        claude_pid=1000,
        claude_argv="opencode2 --standalone",
        verdict=VERDICT_OK,
        details="opencode2 process running",
    )

    mock_launcher = MagicMock()
    mock_launcher.list_sessions.return_value = [sess]
    mock_launcher.tmux.get_pane_pid.return_value = 12345

    patches = [
        patch("overcode.launcher.AgentLauncher", return_value=mock_launcher),
        patch("overcode.doctor.snapshot_process_table", return_value=({}, {})),
        patch("overcode.doctor.inspect_agent", return_value=health),
        patch("overcode.monitor_daemon.is_monitor_daemon_running",
              return_value=True),
        patch("overcode.stats_reader.stats_reader_for_session",
              return_value=MagicMock(get_stats=MagicMock(return_value=None))),
        patch("overcode.bundled_skills.any_skills_stale", return_value=False),
        patch("overcode.status_history.disk_usage_findings", return_value=[]),
        # Isolate the version finding from the machine's real opencode2
        # build and SQLite schema: a preview string is stubbed in, and the
        # schema probe is silenced (it has its own test module).
        patch("overcode.backends.opencode2.installed_version",
              return_value="opencode2 v0.0.0-dev-19272"),
        patch("overcode.backends.opencode2_stats.schema_findings",
              return_value=[]),
    ]
    for p in patches:
        p.start()
    try:
        result = runner.invoke(app, ["doctor"])
    finally:
        for p in patches:
            p.stop()

    assert result.exit_code == 0, result.output
    assert "oc2-agent" in result.output
    assert "all 1 agents have hooks injected" in result.output
    assert "v0.0.0-dev-19272" in result.output
    assert "dev preview" in result.output


def test_build_command_always_standalone():
    # --standalone on EVERY launch: the v2 SSE event bus is
    # directory-scoped, so two overcode agents in the SAME directory each
    # receive BOTH sessions' events (cross-talk verified live, Sep 15
    # 2026, v0.0.0-dev-19272), and the telemetry reducer adopts the first
    # session id it sees — agent B would mirror agent A's turns. A private
    # server per agent restores the v1 plugin's process scoping.
    from overcode.backends.base import LaunchSpec

    backend = Opencode2Backend()
    assert backend.build_command(LaunchSpec()) == ["opencode2", "--standalone"]
    assert backend.build_command(LaunchSpec(resume_session_id="ses_abc")) == [
        "opencode2",
        "--standalone",
        "--session",
        "ses_abc",
    ]
    assert backend.build_command(
        LaunchSpec(dangerously_skip_permissions=True)
    ) == ["opencode2", "--standalone", "--auto"]
    assert backend.build_command(LaunchSpec(extra_args=["--flag x"])) == [
        "opencode2",
        "--standalone",
        "--flag",
        "x",
    ]
