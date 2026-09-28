"""Tests for the learning journey (#483): mastery, catalog, curriculum, and the `u` panel."""

import inspect
import re

import pytest

from overcode import journey as J
from overcode.journey import (
    COMPETENCIES,
    Residue,
    compute_journey,
    mastery_for,
    render_journey,
)
from overcode.usage_analytics import summarize

NOW = 1_790_000_000_000.0
DAY = 86_400_000


def _keymap():
    from overcode.command_palette import keys_by_action
    from overcode.tui import SupervisorTUI
    return keys_by_action(SupervisorTUI.BINDINGS)


def _actions(*spec):
    """spec: (action, via, n) → records in one run."""
    recs, t = [], NOW - 1000
    for action, via, n in spec:
        for _ in range(n):
            t += 10
            recs.append({"kind": "action", "sid": "s", "t": t, "action": action, "via": via, "ok": True})
    return recs


def _journey(*spec, residue=None, now=NOW, **kw):
    return compute_journey(summarize(_actions(*spec)), residue or Residue(), _keymap(), now_ms=now, **kw)


class TestMastery:
    @pytest.mark.parametrize("uses,share,has_key,level", [
        (0, 0, True, "unaware"),
        (1, 1, True, "tried"),
        (3, 1, True, "fluent"),
        (5, 0.2, True, "habitual"),
        (5, 0, False, "habitual"),
        (8, 0, False, "fluent"),
    ])
    def test_ladder(self, uses, share, has_key, level):
        assert mastery_for(uses, share, NOW, has_key, NOW).level == level

    def test_seen(self):
        assert mastery_for(0, 0, None, True, NOW, seen=True).level == "seen"

    def test_decay_one_rung_after_grace_never_below_tried(self):
        fluent_last_used = NOW - 31 * DAY
        assert mastery_for(8, 1, fluent_last_used, True, NOW).level == "habitual"
        assert mastery_for(8, 1, NOW - 500 * DAY, True, NOW).level == "tried"
        assert mastery_for(8, 1, NOW - 29 * DAY, True, NOW).level == "fluent"

    def test_decay_is_configurable(self):
        assert mastery_for(8, 1, NOW - 10 * DAY, True, NOW, grace_days=5, step_days=100).level == "habitual"

    def test_hard_way(self):
        assert mastery_for(6, 0.1, NOW, True, NOW).hard_way
        assert not mastery_for(6, 0.9, NOW, True, NOW).hard_way
        assert not mastery_for(6, 0.0, NOW, False, NOW).hard_way


class TestCatalog:
    def test_every_palette_command_is_a_capability(self):
        from overcode.command_palette import COMMANDS
        ids = {c.id for c in J.build_catalog(_keymap())}
        assert {c.action for c in COMMANDS} <= ids

    def test_mapping_tables_name_real_actions(self):
        from overcode.command_palette import COMMANDS
        actions = {c.action for c in COMMANDS}
        assert set(J.RESIDUE_FOR_ACTION) <= actions
        assert set(J.EXTRA_ACTIONS) <= actions
        fields = set(Residue.__dataclass_fields__)
        assert set(J.RESIDUE_FOR_ACTION.values()) <= fields
        residues = {c.residue for c in J.EXTRA_CAPABILITIES if c.residue}
        assert {r for r in residues if not r.startswith("backend:")} <= fields
        from overcode.backends import list_backends
        assert {r.split(":", 1)[1] for r in residues if r.startswith("backend:")} <= set(list_backends())

    def test_competencies_name_real_things(self):
        """Every id a criterion counts is a capability or a TUI action; every try_action runs."""
        from overcode.tui import SupervisorTUI
        cap_ids = {c.id for c in J.build_catalog(_keymap())}
        src = inspect.getsource(J)
        block = src[src.index("COMPETENCIES = ("):src.index("# ── the journey")]
        named = set()
        for args in re.findall(r"s\.uses\(([^)]*)\)", block):
            named |= set(re.findall(r'"([a-z_]+)"', args))
        named |= set(J.APPROVE)
        unknown = {n for n in named if n not in cap_ids and not hasattr(SupervisorTUI, f"action_{n}")}
        assert unknown == set()
        for c in COMPETENCIES:
            if c.try_action:
                assert hasattr(SupervisorTUI, f"action_{c.try_action}"), c.try_action
        ids = {c.id for c in COMPETENCIES}
        for c in COMPETENCIES:
            assert set(c.prereqs) <= ids, c.id
        assert len(ids) == len(COMPETENCIES)

    def test_untracked_capability_stays_honest(self):
        j = _journey()
        cap = next(c for c in j.catalog if not c.tracked)
        assert j.to_dict()["capabilities"][cap.id]["tracked"] is False


class TestCurriculum:
    def test_empty_log_has_a_frontier_and_nothing_earned(self):
        j = _journey()
        basics = next(t for t in j.tracks if t.id == "basics")
        assert basics.level == 0 and basics.frontier
        assert any(c.id == "b_send" for c in basics.locked)  # needs b_navigate

    def test_earning_unlocks_the_next_step(self):
        j = _journey(("focus_next_session", "key", 3))
        basics = next(t for t in j.tracks if t.id == "basics")
        assert "b_navigate" in j.earned_ids
        assert any(c.id == "b_send" for c in basics.frontier + basics.upcoming)

    def test_competency_can_be_earned_before_its_prereq(self):
        j = _journey(("open_column_config", "key", 1))
        assert "o_columns" in j.earned_ids and "o_detail" not in j.earned_ids

    def test_residue_counts(self):
        j = _journey(residue=Residue(child_of_agent=2, live_agents=6, standing_orders=1, agents=3))
        assert {"x_children", "a_fleet", "f_new"} <= j.earned_ids
        assert j.mastery["focus_standing_orders"].level == "tried"

    def test_agent_driven_use_does_not_count(self):
        j = _journey(("toggle_timeline", "agent", 10))
        assert "o_timeline" not in j.earned_ids

    def test_palette_counts_via_the_palette_key_action(self):
        recs = _actions(("command_palette", "key", 1), ("toggle_timeline", "palette", 1))
        j = compute_journey(summarize(recs), Residue(), _keymap(), now_ms=NOW)
        assert "b_palette" in j.earned_ids

    def test_hard_way_lists_keyed_actions_done_slowly(self):
        j = _journey(("toggle_timeline", "palette", 5), ("toggle_timeline", "click", 5))
        assert "toggle_timeline" in j.hard_way

    def test_achievements(self):
        j = _journey(("toggle_timeline", "key", 1), ("toggle_timeline", "palette", 1),
                     ("toggle_timeline", "click", 1))
        earned = {c.id for c, e in j.achievements if e}
        assert "a_polyglot" in earned

    def test_core_only_counts_toward_the_level(self):
        j = _journey(("cli:view columns", "cli", 1))
        orch = next(t for t in j.tracks if t.id == "orchestration")
        assert "x_scripting" in j.earned_ids and orch.level == 0


class TestOutput:
    def test_render_and_dict(self):
        j = _journey(("focus_next_session", "key", 3), ("toggle_timeline", "palette", 5))
        text = render_journey(j)
        assert "Basics" in text and "✓ Move between agents" in text and "The hard way" in text
        d = j.to_dict()
        assert d["tracks"][0]["id"] == "basics" and d["hard_way"][0]["id"] == "toggle_timeline"
        assert d["capabilities"]["toggle_timeline"]["level"] in ("habitual", "fluent")


class TestJourneyPanelPilot:
    @pytest.mark.asyncio
    async def test_u_opens_it_and_t_tries_the_next_step(self, tmp_path, monkeypatch):
        from overcode.tui import SupervisorTUI
        from overcode.journey import Journey
        monkeypatch.setattr("overcode.journey.gather_residue", lambda: Residue())
        app = SupervisorTUI(tmux_session="test-pilot")
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            await pilot.press("u")
            for _ in range(20):
                await pilot.pause(0.05)
                if app.query_one("#journey-panel").has_class("visible"):
                    break
            panel = app.query_one("#journey-panel")
            assert panel.has_class("visible")
            assert isinstance(panel._journey, Journey)
            rendered = panel.render().plain
            assert "Basics" in rendered and "Capabilities" in rendered
            # The first track with a next step is Basics; its first try_action is focus_next_session.
            calls = []
            app.action_focus_next_session = lambda: calls.append(1)
            await pilot.press("t")
            await pilot.pause()
            assert not panel.has_class("visible")
            assert calls == [1]

    @pytest.mark.asyncio
    async def test_esc_closes(self, tmp_path, monkeypatch):
        from overcode.tui import SupervisorTUI
        monkeypatch.setattr("overcode.journey.gather_residue", lambda: Residue())
        app = SupervisorTUI(tmux_session="test-pilot")
        async with app.run_test(size=(140, 50)) as pilot:
            await pilot.pause()
            app.action_open_journey()
            for _ in range(20):
                await pilot.pause(0.05)
                if app.query_one("#journey-panel").has_class("visible"):
                    break
            await pilot.press("escape")
            await pilot.pause()
            assert not app.query_one("#journey-panel").has_class("visible")


class TestArchiveResidue:
    """The archive is read incrementally: 20k sessions must not be rebuilt per `u`."""

    def _sm(self, tmp_path, monkeypatch):
        from overcode.session_manager import SessionManager
        monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path))
        return SessionManager(skip_git_detection=True)

    def _archive(self, sm, name, **fields):
        s = sm.create_session(name=name, tmux_session="agents", tmux_window=name, command=["claude"])
        if fields:
            sm.update_session(s.id, **fields)
        sm.delete_session(s.id, archive=True)

    def test_counts_follow_appends_and_parse_only_new_lines(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        self._archive(sm, "a", parent_session_id="p", tags=["x"])
        self._archive(sm, "b", standing_instructions="be brief")
        counts, tags, backends, _, _ = J._archive_counts(sm)
        assert counts["agents"] == 2 and counts["child_of_agent"] == 1
        assert counts["standing_orders"] == 1 and tags == {"x"} and backends == {"claude-code"}

        seen = []
        real = type(sm).archived_sessions_since

        def spy(self, offset=0, inode=None):
            out = real(self, offset, inode)
            seen.append(len(out[0]))
            return out

        monkeypatch.setattr(type(sm), "archived_sessions_since", spy)
        self._archive(sm, "c", tags=["y"])
        counts, tags, *_ = J._archive_counts(sm)
        assert counts["agents"] == 3 and tags == {"x", "y"}
        assert J._archive_counts(sm)[0]["agents"] == 3
        assert seen == [1, 0]  # only the appended session, then nothing

    def test_a_replaced_archive_is_recounted(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        self._archive(sm, "a")
        self._archive(sm, "b")
        assert J._archive_counts(sm)[0]["agents"] == 2
        lines = sm.archive_file.read_bytes().splitlines(keepends=True)
        tmp = sm.archive_file.with_name("new.jsonl")
        tmp.write_bytes(lines[0])
        tmp.replace(sm.archive_file)
        assert J._archive_counts(sm)[0]["agents"] == 1

    def test_a_bad_cache_is_ignored(self, tmp_path, monkeypatch):
        sm = self._sm(tmp_path, monkeypatch)
        self._archive(sm, "a")
        (sm.state_dir / "archive_residue.json").write_text('{"v": 1, "counts": []}')
        assert J._archive_counts(sm)[0]["agents"] == 1


class TestBackendsAndSkills:
    """Backends counted per CLI; skill profiles as their own track (#499)."""

    def test_overagent_and_shell_rows_are_not_a_second_agent_cli(self):
        j = _journey(residue=Residue(agents=3, backends=3, agent_clis=1))
        assert "x_backends" not in j.earned_ids
        j = _journey(residue=Residue(agents=3, agent_clis=2))
        assert "x_backends" in j.earned_ids

    def test_agents_per_backend_are_the_uses(self):
        j = _journey(residue=Residue(backend_agents={"codex": 9, "opencode": 1}))
        assert j.mastery["backend_codex"].level == "fluent"
        assert j.mastery["backend_opencode"].level == "tried"
        assert j.mastery["backend_grok"].level == "unaware"

    def test_skills_track(self):
        j = _journey(residue=Residue(agents=2, skill_profiles=3, profiled_agents=2, folder_pins=1,
                                     library_skills=4, agent_clis=2, mixed_profiles=1))
        assert {"s_library", "s_profile", "s_launch", "s_pin", "s_several", "s_mixed"} <= j.earned_ids
        skills = next(t for t in j.tracks if t.id == "skills")
        assert skills.level == skills.core_total
        assert j.mastery["skill_profile_launch"].uses == 2

    def test_skills_track_starts_at_the_dialog(self):
        j = _journey(("open_skills", "key", 1))
        assert "s_dialog" in j.earned_ids
        skills = next(t for t in j.tracks if t.id == "skills")
        assert any(c.id == "s_launch" for c in skills.locked)  # needs a profile first

    def test_skill_achievements(self):
        earned = _journey(residue=Residue(skill_profiles=5, agent_clis=3, library_skills=2,
                                          personal_skills=0)).earned_ids
        assert {"a_profiles", "a_three_clis", "a_nothing_always_on"} <= earned
        assert "a_nothing_always_on" not in _journey(
            residue=Residue(library_skills=2, personal_skills=1)).earned_ids

    def test_counting_sessions_by_backend_and_profile(self):
        class S:
            def __init__(self, backend, profile=None):
                self.backend, self.skill_profile = backend, profile
                self.parent_session_id = self.standing_instructions = self.cost_budget_usd = None
                self.wrapper = self.human_annotation = None
                self.heartbeat_enabled, self.agent_value, self.tags = False, 1000, []
        counts = dict.fromkeys(J._SESSION_COUNTERS, 0)
        tags, backends, per_backend, pairs = set(), set(), {}, set()
        J._count_sessions([S("claude-code", "research"), S("opencode", "research"),
                           S("overagent"), S("shell"), S("codex")],
                          counts, tags, backends, per_backend, pairs)
        assert per_backend == {"claude-code": 1, "opencode": 1, "overagent": 1, "shell": 1, "codex": 1}
        assert counts["shells"] == 1 and counts["profiled_agents"] == 2
        assert pairs == {"research\tclaude-code", "research\topencode"}

    def test_gathered_residue_reads_skills_config(self, tmp_path, monkeypatch):
        from overcode import skill_library as sl
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("OVERCODE_DIR", str(tmp_path / ".overcode"))
        (tmp_path / ".overcode" / "skills" / "x").mkdir(parents=True)
        (tmp_path / ".overcode" / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\n")
        sl.save_profile("a", ["x"])
        sl.save_profile("b", [])
        sl.pin_folder(str(tmp_path), "a")
        r = Residue()
        J._skills_residue(r)
        assert (r.skill_profiles, r.folder_pins, r.library_skills, r.personal_skills) == (2, 1, 1, 0)
