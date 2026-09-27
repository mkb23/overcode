"""Tests for the mentor (#483 P3): the director, its state, and the TUI's gates."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from overcode import mentor as M
from overcode.journey import Residue, compute_journey
from overcode.mentor import MentorState, Nudge
from overcode.usage_analytics import summarize

NOW = 1_790_000_000_000.0
DAY = 86_400_000


def _keymap():
    from overcode.command_palette import keys_by_action
    from overcode.tui import SupervisorTUI
    return keys_by_action(SupervisorTUI.BINDINGS)


def _journey(*spec, phantom=()):
    recs, t = [], NOW - 1000
    for action, via, n in spec:
        for _ in range(n):
            t += 10
            recs.append({"kind": "action", "sid": "s", "t": t, "action": action, "via": via, "ok": True})
    for key, n in phantom:
        for _ in range(n):
            t += 10
            recs.append({"kind": "key", "sid": "s", "t": t, "key": key, "ctx": "list"})
    return compute_journey(summarize(recs, frozenset({"j", "k"})), Residue(), _keymap(), now_ms=NOW)


class TestDial:
    @pytest.mark.parametrize("cfg,dial", [
        ({}, "off"), ({"journey": {"mentor": "coach"}}, "coach"),
        ({"journey": {"mentor": False}}, "off"), ({"journey": {"mentor": "loud"}}, "off"),
        ({"journey": "x"}, "off"),
    ])
    def test_default_off(self, monkeypatch, cfg, dial):
        monkeypatch.setattr("overcode.config.load_config", lambda: cfg)
        assert M.mentor_dial() == dial


class TestGates:
    def test_off_never(self):
        assert not M.may_nudge("off", MentorState(), 0, NOW)

    def test_one_per_run_on_occasional(self):
        assert M.may_nudge("occasional", MentorState(), 0, NOW)
        assert not M.may_nudge("occasional", MentorState(), 1, NOW)
        assert M.may_nudge("coach", MentorState(), 1, NOW)

    def test_gap_grows_when_ignored_and_shrinks_when_engaged(self):
        assert M.gap_ms("occasional", -3) > M.gap_ms("occasional", 0) > M.gap_ms("occasional", 3)
        s = MentorState(last_nudge_ms=NOW - 20 * 60_000)
        assert not M.may_nudge("occasional", s, 0, NOW)
        s.receptiveness = 3
        assert M.may_nudge("occasional", s, 0, NOW)


class TestCandidates:
    def test_new_user_gets_the_first_basics_step(self):
        n = M.choose(_journey(), MentorState(), NOW)
        assert n.kind == "frontier" and n.id == "next:b_navigate"

    def test_hard_way_beats_frontier(self):
        n = M.choose(_journey(("toggle_timeline", "palette", 6)), MentorState(), NOW)
        assert n.kind == "hard_way" and n.id == "key:toggle_timeline" and n.action == "toggle_timeline"
        assert "t" in n.text

    def test_phantom_key(self):
        c = M.candidates(_journey(phantom=[("v", 4)]), MentorState(), NOW)
        assert any(n.id == "phantom:v" for n in c)

    def test_never_teaches_what_you_already_do(self):
        j = _journey(("focus_next_session", "key", 5), ("toggle_help", "key", 5),
                     ("command_palette", "key", 2), ("toggle_timeline", "palette", 1))
        ids = {n.id for n in M.candidates(j, MentorState(), NOW)}
        assert not ids & {"next:b_navigate", "next:b_help", "next:b_palette"}

    def test_snoozed_topic_is_skipped(self):
        s = MentorState(snoozed={"next:b_navigate": NOW + DAY})
        assert M.choose(_journey(), s, NOW).id != "next:b_navigate"

    def test_continuity(self):
        j = _journey(("focus_next_session", "key", 3))
        n = M.choose(j, MentorState(last_topic="b_navigate"), NOW)
        assert n.kind == "continuity" and "Basics" in n.text

    def test_fades_to_one_after_a_month(self):
        s = MentorState(first_seen_ms=NOW - 40 * DAY)
        assert len(M.candidates(_journey(("toggle_timeline", "palette", 6), phantom=[("v", 4)]), s, NOW)) == 1


class TestOutcomes:
    def test_engaged(self):
        s = MentorState(receptiveness=0, snoozed={"next:x": NOW + DAY})
        M.record_outcome(s, Nudge("next:x", "t"), "engaged", NOW)
        assert s.receptiveness == 1 and "next:x" not in s.snoozed and s.last_topic == "x"

    def test_ignored_snoozes_a_week_and_expired_snoozes_drop(self):
        s = MentorState(snoozed={"old": NOW - 1})
        M.record_outcome(s, Nudge("key:t", "t"), "ignored", NOW)
        assert s.receptiveness == -0.5 and s.snoozed == {"key:t": NOW + 7 * DAY}

    def test_receptiveness_is_bounded(self):
        s = MentorState(receptiveness=3)
        M.record_outcome(s, Nudge("a", "t"), "engaged", NOW)
        assert s.receptiveness == 3


class TestAchievements:
    def test_first_call_seeds_silently_then_celebrates_once(self):
        s = MentorState()
        polyglot = _journey(("toggle_timeline", "key", 1), ("toggle_timeline", "palette", 1),
                            ("toggle_timeline", "click", 1))
        assert M.new_achievements(polyglot, s) == [] and s.seeded and "a_polyglot" in s.celebrated
        s2 = MentorState(seeded=True)
        assert [c.id for c in M.new_achievements(polyglot, s2)] == ["a_polyglot"]
        assert M.new_achievements(polyglot, s2) == []


class TestState:
    def test_round_trip_and_bad_file(self, tmp_path, monkeypatch):
        path = tmp_path / "js.json"
        monkeypatch.setattr(M, "state_path", lambda: path)
        s = MentorState(receptiveness=1.5, celebrated=["a"], seeded=True)
        M.save_state(s)
        assert M.load_state() == s
        path.write_text("{bad")
        assert M.load_state() == MentorState()
        path.write_text('{"receptiveness": 2, "unknown_field": 1}')
        assert M.load_state().receptiveness == 2

    @pytest.mark.parametrize("text", ["null", "[]", "1", '"x"', "true"])
    def test_state_that_is_not_an_object_gives_defaults(self, tmp_path, monkeypatch, text):
        path = tmp_path / "js.json"
        monkeypatch.setattr(M, "state_path", lambda: path)
        path.write_text(text)
        assert M.load_state() == MentorState()

    def test_fields_of_the_wrong_type_are_dropped(self, tmp_path, monkeypatch):
        path = tmp_path / "js.json"
        monkeypatch.setattr(M, "state_path", lambda: path)
        path.write_text('{"receptiveness": "high", "snoozed": [1], "celebrated": {"a": 1},'
                        ' "last_topic": 5, "seeded": 1, "first_seen_ms": 7,'
                        ' "last_nudge_ms": true}')
        assert M.load_state() == MentorState(first_seen_ms=7.0)
        path.write_text('{"snoozed": {"a": 9, "b": "soon"}, "celebrated": ["x", 3]}')
        s = M.load_state()
        assert s.snoozed == {"a": 9} and s.celebrated == ["x"]
        M.save_state(s)
        assert M.load_state() == s

    def test_tui_starts_with_a_broken_state_file(self, tmp_path, monkeypatch):
        from overcode.tui_actions.mentor import MentorMixin
        monkeypatch.setattr(M, "load_state", MagicMock(side_effect=RuntimeError("boom")))
        host = MentorMixin()
        host._init_mentor()
        assert host._mentor_state == MentorState()


class TestTuiGates:
    def _tui(self, dial="occasional", idle=60, waiting=False, dialog=False):
        from overcode.tui_actions.mentor import MentorMixin
        tui = MagicMock()
        tui._mentor_state = MentorState()
        tui._mentor_shown_this_run = 0
        tui._active_nudge = None
        tui._mentor_busy = False
        tui.attended = True
        tui._last_keypress = time.monotonic() - idle
        tui._any_dialog_visible.return_value = dialog
        tui._command_bar_in_use.return_value = False
        tui._someone_waiting.return_value = waiting
        tui._mentor_tick = MentorMixin._mentor_tick.__get__(tui)
        return tui

    @pytest.fixture(autouse=True)
    def _dial(self, monkeypatch):
        self.dial = "occasional"
        monkeypatch.setattr("overcode.mentor.mentor_dial", lambda: self.dial)

    def test_picks_when_everything_allows(self):
        tui = self._tui()
        tui._mentor_tick()
        tui.run_worker.assert_called_once()

    @pytest.mark.parametrize("kw", [dict(idle=1), dict(waiting=True), dict(dialog=True)])
    def test_holds_back(self, kw):
        tui = self._tui(**kw)
        tui._mentor_tick()
        tui.run_worker.assert_not_called()

    def test_off_by_default(self):
        self.dial = "off"
        tui = self._tui()
        tui._mentor_tick()
        tui.run_worker.assert_not_called()

    def test_tip_times_out_as_ignored(self):
        tui = self._tui()
        tui._active_nudge = Nudge("next:x", "t")
        tui._nudge_shown_at = time.monotonic() - 100
        tui._mentor_tick()
        tui._end_nudge.assert_called_once_with("ignored")

    def test_taking_the_tip_is_engaged(self):
        from overcode.tui_actions.mentor import MentorMixin
        tui = MagicMock()
        tui._active_nudge = Nudge("key:toggle_timeline", "t", action="toggle_timeline")
        MentorMixin.mentor_saw_action(tui, "toggle_timeline", "key")
        tui._end_nudge.assert_called_once_with("engaged")
        tui._end_nudge.reset_mock()
        MentorMixin.mentor_saw_action(tui, "toggle_timeline", "agent")
        tui._end_nudge.assert_not_called()


class TestMentorFooterPilot:
    @pytest.mark.asyncio
    async def test_tip_replaces_the_footer_and_taking_it_restores_it(self):
        from overcode.tui import SupervisorTUI
        app = SupervisorTUI(tmux_session="test-pilot")
        async with app.run_test(size=(150, 40)) as pilot:
            await pilot.pause()
            footer = app.query_one("#help-text")
            normal = str(footer.render())
            app._show_nudge(Nudge("key:toggle_timeline", "Timeline: t does it in one key", "toggle_timeline"))
            await pilot.pause()
            shown = str(footer.render())
            assert "💡" in shown and "t does it in one key" in shown and "u your journey" in shown
            await pilot.press("t")
            await pilot.pause()
            assert app._active_nudge is None
            assert str(footer.render()) == normal
            assert app._mentor_state.receptiveness == 1
