"""Capability publication and the Phase 6 compat aliases.

Two things are under test here:

1. **Capability plumbing.** A backend's capabilities ride the daemon-state
   passthrough to sisters as plain strings, so a TUI can gate actions (fork,
   skills, sandbox) on what the *remote* backend can do — including backends
   the local build has never heard of. Sisters that predate Phase 6 report
   nothing and must be read as claude-code with everything enabled.
2. **Rename aliases.** Every pre-Phase-6 public name still resolves.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.backends import (
    BackendCapability,
    DEFAULT_BACKEND,
    capabilities_from_names,
    capability_names,
    get_backend,
    session_capabilities,
    session_supports,
)
from overcode.monitor_daemon_state import SessionDaemonState
from overcode.session_manager import Session
from overcode.sister_poller import _agent_to_session


def _local(backend=DEFAULT_BACKEND) -> Session:
    return Session(
        id="s", name="n", tmux_session="agents", tmux_window="n",
        command=["claude"], start_directory=None, start_time="2026-08-01",
        backend=backend,
    )


class TestCapabilitySerialization:
    def test_names_round_trip(self):
        caps = BackendCapability.FORK | BackendCapability.RESUME
        assert capabilities_from_names(capability_names(caps)) == caps

    def test_names_are_sorted_and_exclude_none(self):
        names = capability_names(BackendCapability.RESUME | BackendCapability.FORK)
        assert names == sorted(names)
        assert "NONE" not in names

    def test_empty_flag_serializes_to_nothing(self):
        assert capability_names(BackendCapability.NONE) == []

    def test_unknown_names_are_ignored(self):
        # A sister on a newer overcode may report capabilities we lack.
        parsed = capabilities_from_names(["FORK", "TIME_TRAVEL"])
        assert parsed == BackendCapability.FORK

    @pytest.mark.parametrize("junk", [None, "FORK", 7, {}])
    def test_non_list_input_is_empty(self, junk):
        assert capabilities_from_names(junk) == BackendCapability.NONE

    def test_claude_code_serializes_its_full_set(self):
        names = capability_names(get_backend("claude-code").capabilities)
        assert "FORK" in names and "SKILLS" in names and "SUBSCRIPTION_USAGE" in names


class TestLocalSessionCapabilities:
    def test_known_backend_resolves_from_the_registry(self):
        assert session_capabilities(_local()) == get_backend("claude-code").capabilities

    def test_unknown_backend_supports_nothing(self):
        assert session_capabilities(_local("no-such-backend")) == BackendCapability.NONE
        assert not session_supports(_local("no-such-backend"), BackendCapability.FORK)

    def test_opencode_has_no_subscription_usage(self):
        assert not session_supports(_local("opencode"), BackendCapability.SUBSCRIPTION_USAGE)
        assert session_supports(_local("opencode"), BackendCapability.FORK)


class TestRemoteSessionCapabilities:
    """Sister agents answer from what the remote daemon published."""

    def _remote(self, daemon_state):
        return _agent_to_session(
            {"name": "remote-1", "status": "running", "daemon_state": daemon_state},
            "sister-host",
        )

    def test_old_sister_without_a_report_defaults_to_claude_code_full(self):
        session = self._remote({})
        assert session.is_remote
        assert session.backend == DEFAULT_BACKEND
        assert session_capabilities(session) == get_backend(DEFAULT_BACKEND).capabilities
        assert session_supports(session, BackendCapability.FORK)

    def test_sister_with_no_daemon_state_at_all_defaults_too(self):
        session = _agent_to_session({"name": "r", "status": "running"}, "host")
        assert session.backend == DEFAULT_BACKEND
        assert session_supports(session, BackendCapability.FORK)

    def test_reported_capabilities_win_over_the_local_registry(self):
        session = self._remote({
            "backend": "claude-code",
            "backend_capabilities": ["RESUME"],
        })
        # Locally claude-code can fork; the sister says this agent can't.
        assert session_supports(session, BackendCapability.RESUME)
        assert not session_supports(session, BackendCapability.FORK)

    def test_backend_name_is_carried_over_from_daemon_state(self):
        session = self._remote({"backend": "opencode", "backend_capabilities": ["FORK"]})
        assert session.backend == "opencode"

    def test_backend_unknown_locally_still_reports_capabilities(self):
        session = self._remote({
            "backend": "some-future-cli",
            "backend_capabilities": ["FORK", "RESUME"],
        })
        assert session.backend == "some-future-cli"
        assert session_supports(session, BackendCapability.FORK)

    def test_top_level_backend_key_is_honoured(self):
        session = _agent_to_session(
            {"name": "r", "status": "running", "backend": "opencode"}, "host"
        )
        assert session.backend == "opencode"


class TestDaemonStatePublication:
    def test_field_defaults_to_empty(self):
        assert SessionDaemonState().backend_capabilities == []

    def test_round_trips_through_to_dict(self):
        state = SessionDaemonState(backend="opencode", backend_capabilities=["FORK"])
        assert SessionDaemonState.from_dict(state.to_dict()).backend_capabilities == ["FORK"]

    def test_old_daemon_state_json_loads_without_the_field(self):
        # Tolerant from_dict is what makes this additive change safe.
        assert SessionDaemonState.from_dict({"name": "a"}).backend_capabilities == []


class TestRenameAliases:
    """The pre-Phase-6 public names still resolve to the same objects."""

    def test_launcher_alias(self):
        from overcode.launcher import AgentLauncher, ClaudeLauncher

        assert ClaudeLauncher is AgentLauncher

    def test_exception_aliases_preserve_isinstance(self):
        from overcode.exceptions import (
            AgentCliError,
            AgentCliNotFoundError,
            AgentCliStartupError,
            ClaudeError,
            ClaudeNotFoundError,
            ClaudeStartupError,
        )

        assert ClaudeError is AgentCliError
        assert ClaudeNotFoundError is AgentCliNotFoundError
        assert ClaudeStartupError is AgentCliStartupError
        assert issubclass(AgentCliNotFoundError, AgentCliError)
        assert isinstance(AgentCliNotFoundError("x"), ClaudeNotFoundError)

    def test_opencode_not_found_is_caught_by_both_spellings(self):
        from overcode.backends.opencode import OpencodeNotFoundError
        from overcode.exceptions import AgentCliNotFoundError, ClaudeNotFoundError

        assert issubclass(OpencodeNotFoundError, AgentCliNotFoundError)
        assert issubclass(OpencodeNotFoundError, ClaudeNotFoundError)

    def test_stats_class_alias(self):
        from overcode.history_reader import AgentSessionStats, ClaudeSessionStats
        from overcode.stats_reader import AgentSessionStats as ReaderStats

        assert ClaudeSessionStats is AgentSessionStats
        assert ReaderStats is AgentSessionStats
        assert AgentSessionStats.__name__ == "AgentSessionStats"
