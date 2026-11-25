"""The typing guard's run detector (#560): pure, driven by a list of keys and times."""

import pytest

from overcode.keymap import _CHAR_NAMES
from overcode.typing_guard import (
    MIN_KEYS,
    RUN_GAP,
    TypingRun,
    is_run_key,
    quote,
    starts_immediately,
    typed_text,
    warning_text,
)


def key_of(ch: str):
    """(key, character) as Textual reports a typed character."""
    if ch == " ":
        return "space", " "
    if ch == "\n":
        return "enter", "\r"
    if ch == "\b":
        return "backspace", "\x08"
    return _CHAR_NAMES.get(ch, ch), ch


def feed(text: str, gap: float, ctx=lambda i, ch: "list", start: float = 100.0):
    """Feed ``text`` one character every ``gap`` seconds; the verdicts, and the run."""
    run = TypingRun()
    verdicts = []
    for i, ch in enumerate(text):
        key, char = key_of(ch)
        verdicts.append(run.feed(key, char, ctx(i, ch), start + i * gap))
    return verdicts, run


def typing_at(verdicts):
    """The index of the key that made the run typing, or None."""
    return next((i for i, v in enumerate(verdicts) if v.typing), None)


class TestRuns:

    @pytest.mark.parametrize("gap", [0.08, 0.2, 0.35])
    def test_prose_is_typing(self, gap):
        verdicts, _ = feed("the build is red", gap)
        assert typing_at(verdicts) == MIN_KEYS - 1

    @pytest.mark.parametrize("text", ["jjjj", "jjjjjjjjjjjj", "x x x x", "s s s", "kkkkk"])
    def test_one_key_repeated_never_is(self, text):
        verdicts, _ = feed(text, 0.05)
        assert typing_at(verdicts) is None

    def test_slow_keys_are_no_run(self):
        verdicts, run = feed("jkjkjkjk", RUN_GAP + 0.2)
        assert typing_at(verdicts) is None
        assert all(v.index == 0 for v in verdicts)  # each starts its own run

    def test_a_pause_starts_a_new_run(self):
        run = TypingRun()
        for i, ch in enumerate("ab"):
            run.feed(ch, ch, "list", i * 0.1)
        assert run.feed("c", "c", "list", 0.1 + RUN_GAP + 0.01).index == 0
        assert len(run.keys) == 1

    def test_a_non_typing_key_ends_the_run(self):
        run = TypingRun()
        for i, ch in enumerate("abc"):
            run.feed(ch, ch, "list", i * 0.1)
        assert not run.feed("down", None, "list", 0.35).counted
        assert run.feed("d", "d", "list", 0.4).index == 0

    def test_hi_team_typing_into_help(self):
        """h opens help; help is a place keys count."""
        verdicts, _ = feed("hi team", 0.15, ctx=lambda i, ch: "list" if i == 0 else "help")
        assert typing_at(verdicts) == 3

    def test_i_then_a_fast_instruction_is_deliberate(self):
        """A dialog the run's first key opened takes what is typed into it."""
        verdicts, run = feed("ifix the tests please\n", 0.08,
                             ctx=lambda i, ch: "list" if i == 0 else "command_bar:send")
        assert typing_at(verdicts) is None
        assert verdicts[0].counted and not any(v.counted for v in verdicts[1:])
        assert run.keys == []

    def test_a_dialog_opened_mid_run_counts(self):
        """t toggles the timeline, i opens the bar: the prose after it counts."""
        verdicts, _ = feed("tifor", 0.12,
                           ctx=lambda i, ch: "list" if i < 2 else "command_bar:send")
        assert typing_at(verdicts) == 3

    def test_typing_into_a_dialog_open_before_the_run(self):
        verdicts, _ = feed("hello world", 0.1, ctx=lambda i, ch: "modal:new-agent-modal")
        assert not any(v.counted for v in verdicts)

    def test_jobs_view_counts(self):
        verdicts, _ = feed("ok then", 0.1, ctx=lambda i, ch: "jobs")
        assert typing_at(verdicts) == 3

    def test_typing_is_reported_once(self):
        verdicts, run = feed("the build is red", 0.1)
        assert sum(v.typing for v in verdicts) == 1 and run.typing

    def test_enter_and_backspace_are_typing_keys(self):
        verdicts, _ = feed("ab\b\n", 0.1)
        assert typing_at(verdicts) == 3


class TestKeys:

    @pytest.mark.parametrize("key,char,typed", [
        ("a", "a", True), ("A", "A", True), ("1", "1", True),
        ("exclamation_mark", "!", True), ("space", " ", True), ("enter", "\r", True),
        ("backspace", "\x08", True), ("escape", "\x1b", False), ("down", None, False),
        ("ctrl+z", "\x1a", False), ("ctrl+p", "\x10", False), ("tab", "\t", False),
    ])
    def test_is_run_key(self, key, char, typed):
        assert is_run_key(key, char) is typed

    @pytest.mark.parametrize("key,now", [
        ("1", True), ("5", True), ("enter", True), ("escape", True),
        ("e", False), ("q", False), ("exclamation_mark", False), ("backslash", False),
    ])
    def test_starts_immediately(self, key, now):
        assert starts_immediately(key) is now


class TestConfig:

    @pytest.fixture(autouse=True)
    def _own_config(self, tmp_path, monkeypatch):
        from overcode import config
        monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
        config._clear_config_cache()
        yield
        config._clear_config_cache()

    def _write(self, text):
        from overcode import config
        config.config_path().write_text(text)
        config._clear_config_cache()

    def test_on_by_default(self):
        from overcode import config
        self._write("")
        assert config.get_typing_guard_enabled() is True
        self._write("tui: nonsense\n")
        assert config.get_typing_guard_enabled() is True

    def test_saved_beside_the_rest_of_tui(self):
        import yaml
        from overcode import config
        self._write("tui:\n  other: 1\nsend:\n  prefer_claude_messaging: true\n")
        config.save_typing_guard_enabled(False)
        config._clear_config_cache()
        assert config.get_typing_guard_enabled() is False
        data = yaml.safe_load(config.config_path().read_text())
        assert data["tui"] == {"other": 1, "typing_guard": False}
        assert data["send"] == {"prefer_claude_messaging": True}
        config.save_typing_guard_enabled(True)
        config._clear_config_cache()
        assert config.get_typing_guard_enabled() is True


class TestClassification:

    def test_irreversible_actions_are_palette_commands_without_state(self):
        from overcode.command_palette import (
            COMMANDS_BY_ACTION, IRREVERSIBLE_ACTIONS, IRREVERSIBLE_IN_JOBS, STATELESS_UNDO,
            UNDO_INVERSES)
        for name in IRREVERSIBLE_ACTIONS | IRREVERSIBLE_IN_JOBS:
            assert name in COMMANDS_BY_ACTION, name
            assert COMMANDS_BY_ACTION[name].state is None, name
        for name, inverse in UNDO_INVERSES.items():
            assert inverse in COMMANDS_BY_ACTION, inverse
            assert name in STATELESS_UNDO or COMMANDS_BY_ACTION[name].state is not None
        assert STATELESS_UNDO <= set(UNDO_INVERSES)

    def test_every_bare_default_key_is_reversible_or_classified(self):
        """A bare printable key the default map binds either runs something
        undoable or a dialog, or is a one-shot the guard holds back. This list
        is what is left: navigation, and actions that confirm themselves."""
        from overcode.command_palette import (
            COMMANDS_BY_ACTION, IRREVERSIBLE_ACTIONS, STATELESS_UNDO)
        from overcode.keymap import default_keymap, is_text_key
        confirmed_or_navigation = {
            "focus_next_session", "focus_previous_session", "jump_to_attention",
            "kill_focused", "restart_focused", "sync_to_main_and_clear",
            "resize_focused_window",
        }
        dialogs = {
            "toggle_help", "open_agent_visibility", "focus_command_bar", "focus_standing_orders",
            "focus_human_annotation", "new_agent", "command_palette", "filter_by_tag",
            "add_tag", "choose_sort", "edit_agent_value", "edit_cost_budget",
            "open_billing_codes", "configure_heartbeat", "fork_focused",
            "new_agent_in_worktree", "open_column_config", "open_instruction_history",
        }
        for b in default_keymap().bindings("app"):
            if not is_text_key(b.key):
                continue
            cmd = COMMANDS_BY_ACTION.get(b.action)
            stateful = cmd is not None and cmd.state is not None
            assert (stateful or b.action in IRREVERSIBLE_ACTIONS | STATELESS_UNDO
                    | confirmed_or_navigation | dialogs), (b.key, b.action)


class TestText:

    def test_typed_text(self):
        keys = [key_of(c) for c in "hi tx\bem\n"]
        assert typed_text(keys) == "hi tem⏎"

    def test_quote_truncates(self):
        assert quote("x" * 10, 40) == "x" * 10
        assert quote("hi team, the build is red again and again and again", 20).endswith("…")
        assert len(quote("y" * 100, 20)) == 20

    def test_warning_text(self):
        text = warning_text("hi team, the build is red again on main, sorry",
                            ["Timeline", "Summary detail", "Timeline"], [], ["New shell"],
                            undo_key="Ctrl+Z")
        assert text.startswith('Typing went to overcode — ignored "hi team, the build is red')
        assert "…" in text
        assert "undid Timeline, Summary detail;" in text
        assert "didn't run New shell" in text
        assert text.endswith("Ctrl+Z undoes more.")

    def test_warning_text_failures(self):
        text = warning_text("abcd", [], ["Sleep"], [], undo_key="")
        assert text == 'Typing went to overcode — ignored "abcd"; couldn\'t undo Sleep.'
