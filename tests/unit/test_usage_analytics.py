"""Tests for usage_analytics: the signals folded from the usage log (#483)."""

import pytest

from overcode.usage_analytics import parse_since, render_summary, summarize

BOUND = frozenset({"j", "k", "b", "t", "h", "m", "slash"})


class _Log:
    """Builds one TUI run's records with increasing timestamps."""

    def __init__(self, sid="run1", t0=1_790_000_000_000.0):
        self.sid, self.t, self.records = sid, t0, []

    def add(self, kind, gap_ms=100, **fields):
        self.t += gap_ms
        self.records.append({"kind": kind, "sid": self.sid, "t": self.t, **fields})
        return self

    def key(self, key, ctx="list", gap_ms=100, dt=None):
        return self.add("key", gap_ms, key=key, ctx=ctx, dt=dt if dt is not None else gap_ms)

    def action(self, name, via="key", ok=True, gap_ms=5, **f):
        return self.add("action", gap_ms, action=name, via=via, ok=ok, **f)

    def pressed(self, key, action, gap_ms=100, **f):
        return self.key(key, gap_ms=gap_ms).action(action, **f)


class TestParseSince:
    @pytest.mark.parametrize("text,secs", [("90", 90), ("30m", 1800), ("24h", 86400), ("7d", 604800), ("2w", 1209600)])
    def test_units(self, text, secs):
        assert parse_since(text) == secs

    def test_bad(self):
        with pytest.raises(ValueError):
            parse_since("soon")


class TestActions:
    def test_counts_and_via_share(self):
        log = _Log().pressed("t", "toggle_timeline").pressed("t", "toggle_timeline", gap_ms=5000)
        log.action("toggle_timeline", via="palette", gap_ms=5000)
        log.action("toggle_timeline", via="click", gap_ms=5000)
        s = summarize(log.records, BOUND)
        a = s.actions["toggle_timeline"]
        assert a.uses == 4 and a.user_uses == 4
        assert a.efficient_share == 0.75

    def test_agent_and_auto_uses_are_not_the_users(self):
        log = _Log().action("toggle_timeline", via="agent").action("refresh", via="auto")
        s = summarize(log.records, BOUND)
        assert s.actions["toggle_timeline"].user_uses == 0
        assert s.actions["refresh"].user_uses == 0

    def test_widget_bindings_are_not_tui_actions(self):
        log = _Log().action("cursor_down", ns="TextArea")
        assert "cursor_down" not in summarize(log.records, BOUND).actions

    def test_blocked(self):
        log = _Log().pressed("m", "toggle_preview", ok=False)
        assert summarize(log.records, BOUND).blocked["toggle_preview"] == 1

    def test_cli_counts_as_an_action(self):
        log = _Log().add("cli", cmd="launch", via="cli")
        s = summarize(log.records, BOUND)
        assert s.cli["launch"] == 1 and s.actions["cli:launch"].user_uses == 1


class TestPhantomKeys:
    def test_unbound_list_key_with_no_action_is_a_phantom(self):
        log = _Log().key("v").key("v").pressed("j", "focus_next_session")
        assert dict(summarize(log.records, BOUND).phantom_keys) == {"v": 2}

    def test_bound_key_without_action_is_not_a_phantom(self):
        log = _Log().key("j").key("k")  # e.g. blocked before run_action, or handled in on_key
        assert not summarize(log.records, BOUND).phantom_keys

    def test_keys_in_modals_are_not_phantoms(self):
        log = _Log().key("v", ctx="modal:new-agent-modal")
        assert not summarize(log.records, BOUND).phantom_keys

    def test_trailing_key_is_settled(self):
        log = _Log().pressed("j", "focus_next_session").key("v")
        assert summarize(log.records, BOUND).phantom_keys["v"] == 1


class TestWorkflowSignals:
    def test_walk_of_six_or_more(self):
        log = _Log()
        for _ in range(7):
            log.pressed("j", "focus_next_session")
        log.pressed("b", "jump_to_attention")
        for _ in range(3):
            log.pressed("k", "focus_previous_session")
        s = summarize(log.records, BOUND)
        assert (s.walks, s.walk_keys) == (1, 7)

    def test_toggle_regret_within_three_seconds(self):
        log = _Log().pressed("m", "toggle_preview").pressed("m", "toggle_preview", gap_ms=800)
        log.pressed("m", "toggle_preview", gap_ms=10_000)
        assert summarize(log.records, BOUND).toggle_regret["toggle_preview"] == 1

    def test_help_lookup(self):
        log = _Log().pressed("h", "toggle_help")
        log.add("dialog", name="help", phase="open")
        log.add("dialog", gap_ms=4000, name="help", phase="cancel", dur_ms=4000)
        log.pressed("b", "jump_to_attention", gap_ms=2000)
        log.pressed("t", "toggle_timeline", gap_ms=60_000)
        s = summarize(log.records, BOUND)
        assert dict(s.help_lookups) == {"jump_to_attention": 1}
        assert s.dialogs["help"]["cancel"] == 1 and s.dialog_cancel_ms["help"] == [4000]

    def test_palette_pick_of_an_action_with_a_key(self):
        log = _Log().action("toggle_timeline", via="palette", q="time", rank=0)
        s = summarize(log.records, BOUND, keys_by_action={"toggle_timeline": ["t"]})
        assert s.palette_picks_with_key["toggle_timeline"] == 1

    def test_palette_miss_keeps_the_final_query(self):
        log = _Log()
        for q in ("r", "re", "rep", "repo x"):
            log.add("palette_query", q=q, n=0 if len(q) > 2 else 3, mode="commands")
        assert dict(summarize(log.records, BOUND).palette_misses) == {"repo x": 1}

    def test_hesitation(self):
        log = _Log().key("x", gap_ms=3000, dt=3000).action("kill_focused")
        assert summarize(log.records, BOUND).hesitations["kill_focused"] == 1

    def test_runs_are_folded_separately(self):
        a = _Log(sid="a")
        b = _Log(sid="b")
        for _ in range(4):
            a.pressed("j", "focus_next_session")
            b.pressed("j", "focus_next_session")
        records = [r for pair in zip(a.records, b.records) for r in pair]
        assert summarize(records, BOUND).walks == 0  # 4 + 4 interleaved is not a walk of 8


class TestRender:
    def test_empty(self):
        assert "Nothing recorded yet" in render_summary(summarize([], BOUND))

    def test_sections_and_to_dict(self):
        log = _Log().add("tui", phase="start").key("v").pressed("j", "focus_next_session")
        s = summarize(log.records, BOUND)
        text = render_summary(s)
        assert "Most used" in text and "Keys pressed that do nothing" in text and "experimental" in text
        d = s.to_dict()
        assert d["tui_runs"] == 1 and d["experimental"]["phantom_keys"] == {"v": 1}
