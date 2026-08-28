"""Cross-backend sweep — 0.6.0 hardening pass.

Phase 5 of docs/design/agent-backends-codex-grok.md asks for one place that
proves the four backends (claude-code, opencode, codex, grok) behave
consistently as a *fleet*, rather than only individually the way each
backend's own test_backend_*.py file does. These are mock-level/unit tests —
no live CLIs, no tmux — covering: the registry lists all four, their BKD
badges are distinct, the new-agent modal's backend picker cycles all four
using the real production code path (not a hand-rolled duplicate), and
`overcode doctor` runs cleanly over a fleet that mixes all four backends in
one session.

Supervisor context-line backend naming for codex/grok is covered by
test_supervisor_daemon_core.py's parametrized
TestBuildDaemonClaudeContext::test_non_default_backend_is_named.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import DEFAULT_BACKEND, get_backend, list_backends
from overcode.cli import app
from overcode.doctor import AgentHealth, VERDICT_MISSING_SETTINGS, VERDICT_OK
from overcode.session_manager import Session, SessionStats
from overcode.summary_columns import BACKEND_BADGES, _backend_badge
from overcode.tui_widgets.new_agent_modal import NewAgentModal

EXPECTED_BACKENDS = {"claude-code", "opencode", "codex", "grok"}

runner = CliRunner()


class TestRegistryListsFour:
    def test_registry_has_exactly_the_four_backends(self):
        assert set(list_backends()) == EXPECTED_BACKENDS

    @pytest.mark.parametrize("name", sorted(EXPECTED_BACKENDS))
    def test_each_name_resolves_to_a_backend(self, name):
        backend = get_backend(name)
        assert backend.name == name

    def test_default_backend_is_claude_code(self):
        # Every capability-gating / doc-honesty claim in backends.md assumes
        # a Claude-only fleet is unchanged — that only holds if claude-code
        # stays the default.
        assert DEFAULT_BACKEND == "claude-code"
        assert DEFAULT_BACKEND in list_backends()


class TestBackendBadgesDistinct:
    def test_every_registered_backend_has_a_badge(self):
        assert set(BACKEND_BADGES) == set(list_backends())

    def test_badges_are_pairwise_distinct(self):
        badges = list(BACKEND_BADGES.values())
        assert len(badges) == len(set(badges))

    def test_badges_are_two_characters(self):
        # summary_columns.render_backend keeps the column at a fixed width.
        for badge in BACKEND_BADGES.values():
            assert len(badge) == 2

    @pytest.mark.parametrize("name", sorted(EXPECTED_BACKENDS))
    def test_lookup_helper_matches_the_table(self, name):
        assert _backend_badge(name) == BACKEND_BADGES[name]

    def test_unknown_backend_falls_back_to_a_truncated_name(self):
        # A sister on a newer overcode build could report a backend this
        # build has never heard of — the badge renderer must not crash.
        assert _backend_badge("future-cli") == "fu"


class TestNewAgentModalCyclesAllFour:
    """Drives the real NewAgentModal.show()/._cycle(), not a hand-rolled copy.

    NewAgentModal can be constructed and driven without a running Textual
    app (the existing TestNewAgentModalState suite in test_new_agent_modal.py
    does the same for its own hand-built fields); this class instead calls
    the production show()/_cycle() so a future change to the backend picker's
    ordering logic is actually exercised.
    """

    def _shown_modal(self, **defaults_overrides) -> NewAgentModal:
        modal = NewAgentModal(id="test-modal")
        defaults = {
            "bypass_permissions": False,
            "agent_teams": False,
            "provider": "web",
            "wrapper": "",
        }
        defaults.update(defaults_overrides)
        modal.show(
            directory="/tmp/proj",
            defaults=defaults,
            agents=[],
            existing_names=set(),
            local_hostname="host",
            sister_names=[],
            wrappers=[],
            app_ref=None,
        )
        return modal

    def test_backend_field_options_are_the_full_registry(self):
        modal = self._shown_modal()
        backend_field = modal._field("backend")
        assert set(backend_field.options) == EXPECTED_BACKENDS

    def test_configured_default_leads_the_cycle(self):
        modal = self._shown_modal(backend="opencode")
        backend_field = modal._field("backend")
        assert backend_field.options[0] == "opencode"
        assert backend_field.value == "opencode"

    def test_cycling_visits_every_backend_exactly_once(self):
        modal = self._shown_modal()
        backend_field = modal._field("backend")
        seen = []
        for _ in range(len(backend_field.options)):
            seen.append(backend_field.value)
            modal._cycle(backend_field)
        assert set(seen) == EXPECTED_BACKENDS
        # A full cycle returns to the starting value.
        assert backend_field.value == seen[0]


def _make_session(name, backend, **overrides):
    defaults = dict(
        id=f"sid-{name}",
        name=name,
        tmux_session="agents",
        tmux_window=f"{name}-0",
        command=[get_backend(backend).binary],
        start_directory="/tmp",
        start_time="2026-08-28T12:00:00",
        stats=SessionStats(),
        status="running",
        backend=backend,
    )
    defaults.update(overrides)
    return Session(**defaults)


class TestDoctorOverAFourBackendFleet:
    """`overcode doctor` must not crash when the fleet mixes all four backends.

    Mirrors test_doctor_cli.py's patch shape (AgentLauncher, snapshot_process_table,
    inspect_agent, is_monitor_daemon_running, bundled_skills.any_skills_stale) and
    additionally exercises the three per-backend version_findings dispatches
    doctor.py gates on fleet membership (opencode/codex/grok).
    """

    def _run(self, sessions, health_by_name):
        mock_launcher = MagicMock()
        mock_launcher.list_sessions.return_value = sessions
        mock_launcher.tmux.get_pane_pid.return_value = 12345

        def fake_inspect(sess, *args, **kwargs):
            return health_by_name[sess.name]

        patches = [
            patch("overcode.launcher.AgentLauncher", return_value=mock_launcher),
            patch("overcode.doctor.snapshot_process_table", return_value=({}, {})),
            patch("overcode.doctor.inspect_agent", side_effect=fake_inspect),
            patch("overcode.monitor_daemon.is_monitor_daemon_running", return_value=True),
            patch("overcode.stats_reader.stats_reader_for_session",
                  return_value=MagicMock(get_stats=MagicMock(return_value=None))),
            patch("overcode.bundled_skills.any_skills_stale", return_value=False),
            patch("overcode.backends.opencode.version_findings", return_value=[]),
            patch("overcode.backends.codex.version_findings", return_value=[]),
            patch("overcode.backends.grok.version_findings", return_value=[]),
        ]
        for p in patches:
            p.start()
        try:
            return runner.invoke(app, ["doctor"])
        finally:
            for p in patches:
                p.stop()

    def test_healthy_fleet_of_four_backends(self):
        sessions = [
            _make_session("cc-agent", "claude-code"),
            _make_session("oc-agent", "opencode"),
            _make_session("cx-agent", "codex"),
            _make_session("gk-agent", "grok"),
        ]
        health_by_name = {
            sess.name: AgentHealth(
                name=sess.name,
                tmux_window=sess.tmux_window,
                launcher_version="0.6.0",
                claude_pid=1000,
                claude_argv=f"{get_backend(sess.backend).binary} --settings x",
                verdict=VERDICT_OK,
                details=f"{sess.backend} process running",
            )
            for sess in sessions
        }

        result = self._run(sessions, health_by_name)

        assert result.exit_code == 0, result.output
        for sess in sessions:
            assert sess.name in result.output
        assert "all 4 agents have hooks injected" in result.output

    def test_one_broken_agent_per_non_default_backend_still_reports_cleanly(self):
        sessions = [
            _make_session("oc-agent", "opencode"),
            _make_session("cx-agent", "codex"),
            _make_session("gk-agent", "grok"),
        ]
        health_by_name = {
            sess.name: AgentHealth(
                name=sess.name,
                tmux_window=sess.tmux_window,
                launcher_version="0.6.0",
                claude_pid=1000,
                claude_argv=get_backend(sess.backend).binary,
                verdict=VERDICT_MISSING_SETTINGS,
                details=f"{sess.backend} running without telemetry injected",
            )
            for sess in sessions
        }

        result = self._run(sessions, health_by_name)

        assert result.exit_code == 0, result.output
        assert "3 broken" in result.output
        assert "overcode restart" in result.output
