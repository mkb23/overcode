"""Tests for configurable keybindings (#510): presets, merging, validation,
the vscode preset, the `overcode keys` CLI, and the keymap reaching help,
palette and the running TUI."""

import re
import json
from unittest.mock import patch

import pytest
from rich.cells import cell_len

from overcode import keymap as km_mod
from overcode.keymap import (
    SCOPES, KeyMap, build_keymap, default_entries, default_scope_bindings,
    effective_keymap, is_text_key, list_presets, load_preset, normalize_key,
    parse_keys, parse_layer, preset_from_dict, terminal_findings,
)


def _km(section=None, preset=None, passthru=None, tmux=None) -> KeyMap:
    return effective_keymap(preset=preset, section=section or {}, passthru=passthru or {},
                            tmux_toggle_key=tmux or "Tab")


# ---------------------------------------------------------------------------
# Key names
# ---------------------------------------------------------------------------

class TestKeyNames:

    @pytest.mark.parametrize("written, name", [
        ("ctrl+p", "ctrl+p"), ("Ctrl+P", "ctrl+p"), ("^P", "ctrl+p"), ("^p", "ctrl+p"),
        ("C-Space", "ctrl+space"), ("M-j", "alt+j"), ("ctrl+shift+P", "ctrl+shift+p"),
        ("?", "question_mark"), ("@", "at"), ("/", "slash"), ("J", "J"), ("j", "j"),
        ("Esc", "escape"), ("PgUp", "pageup"), ("question_mark", "question_mark"),
        ("ctrl++", "ctrl+plus"), ("f2", "f2"),
    ])
    def test_normalize(self, written, name):
        assert normalize_key(written) == name

    def test_parse_keys_forms(self):
        assert parse_keys(None) == []
        assert parse_keys([]) == []
        assert parse_keys("ctrl+j") == ["ctrl+j"]
        assert parse_keys("ctrl+j, J") == ["ctrl+j", "J"]
        assert parse_keys(["^J", "J", "J"]) == ["ctrl+j", "J"]
        assert parse_keys(",") == ["comma"]

    def test_text_keys(self):
        assert is_text_key("a") and is_text_key("J") and is_text_key("slash")
        assert not is_text_key("ctrl+a") and not is_text_key("down") and not is_text_key("f5")


# ---------------------------------------------------------------------------
# Presets and merging
# ---------------------------------------------------------------------------

class TestPresets:

    def test_shipped_presets(self):
        names = list_presets()
        assert names[0] == "default"
        assert "vscode" in names

    def test_presets_ship_as_package_data(self):
        import tomllib
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        data = tomllib.loads((root / "pyproject.toml").read_text())
        assert "data/keymaps/*.yaml" in data["tool"]["setuptools"]["package-data"]["overcode"]

    def test_unknown_preset_raises_but_effective_warns(self):
        with pytest.raises(KeyError):
            load_preset("nope")
        km = _km({"preset": "nope"})
        assert km.preset == "default"
        assert any("unknown key preset 'nope'" in w for w in km.warnings)

    def test_default_preset_is_the_code(self):
        """The default preset changes nothing: app keys == SupervisorTUI.BINDINGS."""
        from overcode.tui import SupervisorTUI
        km = _km()
        assert km.preset == "default"
        assert km.as_tuples("app") == [tuple(b) for b in SupervisorTUI.BINDINGS]
        assert all(b.source == "default" for s in SCOPES for b in km.bindings(s))
        assert km.warnings == []

    def test_default_yaml_repeats_no_keys(self):
        """No duplication of BINDINGS: default.yaml is an empty delta."""
        p = load_preset("default")
        assert not any(p.scopes.values())


class TestMerge:

    DEFAULTS = {"app": [("a", "alpha", "Alpha"), ("b", "beta", "Beta"),
                        ("c", "gamma", "Gamma"), ("down", "beta", "Beta")]}

    def _build(self, preset=None, user=None, **kw):
        p = preset_from_dict("p", preset) if preset is not None else None
        u, w = parse_layer(user or {}, "config keys")
        return build_keymap(self.DEFAULTS, p, u, user_warnings=w, **kw)

    def test_override_replaces_all_keys_in_place(self):
        km = self._build(user={"overrides": {"beta": ["x", "y"]}})
        assert [(b.key, b.action) for b in km.bindings()] == [
            ("a", "alpha"), ("x", "beta"), ("y", "beta"), ("c", "gamma")]
        assert km.source_of("beta") == "override"
        assert km.source_of("alpha") == "default"

    def test_null_unbinds(self):
        km = self._build(user={"overrides": {"gamma": None}})
        assert km.keys_for("gamma") == []
        assert km.action_for("c") is None

    def test_single_key_string(self):
        assert self._build(user={"overrides": {"alpha": "^A"}}).keys_for("alpha") == ["ctrl+a"]

    def test_user_wins_over_preset(self):
        km = self._build(preset={"overrides": {"alpha": ["p"]}},
                         user={"overrides": {"alpha": ["u"]}})
        assert km.keys_for("alpha") == ["u"] and km.source_of("alpha") == "override"
        km = self._build(preset={"overrides": {"alpha": ["p"]}})
        assert km.keys_for("alpha") == ["p"] and km.source_of("alpha") == "p"

    def test_extra_actions_can_be_bound(self):
        km = self._build(user={"overrides": {"delta": ["d"]}},
                         extra_actions={"app": {"delta": "Delta"}})
        assert km.keys_for("delta") == ["d"]
        assert km.bindings()[-1].description == "Delta"
        assert km.warnings == []


class TestValidation:

    DEFAULTS = TestMerge.DEFAULTS

    def _warnings(self, user=None, preset=None, passthru=None):
        return TestMerge._build(self, preset=preset, user=user, passthru=passthru).warnings

    def test_unknown_action(self):
        assert any("unknown action 'nope'" in w for w in self._warnings({"overrides": {"nope": "z"}}))

    def test_unknown_scope(self):
        assert any("unknown scope 'bogus'" in w
                   for w in self._warnings({"scopes": {"bogus": {"x": "y"}}}))

    def test_bad_key(self):
        assert any("not a key name" in w for w in self._warnings({"overrides": {"alpha": "a b"}}))
        assert any("unknown modifier" in w for w in self._warnings({"overrides": {"alpha": "hyper+a"}}))

    def test_duplicate_key_in_scope(self):
        ws = self._warnings({"overrides": {"alpha": "b"}})
        assert any("b is bound to alpha and beta" in w or "b is bound to beta and alpha" in w for w in ws)

    def test_forbidden_key(self):
        ws = self._warnings({"overrides": {"alpha": "ctrl+p"}},
                            preset={"forbidden": {"ctrl+p": "Quick Open"}})
        assert any("avoided by the 'p' preset: Quick Open" in w for w in ws)

    def test_passthru_shadowing(self):
        ws = self._warnings({"overrides": {"alpha": "enter"}}, passthru={"enter": "enter"})
        assert any("passthru key" in w and "alpha" in w for w in ws)

    def test_typing_key_in_text_scope(self):
        km = build_keymap({"command_palette": [("down", "cursor_down", "")]}, None,
                          {"command_palette": {"cursor_down": ["j"]}})
        assert any("typing key" in w for w in km.warnings)

    def test_never_raises_on_garbage(self):
        for section in ("nonsense", 42, {"overrides": "x"}, {"scopes": ["x"]},
                        {"overrides": {"alpha": 7}}):
            km = effective_keymap(section=section, passthru={}, tmux_toggle_key="Tab")
            assert isinstance(km, KeyMap)

    def test_real_config_passthru_default_has_no_conflicts(self):
        from overcode.config import DEFAULT_PASSTHRU_KEYS
        assert _km(passthru=dict(DEFAULT_PASSTHRU_KEYS)).warnings == []
        assert _km(preset="vscode", passthru=dict(DEFAULT_PASSTHRU_KEYS)).warnings == []


# ---------------------------------------------------------------------------
# The vscode preset
# ---------------------------------------------------------------------------

class TestVSCodePreset:

    def test_every_action_exists(self):
        p = load_preset("vscode")
        defaults = default_scope_bindings()
        from overcode.command_palette import COMMANDS
        for scope, actions in p.scopes.items():
            known = {b.action for b in defaults[scope]}
            if scope == "app":
                known |= {c.action for c in COMMANDS}
            assert set(actions) <= known, (scope, set(actions) - known)
        assert p.warnings == ()

    @pytest.mark.parametrize("preset", ["default", "vscode"])
    def test_no_duplicate_keys_per_scope(self, preset):
        km = _km(preset=preset)
        for scope in SCOPES:
            keys = [b.key for b in km.bindings(scope)]
            dupes = {k for k in keys if keys.count(k) > 1}
            assert not dupes, (preset, scope, dupes)

    def test_binds_none_of_its_forbidden_keys(self):
        p = load_preset("vscode")
        km = _km(preset="vscode")
        assert {"ctrl+p", "ctrl+k", "ctrl+j", "ctrl+e", "ctrl+g", "ctrl+space", "f1"} <= set(p.forbidden)
        used = {b.key for s in SCOPES for b in km.bindings(s)}
        assert used & set(p.forbidden) == set()
        assert km.warnings == []

    def test_every_default_action_stays_reachable(self):
        """Remapping never drops an action: everything bound by default is
        still bound under vscode."""
        d, v = _km(), _km(preset="vscode")
        for scope in SCOPES:
            for action in d.raw_keys_by_action(scope):
                assert v.keys_for(action, scope), (scope, action)

    def test_moves_the_swallowed_main_screen_keys(self):
        km = _km(preset="vscode")
        assert km.keys_for("jump_to_agent") == ["space"]
        assert km.keys_for("open_passthru_config") == ["circumflex_accent"]
        assert km.keys_for("cursor_up", "command_palette") == ["up"]
        assert km.keys_for("toggle_expand", "command_bar") == ["ctrl+l"]

    def test_recommends_a_tmux_toggle_key(self):
        p = load_preset("vscode")
        assert p.tmux_toggle_key == "Tab"
        assert "C-Space" in p.tmux_toggle_avoid
        km = _km(preset="vscode", tmux="C-Space")
        assert any("tmux toggle key C-Space" in w for w in km.warnings)


# ---------------------------------------------------------------------------
# Terminal advice, config writes, CLI
# ---------------------------------------------------------------------------

class TestTerminalFindings:

    def test_outside_vscode_nothing(self):
        assert terminal_findings({"TERM_PROGRAM": "iTerm.app"}, "default") == []

    def test_vscode_default_suggests_preset(self):
        out = terminal_findings({"TERM_PROGRAM": "vscode"}, "default")
        assert len(out) == 1
        assert "overcode keys --use vscode" in out[0] and "commandsToSkipShell" in out[0]

    def test_vscode_preset_quiet(self):
        assert terminal_findings({"TERM_PROGRAM": "vscode"}, "vscode") == []

    def test_ctrl_space_toggle(self):
        out = terminal_findings({"TERM_PROGRAM": "vscode"}, "vscode", tmux_toggle_key="C-Space")
        assert any("Ctrl+Space" in f for f in out)


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    import overcode.config as config
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    config._clear_config_cache()
    # Undo the autouse stub: these tests read the real (temporary) config.
    monkeypatch.setattr(km_mod, "user_keys_config",
                        lambda: config._get_config_value("keys", {}))
    yield path
    config._clear_config_cache()


class TestConfig:

    def test_set_preset_round_trip(self, tmp_config):
        import yaml
        tmp_config.write_text("tmux:\n  toggle_key: Tab\n")
        km_mod.set_configured_preset("vscode")
        data = yaml.safe_load(tmp_config.read_text())
        assert data["keys"] == {"preset": "vscode"}
        assert data["tmux"] == {"toggle_key": "Tab"}  # untouched
        assert effective_keymap().preset == "vscode"
        km_mod.set_configured_preset("default")
        assert "keys" not in yaml.safe_load(tmp_config.read_text())

    def test_set_unknown_preset_raises(self, tmp_config):
        with pytest.raises(KeyError):
            km_mod.set_configured_preset("nope")

    def test_config_overrides_and_scopes(self, tmp_config):
        tmp_config.write_text(
            "keys:\n  preset: vscode\n  overrides:\n    jump_to_agent: [ctrl+j, J]\n"
            "    toggle_monochrome: null\n  scopes:\n    command_palette:\n"
            "      cursor_down: [down, ctrl+n]\n")
        km = effective_keymap(passthru={}, tmux_toggle_key="Tab")
        assert km.keys_for("jump_to_agent") == ["ctrl+j", "J"]
        assert km.keys_for("toggle_monochrome") == []
        assert km.keys_for("cursor_down", "command_palette") == ["down", "ctrl+n"]
        # ctrl+j is swallowed on Linux/Windows VSCode; J is toggle_tui_mode
        assert any("ctrl+j" in w.lower() or "^J" in w for w in km.warnings)
        assert any("J is bound to" in w for w in km.warnings)


class TestKeysCLI:

    def _run(self, *args):
        from typer.testing import CliRunner
        from overcode.cli import app
        return CliRunner().invoke(app, ["keys", *args])

    def test_json_shows_sources(self, tmp_config):
        r = self._run("--preset", "vscode", "--json", "--scope", "app")
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["preset"] == "vscode"
        rows = {row["action"]: row for row in data["scopes"]["app"]}
        assert rows["jump_to_agent"]["keys"] == ["Space"]
        assert rows["jump_to_agent"]["source"] == "vscode"
        assert rows["quit"]["source"] == "default"

    def test_conflicts(self, tmp_config):
        tmp_config.write_text("keys:\n  overrides:\n    quit: j\n")
        r = self._run("--conflicts")
        assert r.exit_code == 0
        assert "j is bound to" in r.output

    def test_use_writes_config(self, tmp_config):
        r = self._run("--use", "vscode")
        assert r.exit_code == 0, r.output
        assert "vscode" in r.output
        assert "preset: vscode" in tmp_config.read_text()

    def test_unknown_preset(self, tmp_config):
        assert self._run("--use", "nope").exit_code == 1
        assert self._run("--preset", "nope").exit_code == 1

    def test_presets_list(self, tmp_config):
        r = self._run("--presets")
        assert "default" in r.output and "vscode" in r.output


# ---------------------------------------------------------------------------
# Help overlay
# ---------------------------------------------------------------------------

class TestHelpLayout:

    @pytest.mark.parametrize("width, cols", [(50, 1), (80, 1), (120, 2), (149, 2), (160, 3), (220, 3)])
    def test_columns_by_width(self, width, cols):
        from overcode.tui_widgets.help_overlay import help_columns
        assert help_columns(width) == cols

    @pytest.mark.parametrize("width", [40, 60, 80, 120, 160, 220])
    def test_lines_fit_width(self, width):
        from overcode.tui_widgets.help_overlay import build_help
        text = build_help(width, _km())
        for line in text.split("\n"):
            assert cell_len(line.plain) <= width, (width, line.plain)

    def test_three_columns_put_sections_side_by_side(self):
        from overcode.tui_widgets.help_overlay import build_help
        lines = build_help(170, _km()).plain.split("\n")
        heads = [l for l in lines if l.count("───") >= 3]
        assert heads, "expected a line with three section rules"

    def test_one_column_is_taller_than_three(self):
        from overcode.tui_widgets.help_overlay import build_help
        assert (len(build_help(80, _km()).plain.split("\n"))
                > 2 * len(build_help(170, _km()).plain.split("\n")))

    def test_compact_leaves_out_unbound(self):
        from overcode.tui_widgets.help_overlay import build_help
        wide, narrow = build_help(120, _km()).plain, build_help(50, _km()).plain
        assert "Reverse sort" in wide          # palette-only: shown dim with ·
        assert "Reverse sort" not in narrow

    def test_every_bound_action_appears(self):
        from overcode.command_palette import COMMANDS
        from overcode.tui_widgets.help_overlay import build_help
        km = _km()
        text = build_help(200, km).plain
        titles = {c.action: c.title for c in COMMANDS}
        for b in km.bindings("app"):
            if b.action.startswith("send_") and b.action[5].isdigit():
                continue  # collapsed into "1-5"
            assert titles.get(b.action, b.description) in text, b.action
        assert "1-5" in text

    def test_groups_follow_palette_categories(self):
        from overcode.command_palette import CATEGORIES
        from overcode.tui_widgets.help_overlay import key_sections
        titles = [s.title for s in key_sections(_km())]
        cats = [c.upper() for c in CATEGORIES]
        assert [t for t in titles if t in cats] == cats

    def test_help_shows_remapped_key(self):
        from overcode.tui_widgets.help_overlay import build_help
        default = build_help(120, _km()).plain
        vscode = build_help(120, _km(preset="vscode")).plain
        # The key column is as wide as its longest entry (j/↓/M-j)
        assert re.search(r"\^P +Jump to agent", default)
        assert re.search(r"Space +Jump to agent", vscode)
        assert "^P" not in vscode.split("COMMAND BAR")[0]
        assert "keys: vscode preset" in vscode

    def test_user_override_shown(self):
        from overcode.tui_widgets.help_overlay import build_help
        km = _km({"overrides": {"toggle_monochrome": "F7"}})
        text = build_help(120, km).plain
        assert "f7" in text.lower() and "Monochrome" in text

    def test_status_reference_kept(self):
        from overcode.tui_widgets.help_overlay import build_help
        text = build_help(160, _km()).plain
        for s in ("AGENT STATUSES", "SPECIAL INDICATORS", "SKILL EMOJI", "TOOL EMOJI",
                  "TIMELINE LEGEND", "Waiting (user)", "Standing orders active"):
            assert s in text


# ---------------------------------------------------------------------------
# Through the real TUI
# ---------------------------------------------------------------------------

@pytest.fixture
def vscode_tui(tmp_path, monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("OVERCODE_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(km_mod, "user_keys_config", lambda: {"preset": "vscode"})
    from tests.unit.test_command_palette import _sessions
    from overcode.tui import SupervisorTUI
    with patch.object(SupervisorTUI, "_ensure_monitor_daemon", lambda self: None), \
         patch("overcode.launcher.AgentLauncher.list_sessions", lambda self: _sessions()):
        yield SupervisorTUI(tmux_session="test")


@pytest.mark.asyncio
class TestKeymapInTUI:

    async def test_remapped_key_runs_the_action(self, vscode_tui):
        from tests.unit.test_command_palette import _palette, _ready
        tui = vscode_tui
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            assert tui.keymap.preset == "vscode"
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert not _palette(tui).has_class("visible")
            await pilot.press("space")
            await pilot.pause()
            assert _palette(tui).has_class("visible") and _palette(tui).mode == "agents"

    async def test_palette_shows_remapped_key(self, vscode_tui):
        from tests.unit.test_command_palette import _palette, _ready
        tui = vscode_tui
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("slash", *"rename")
            await pilot.pause()
            palette = _palette(tui)
            assert palette._keymap["rename_focused"] == ["N"]
            assert palette._keymap["jump_to_agent"] == ["Space"]
            row = palette.selected_row
            assert row.match.command.action == "rename_focused"

    async def test_palette_nav_uses_scope_keys(self, vscode_tui):
        from tests.unit.test_command_palette import _palette, _ready
        tui = vscode_tui
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            await pilot.press("slash")
            await pilot.pause()
            palette = _palette(tui)
            start = palette.selected_index
            await pilot.press("ctrl+n")  # not a nav key under vscode
            assert palette.selected_index == start
            await pilot.press("down")
            assert palette.selected_index == start + 1

    async def test_footer_and_help_follow_the_keymap(self, vscode_tui):
        from tests.unit.test_command_palette import _ready
        tui = vscode_tui
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            assert "Space Jump to agent" in tui._build_footer_text().plain
            await pilot.press("h")
            await pilot.pause()
            overlay = tui.query_one("#help-overlay")
            assert overlay.has_class("visible")
            body = overlay.query_one("#help-body")
            assert re.search(r"Space +Jump to agent", body.render().plain)

    async def test_cycle_preset_rebinds_live(self, vscode_tui, monkeypatch):
        from tests.unit.test_command_palette import _palette, _ready
        tui = vscode_tui
        saved = []
        monkeypatch.setattr(km_mod, "set_configured_preset", saved.append)
        monkeypatch.setattr(km_mod, "user_keys_config", lambda: {})
        async with tui.run_test(size=(120, 40)) as pilot:
            await _ready(pilot)
            tui.action_cycle_key_preset()  # vscode → default
            await pilot.pause()
            assert saved == ["default"] and tui.keymap.preset == "default"
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert _palette(tui).has_class("visible")

    async def test_help_scrolls_instead_of_closing(self, tui_small):
        from tests.unit.test_command_palette import _ready
        tui = tui_small
        async with tui.run_test(size=(80, 20)) as pilot:
            await _ready(pilot)
            await pilot.press("h")
            await pilot.pause()
            overlay = tui.query_one("#help-overlay")
            assert overlay.max_scroll_y > 0
            await pilot.press("pagedown")
            await pilot.pause()
            assert overlay.scroll_y > 0 and overlay.has_class("visible")
            await pilot.press("home")
            await pilot.pause()
            assert overlay.scroll_y == 0
            await pilot.press("j")
            await pilot.pause()
            assert overlay.scroll_y == 1 and overlay.has_class("visible")
            await pilot.press("escape")
            await pilot.pause()
            assert not overlay.has_class("visible")


@pytest.fixture
def tui_small(tmp_path, monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith("OVERCODE_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    from tests.unit.test_command_palette import _sessions
    from overcode.tui import SupervisorTUI
    with patch.object(SupervisorTUI, "_ensure_monitor_daemon", lambda self: None), \
         patch("overcode.launcher.AgentLauncher.list_sessions", lambda self: _sessions()):
        yield SupervisorTUI(tmux_session="test")


class TestWidgetScopes:

    def test_fullscreen_preview_takes_scope_keys(self):
        from overcode.tui_widgets import FullscreenPreview
        km = _km({"scopes": {"fullscreen_preview": {"scroll_down_20": ["J"]}}})
        km_mod.set_active(km)
        w = FullscreenPreview()
        keys = w._bindings.key_to_bindings
        assert "J" in keys and keys["J"][0].action == "scroll_down_20"
        assert "j" not in keys or keys["j"][0].action != "scroll_down_20"

    def test_default_entries_from_binding_objects(self):
        from textual.binding import Binding
        e = default_entries([Binding("a,b", "act", "Desc", show=False)])
        assert [(x.key, x.action, x.show) for x in e] == [("a", "act", False), ("b", "act", False)]
