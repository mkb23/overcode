"""
Unit tests for NewAgentDefaultsModal — the "G" modal's backend selector.

Exercises modal state directly (no running Textual app), the same pattern
test_new_agent_modal.py uses.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.tui_widgets.new_agent_defaults_modal import (
    NewAgentDefaultsModal,
    UNSET_BACKEND,
)
from overcode.backends import list_backends


def _make_modal() -> NewAgentDefaultsModal:
    return NewAgentDefaultsModal(id="test-defaults-modal")


class TestShowPopulatesBackendOptions:
    def test_unset_when_not_explicit(self):
        modal = _make_modal()
        modal.show({
            "bypass_permissions": False, "agent_teams": False,
            "provider": "web", "wrapper": "",
            "backend": "claude-code", "backend_explicit": False,
        })
        assert modal.backend_value == UNSET_BACKEND
        assert modal.backend_options[0] == UNSET_BACKEND
        assert set(modal.backend_options[1:]) == set(list_backends())

    def test_explicit_backend_preselected(self):
        modal = _make_modal()
        modal.show({
            "bypass_permissions": False, "agent_teams": False,
            "provider": "web", "wrapper": "",
            "backend": "opencode", "backend_explicit": True,
        })
        assert modal.backend_value == "opencode"

    def test_explicit_but_unknown_backend_falls_back_to_unset(self):
        """A stale config value naming a backend this build doesn't have."""
        modal = _make_modal()
        modal.show({
            "bypass_permissions": False, "agent_teams": False,
            "provider": "web", "wrapper": "",
            "backend": "some-future-backend", "backend_explicit": True,
        })
        assert modal.backend_value == UNSET_BACKEND


class TestCycleBackend:
    def test_cycles_through_all_options_and_wraps(self):
        modal = _make_modal()
        modal.show({"backend": "claude-code", "backend_explicit": False})
        start = modal.backend_value
        assert start == UNSET_BACKEND

        seen = [modal.backend_value]
        for _ in range(len(modal.backend_options)):
            modal._cycle_backend()
            seen.append(modal.backend_value)

        # After a full cycle we're back to the start.
        assert seen[-1] == start
        # Every option was visited exactly once (plus the repeated start).
        assert set(seen[:-1]) == set(modal.backend_options)

    def test_selecting_grok_then_cycling_advances(self):
        modal = _make_modal()
        modal.show({"backend": "grok", "backend_explicit": True})
        assert modal.backend_value == "grok"
        modal._cycle_backend()
        assert modal.backend_value != "grok"


class TestApplyMessage:
    def test_apply_sends_none_for_unset(self):
        modal = _make_modal()
        modal.show({
            "bypass_permissions": True, "agent_teams": False,
            "provider": "web", "wrapper": "",
            "backend": "claude-code", "backend_explicit": False,
        })
        posted = []
        modal.post_message = lambda msg: posted.append(msg)
        modal._apply()

        assert len(posted) == 1
        result = posted[0].defaults
        assert result["backend"] is None
        assert "backend_explicit" not in result
        assert result["bypass_permissions"] is True

    def test_apply_sends_selected_backend_name(self):
        modal = _make_modal()
        modal.show({
            "bypass_permissions": False, "agent_teams": False,
            "provider": "web", "wrapper": "",
            "backend": "codex", "backend_explicit": True,
        })
        posted = []
        modal.post_message = lambda msg: posted.append(msg)
        modal._apply()

        result = posted[0].defaults
        assert result["backend"] == "codex"
        assert "backend_explicit" not in result

    def test_apply_after_cycling_away_from_explicit(self):
        modal = _make_modal()
        modal.show({
            "bypass_permissions": False, "agent_teams": False,
            "provider": "web", "wrapper": "",
            "backend": "codex", "backend_explicit": True,
        })
        modal._cycle_backend()
        chosen = modal.backend_value

        posted = []
        modal.post_message = lambda msg: posted.append(msg)
        modal._apply()

        expected = None if chosen == UNSET_BACKEND else chosen
        assert posted[0].defaults["backend"] == expected


class TestConfigRoundTrip:
    """The modal's output dict round-trips cleanly through config.py."""

    def test_round_trip_explicit_backend(self, tmp_path, monkeypatch):
        from overcode import config

        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", config_path)
        config._clear_config_cache()

        modal = _make_modal()
        modal.show(config.get_new_agent_defaults())
        modal.backend_value = "opencode"

        posted = []
        modal.post_message = lambda msg: posted.append(msg)
        modal._apply()
        config.save_new_agent_defaults(posted[0].defaults)

        reloaded = config.get_new_agent_defaults()
        assert reloaded["backend"] == "opencode"
        assert reloaded["backend_explicit"] is True

    def test_round_trip_unset_backend_falls_back_to_default(self, tmp_path, monkeypatch):
        from overcode import config
        from overcode.backends import DEFAULT_BACKEND

        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr(config, "CONFIG_PATH", config_path)
        config._clear_config_cache()

        modal = _make_modal()
        modal.show(config.get_new_agent_defaults())
        modal.backend_value = UNSET_BACKEND

        posted = []
        modal.post_message = lambda msg: posted.append(msg)
        modal._apply()
        config.save_new_agent_defaults(posted[0].defaults)

        reloaded = config.get_new_agent_defaults()
        assert reloaded["backend"] == DEFAULT_BACKEND
        assert reloaded["backend_explicit"] is False
