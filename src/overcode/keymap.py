"""
Configurable keybindings (#510).

Pure logic, no Textual at import time. The keys overcode ships are the ones
written in code — ``SupervisorTUI.BINDINGS`` for the main screen and each
widget's own list for its scope. That *is* the ``default`` preset: nothing
here repeats it, so a binding added to ``BINDINGS`` is picked up everywhere
(keymap, palette, help, ``overcode keys``) without touching this module.

Other presets are deltas on top of those defaults, shipped as YAML in
``overcode/data/keymaps/``. The user's own deltas go in config.yaml::

    keys:
      preset: vscode                      # default | vscode
      overrides:                          # main-screen actions
        jump_to_agent: ["ctrl+j", "J"]    # action -> one key or a list
        toggle_monochrome: null           # unbind
      scopes:                             # the other key scopes
        command_palette:
          cursor_down: ["down", "ctrl+n"]

Layers apply in order default → preset → overrides. A layer names an
action and gives its complete key list, replacing what the layer below
bound for that action. Problems (an unknown action, a key bound twice in
one scope, a key the preset says the terminal swallows, a key that shadows
a passthru key) are collected as warnings; building a keymap never raises.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# Scopes: where a key is looked up. "app" is the main screen; the others
# are widgets that read keys while they have focus.
SCOPES: Dict[str, str] = {
    "app": "Main screen",
    "command_bar": "Command bar",
    "command_palette": "Command palette",
    "summary_prompt_lab": "Summary prompt lab",
}

# Removed with the standalone monitor in 0.6.0 (#523). A config that still
# names one gets a warning that says so; it is otherwise ignored.
REMOVED_SCOPES = frozenset({"fullscreen_preview"})
REMOVED_ACTIONS = frozenset({"toggle_preview", "expand_preview", "toggle_tmux_sync"})

# Scopes where the user types text: a bare printable key bound there would
# stop that character being typed.
TEXT_SCOPES = frozenset({"command_bar", "command_palette"})

DEFAULT_PRESET = "default"
PRESET_DIR = Path(__file__).parent / "data" / "keymaps"

# Passthru slots (config.passthru_keys) are named after the overcode key
# that forwards them; this is the action each slot belongs to.
PASSTHRU_SLOT_ACTIONS: Dict[str, str] = {
    "enter": "send_enter_to_focused",
    "escape": "send_escape_to_focused",
    "1": "send_1_to_focused",
    "2": "send_2_to_focused",
    "3": "send_3_to_focused",
    "4": "send_4_to_focused",
    "5": "send_5_to_focused",
    "ctrl+o": "send_ctrl_o_to_focused",
}


# ---------------------------------------------------------------------------
# Key names
# ---------------------------------------------------------------------------

# Printable characters → Textual key names, so config can say "?" or "@".
_CHAR_NAMES: Dict[str, str] = {
    " ": "space", "!": "exclamation_mark", '"': "quotation_mark", "#": "number_sign",
    "$": "dollar_sign", "%": "percent_sign", "&": "ampersand", "'": "apostrophe",
    "(": "left_parenthesis", ")": "right_parenthesis", "*": "asterisk", "+": "plus",
    ",": "comma", "-": "minus", ".": "full_stop", "/": "slash", ":": "colon",
    ";": "semicolon", "<": "less_than_sign", "=": "equals_sign", ">": "greater_than_sign",
    "?": "question_mark", "@": "at", "[": "left_square_bracket", "\\": "backslash",
    "]": "right_square_bracket", "^": "circumflex_accent", "_": "underscore",
    "`": "grave_accent", "{": "left_curly_bracket", "|": "vertical_line",
    "}": "right_curly_bracket", "~": "tilde",
}
_NAME_ALIASES: Dict[str, str] = {
    "esc": "escape", "return": "enter", "pgup": "pageup", "pgdn": "pagedown",
    "page_up": "pageup", "page_down": "pagedown", "del": "delete", "bs": "backspace",
    "backtick": "grave_accent",
}
_MODIFIERS = ("ctrl", "alt", "shift", "meta", "super")
_MOD_ALIASES = {"control": "ctrl", "c": "ctrl", "option": "alt", "opt": "alt", "m": "alt", "s": "shift"}
_KEY_RE = re.compile(r"^[A-Za-z0-9_+]+$")

# Keys that edit or move rather than type, so they are fine in text scopes.
_NON_TEXT_KEYS = frozenset({
    "escape", "enter", "tab", "backspace", "delete", "insert", "up", "down", "left",
    "right", "home", "end", "pageup", "pagedown",
} | {f"f{i}" for i in range(1, 25)})


def normalize_key(key: Any) -> str:
    """Canonical Textual key name for a key written by hand.

    Accepts Textual names (``ctrl+p``, ``question_mark``), characters
    (``?``, ``@``), caret notation (``^P``) and tmux notation (``C-p``,
    ``M-x``). Modifiers are lowercased, and so is a letter that carries a
    modifier (terminals report ``ctrl+p``, never ``ctrl+P``); a bare
    capital stays capital, since ``J`` is shift+j.
    """
    k = str(key).strip() if key is not None else ""
    if not k:
        return ""
    if len(k) == 1:
        return _CHAR_NAMES.get(k, k)
    if k.startswith("^") and len(k) >= 2:
        return "ctrl+" + normalize_key(k[1:]).lower()
    m = re.match(r"^([CMS])-(.+)$", k)
    if m:
        mod = {"C": "ctrl", "M": "alt", "S": "shift"}[m.group(1)]
        return normalize_key(f"{mod}+{m.group(2)}")
    parts = k.split("+")
    if len(parts) > 1 and parts[-1] == "":  # "ctrl++" → ctrl+plus
        parts = parts[:-2] + ["+"]
    *mods, base = parts
    mods = [_MOD_ALIASES.get(p.lower(), p.lower()) for p in mods]
    if len(base) == 1:
        base = _CHAR_NAMES.get(base, base)
        if mods and base.isalpha():
            base = base.lower()
    else:
        base = _NAME_ALIASES.get(base.lower(), base.lower())
    return "+".join(mods + [base])


def key_problem(key: str) -> Optional[str]:
    """Why a normalized key name cannot be right, or None."""
    if not key:
        return "empty key"
    if not _KEY_RE.match(key):
        return f"'{key}' is not a key name"
    *mods, base = key.split("+")
    bad = [m for m in mods if m not in _MODIFIERS]
    if bad:
        return f"'{key}' has unknown modifier {bad[0]!r}"
    if not base:
        return f"'{key}' has no key after the modifiers"
    return None


def parse_keys(value: Any) -> List[str]:
    """One key, a comma list or a YAML list → normalized keys ([] = unbind)."""
    if value is None or value is False:
        return []
    if isinstance(value, str):
        items: Iterable[Any] = [value] if value.strip() in (",",) else value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]
    out: List[str] = []
    for item in items:
        k = normalize_key(item)
        if k and k not in out:
            out.append(k)
    return out


def is_text_key(key: str) -> bool:
    """A key that types a character (no modifier other than shift)."""
    *mods, base = key.split("+")
    if any(m != "shift" for m in mods):
        return False
    return base not in _NON_TEXT_KEYS


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KeyBinding:
    """One key bound to one action in one scope.

    source: "default" (written in code), the preset's name, or "override"
    (the user's config).
    """
    key: str
    action: str
    description: str = ""
    source: str = "default"
    show: bool = True
    priority: bool = False


def default_entries(bindings: Iterable[Any]) -> List[KeyBinding]:
    """KeyBindings from a Textual BINDINGS list (tuples or Binding objects).

    A comma key ("j,down") becomes one entry per key.
    """
    out: List[KeyBinding] = []
    for b in bindings:
        if isinstance(b, (tuple, list)):
            key, action = b[0], b[1]
            desc = b[2] if len(b) > 2 else ""
            show, priority = True, False
        else:
            key, action = b.key, b.action
            desc = getattr(b, "description", "") or ""
            show = getattr(b, "show", True)
            priority = getattr(b, "priority", False)
        for k in str(key).split(","):
            k = k.strip()
            if k:
                out.append(KeyBinding(k, action, desc, "default", show, priority))
    return out


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    """A named delta on top of the in-code defaults.

    scopes:    {scope: {action: [keys]}} — [] unbinds.
    forbidden: keys this preset promises never to bind, with why
               (e.g. the terminal swallows them).
    tmux_toggle_key: the tmux pane-toggle key the preset recommends
               (advice only; tmux.toggle_key in config is never rewritten).
    """
    name: str
    description: str = ""
    scopes: Mapping[str, Mapping[str, List[str]]] = field(default_factory=dict)
    forbidden: Mapping[str, str] = field(default_factory=dict)
    tmux_toggle_key: Optional[str] = None
    tmux_toggle_avoid: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


def parse_layer(section: Any, where: str) -> Tuple[Dict[str, Dict[str, List[str]]], List[str]]:
    """Read ``overrides`` + ``scopes`` from a preset or the user's ``keys:``.

    Returns ({scope: {action: keys}}, warnings).
    """
    warnings: List[str] = []
    scopes: Dict[str, Dict[str, List[str]]] = {}
    if section is None:
        return scopes, warnings
    if not isinstance(section, Mapping):
        return scopes, [f"{where}: expected a mapping, got {type(section).__name__}"]

    def add(scope: str, mapping: Any, label: str) -> None:
        if mapping is None:
            return
        if not isinstance(mapping, Mapping):
            warnings.append(f"{where}: {label} should map actions to keys")
            return
        if scope in REMOVED_SCOPES:
            warnings.append(f"{where}: scope '{scope}' was removed in 0.6.0 — ignored")
            return
        if scope not in SCOPES:
            warnings.append(f"{where}: unknown scope '{scope}' (known: {', '.join(SCOPES)})")
            return
        target = scopes.setdefault(scope, {})
        for action, value in mapping.items():
            keys = parse_keys(value)
            for k in keys:
                problem = key_problem(k)
                if problem:
                    warnings.append(f"{where}: {scope}.{action}: {problem}")
            target[str(action)] = [k for k in keys if key_problem(k) is None]

    add("app", section.get("overrides"), "overrides")
    raw_scopes = section.get("scopes")
    if raw_scopes is not None and not isinstance(raw_scopes, Mapping):
        warnings.append(f"{where}: scopes should map scope names to actions")
    elif raw_scopes:
        for scope, mapping in raw_scopes.items():
            add(str(scope), mapping, f"scopes.{scope}")
    return scopes, warnings


def list_presets() -> List[str]:
    """Shipped preset names, default first."""
    names = sorted(p.stem for p in PRESET_DIR.glob("*.yaml")) if PRESET_DIR.is_dir() else []
    if DEFAULT_PRESET in names:
        names.remove(DEFAULT_PRESET)
    return [DEFAULT_PRESET] + names


def preset_from_dict(name: str, data: Any) -> Preset:
    data = data if isinstance(data, Mapping) else {}
    scopes, warnings = parse_layer(data, f"preset '{name}'")
    forbidden_raw = data.get("forbidden") or {}
    forbidden: Dict[str, str] = {}
    if isinstance(forbidden_raw, Mapping):
        for k, why in forbidden_raw.items():
            forbidden[normalize_key(k)] = str(why or "")
    elif isinstance(forbidden_raw, (list, tuple)):
        forbidden = {normalize_key(k): "" for k in forbidden_raw}
    tmux = data.get("tmux") if isinstance(data.get("tmux"), Mapping) else {}
    return Preset(
        name=str(data.get("name") or name),
        description=str(data.get("description") or ""),
        scopes=scopes,
        forbidden=forbidden,
        tmux_toggle_key=tmux.get("toggle_key"),
        tmux_toggle_avoid=tuple(str(k) for k in (tmux.get("avoid_toggle_keys") or ())),
        warnings=tuple(warnings),
    )


def load_preset(name: Optional[str]) -> Preset:
    """A shipped preset by name. Unknown names raise KeyError."""
    name = (name or DEFAULT_PRESET).strip()
    path = PRESET_DIR / f"{name}.yaml"
    if not re.match(r"^[A-Za-z0-9_-]+$", name) or not path.is_file():
        if name == DEFAULT_PRESET:
            return Preset(DEFAULT_PRESET, "overcode's built-in keys")
        raise KeyError(name)
    import yaml
    with open(path) as f:
        return preset_from_dict(name, yaml.safe_load(f))


# ---------------------------------------------------------------------------
# The effective keymap
# ---------------------------------------------------------------------------

@dataclass
class KeyMap:
    """Every scope's effective bindings, in binding order, plus warnings."""
    preset: str
    scopes: Dict[str, List[KeyBinding]]
    warnings: List[str] = field(default_factory=list)
    forbidden: Mapping[str, str] = field(default_factory=dict)
    tmux_toggle_key: Optional[str] = None
    _key_action_cache: Dict[str, Dict[str, str]] = field(default_factory=dict, repr=False)

    def bindings(self, scope: str = "app") -> List[KeyBinding]:
        return list(self.scopes.get(scope, ()))

    def keys_for(self, action: str, scope: str = "app") -> List[str]:
        return [b.key for b in self.scopes.get(scope, ()) if b.action == action]

    def labels_for(self, action: str, scope: str = "app") -> List[str]:
        from .command_palette import key_label
        return [key_label(k) for k in self.keys_for(action, scope)]

    def label(self, action: str, scope: str = "app", sep: str = "/", fallback: str = "") -> str:
        """Display form of an action's keys, e.g. ``j/↓``; ``fallback`` if unbound."""
        return sep.join(self.labels_for(action, scope)) or fallback

    def action_for(self, key: str, scope: str = "app") -> Optional[str]:
        """The action a key runs in a scope (the first binding wins)."""
        cache = self._key_action_cache.get(scope)
        if cache is None:
            cache = {}
            for b in self.scopes.get(scope, ()):
                cache.setdefault(b.key, b.action)
            self._key_action_cache[scope] = cache
        return cache.get(key)

    def keys_by_action(self, scope: str = "app") -> Dict[str, List[str]]:
        """Action → key labels, like command_palette.keys_by_action."""
        from .command_palette import keys_by_action
        return keys_by_action(self.as_tuples(scope))

    def raw_keys_by_action(self, scope: str = "app") -> Dict[str, List[str]]:
        """Action → Textual key names (what the usage log records)."""
        out: Dict[str, List[str]] = {}
        for b in self.scopes.get(scope, ()):
            out.setdefault(b.action, []).append(b.key)
        return out

    def as_tuples(self, scope: str = "app") -> List[Tuple[str, str, str]]:
        return [(b.key, b.action, b.description) for b in self.scopes.get(scope, ())]

    def source_of(self, action: str, scope: str = "app") -> Optional[str]:
        for b in self.scopes.get(scope, ()):
            if b.action == action:
                return b.source
        return None


def _apply_layer(
    entries: List[KeyBinding],
    layer: Mapping[str, List[str]],
    source: str,
    known: Mapping[str, str],
    scope: str,
    warnings: List[str],
) -> List[KeyBinding]:
    """Replace each named action's keys, keeping its place in binding order."""
    out = list(entries)
    for action, keys in layer.items():
        if action not in known:
            if action in REMOVED_ACTIONS:
                warnings.append(f"{source}: action '{action}' was removed in 0.6.0 — ignored")
            else:
                warnings.append(f"{source}: unknown action '{action}' in {scope}")
            continue
        old = [b for b in out if b.action == action]
        at = next((i for i, b in enumerate(out) if b.action == action), len(out))
        template = old[0] if old else KeyBinding("", action, known.get(action, ""))
        out = [b for b in out if b.action != action]
        new = [replace(template, key=k, source=source) for k in keys]
        at = min(at, len(out))
        out[at:at] = new
    return out


def build_keymap(
    defaults: Mapping[str, Iterable[Any]],
    preset: Optional[Preset] = None,
    user: Optional[Mapping[str, Mapping[str, List[str]]]] = None,
    extra_actions: Optional[Mapping[str, Mapping[str, str]]] = None,
    passthru: Optional[Mapping[str, str]] = None,
    tmux_toggle_key: Optional[str] = None,
    user_warnings: Sequence[str] = (),
) -> KeyMap:
    """The effective keymap: defaults, then the preset, then the user's layer.

    defaults:      {scope: BINDINGS list or KeyBindings} — the code's keys.
    extra_actions: {scope: {action: description}} of actions that may be
                   bound though nothing binds them by default (palette-only
                   commands such as reverse_sort).
    passthru:      the active passthru map (config.get_passthru_keys()).
    tmux_toggle_key: the configured tmux pane toggle key, checked against
                   the preset's advice.
    """
    preset = preset or Preset(DEFAULT_PRESET)
    user = user or {}
    extra_actions = extra_actions or {}
    warnings: List[str] = list(preset.warnings) + list(user_warnings)
    scopes: Dict[str, List[KeyBinding]] = {}

    for scope in SCOPES:
        base = defaults.get(scope, ())
        entries = [b if isinstance(b, KeyBinding) else None for b in base]
        if any(e is None for e in entries):
            entries = default_entries(base)
        known: Dict[str, str] = {}
        for b in entries:
            known.setdefault(b.action, b.description)
        for action, desc in extra_actions.get(scope, {}).items():
            known.setdefault(action, desc)
        entries = _apply_layer(entries, preset.scopes.get(scope, {}), preset.name, known, scope, warnings)
        entries = _apply_layer(entries, user.get(scope, {}), "override", known, scope, warnings)
        scopes[scope] = entries

    km = KeyMap(preset.name, scopes, warnings, dict(preset.forbidden), preset.tmux_toggle_key)
    warnings.extend(conflicts(km, passthru))
    if tmux_toggle_key and tmux_toggle_key in preset.tmux_toggle_avoid:
        warnings.append(
            f"tmux toggle key {tmux_toggle_key} is unreliable with the '{preset.name}' preset"
            + (f" — try {preset.tmux_toggle_key} (the 'Tmux toggle key…' command)"
               if preset.tmux_toggle_key else ""))
    return km


def conflicts(km: KeyMap, passthru: Optional[Mapping[str, str]] = None) -> List[str]:
    """Duplicate keys per scope, forbidden keys, typed keys in text scopes,
    and keys that shadow a passthru key."""
    from .command_palette import key_label
    out: List[str] = []
    for scope, entries in km.scopes.items():
        by_key: Dict[str, List[str]] = {}
        for b in entries:
            acts = by_key.setdefault(b.key, [])
            if b.action not in acts:
                acts.append(b.action)
        for key, acts in by_key.items():
            if len(acts) > 1:
                out.append(f"{scope}: {key_label(key)} is bound to {' and '.join(acts)} "
                           f"— only {acts[0]} runs")
        for b in entries:
            if b.key in km.forbidden:
                why = km.forbidden[b.key]
                out.append(f"{scope}: {key_label(b.key)} ({b.action}) is avoided by the "
                           f"'{km.preset}' preset" + (f": {why}" if why else ""))
            if scope in TEXT_SCOPES and is_text_key(b.key):
                out.append(f"{scope}: {key_label(b.key)} ({b.action}) is a typing key "
                           f"— it can no longer be typed there")
    for slot, target in (passthru or {}).items():
        slot_action = PASSTHRU_SLOT_ACTIONS.get(slot)
        if not target:
            continue
        owner = km.action_for(normalize_key(slot))
        if slot_action and owner and owner != slot_action:
            out.append(f"app: {key_label(normalize_key(slot))} is a passthru key (forwards "
                       f"{target} to the agent) but is bound to {owner}")
    return out


# ---------------------------------------------------------------------------
# Loading from the running code and config
# ---------------------------------------------------------------------------

def default_scope_bindings() -> Dict[str, List[KeyBinding]]:
    """The in-code defaults of every scope (imports the TUI lazily)."""
    from .tui import SupervisorTUI
    from .tui_widgets.command_bar import CommandBar
    from .tui_widgets.command_palette import CommandPalette
    from .tui_widgets.summary_prompt_lab import SummaryPromptLab
    return {
        "app": default_entries(SupervisorTUI.BINDINGS),
        "command_bar": default_entries(CommandBar.KEYS),
        "command_palette": default_entries(CommandPalette.KEYS),
        "summary_prompt_lab": default_entries(SummaryPromptLab.BINDINGS),
    }


def extra_app_actions() -> Dict[str, Dict[str, str]]:
    """Palette commands may be bound even when no key reaches them today."""
    from .command_palette import COMMANDS
    return {"app": {c.action: c.title for c in COMMANDS}}


def user_keys_config() -> Any:
    """The ``keys:`` section of ~/.overcode/config.yaml (tests patch this)."""
    from .config import _get_config_value
    return _get_config_value("keys", {})


def configured_preset_name(section: Any = None) -> str:
    section = user_keys_config() if section is None else section
    if isinstance(section, Mapping) and section.get("preset"):
        return str(section["preset"])
    return DEFAULT_PRESET


def effective_keymap(preset: Optional[str] = None, section: Any = None,
                     passthru: Optional[Mapping[str, str]] = None,
                     tmux_toggle_key: Optional[str] = None,
                     use_config: bool = True) -> KeyMap:
    """The keymap the TUI runs with: code defaults + preset + config overrides.

    preset: show this preset instead of the configured one.
    use_config=False ignores config entirely (the pure default map).
    """
    section = (user_keys_config() if use_config else {}) if section is None else section
    warnings: List[str] = []
    name = preset or configured_preset_name(section)
    try:
        p = load_preset(name)
    except KeyError:
        warnings.append(f"unknown key preset '{name}' (known: {', '.join(list_presets())}) "
                        "— using default")
        p = load_preset(DEFAULT_PRESET)
    user, user_warn = parse_layer(section, "config keys") if use_config else ({}, [])
    if use_config and passthru is None:
        try:
            from .config import get_passthru_keys
            passthru = get_passthru_keys()
        except Exception:
            passthru = None
    if use_config and tmux_toggle_key is None:
        try:
            from .config import get_tmux_toggle_key
            tmux_toggle_key = get_tmux_toggle_key()
        except Exception:
            tmux_toggle_key = None
    return build_keymap(default_scope_bindings(), p, user, extra_app_actions(),
                        passthru, tmux_toggle_key, warnings + user_warn)


_default_keymap: Optional[KeyMap] = None
_active_keymap: Optional[KeyMap] = None


def default_keymap() -> KeyMap:
    """The built-in keys with no preset or config (cached)."""
    global _default_keymap
    if _default_keymap is None:
        _default_keymap = effective_keymap(use_config=False)
    return _default_keymap


def active() -> KeyMap:
    """The keymap the running TUI set (see set_active), else the defaults."""
    return _active_keymap if _active_keymap is not None else default_keymap()


def keymap_of(app: Any) -> KeyMap:
    """An app's keymap (``app.keymap``), else the active one."""
    km = getattr(app, "keymap", None)
    return km if isinstance(km, KeyMap) else active()


def set_active(km: Optional[KeyMap]) -> None:
    global _active_keymap
    _active_keymap = km


# ---------------------------------------------------------------------------
# Textual glue (imports Textual only when called)
# ---------------------------------------------------------------------------

def textual_bindings_map(cls: type, entries: Iterable[KeyBinding]):
    """A BindingsMap for an instance of ``cls``: what it inherits from its
    Textual base classes, with ``entries`` in place of its own BINDINGS.

    Mirrors Textual's own merge, where a class's key replaces the same key
    inherited from a base class.
    """
    from textual.binding import Binding, BindingsMap
    from textual.dom import DOMNode
    base = next((b for b in cls.__mro__[1:] if isinstance(b, type) and issubclass(b, DOMNode)), None)
    merged = getattr(base, "_merged_bindings", None) if base is not None else None
    keys: Dict[str, list] = {}
    if merged is not None:
        keys = {k: list(v) for k, v in merged.key_to_bindings.items()}
    own: Dict[str, list] = {}
    for b in entries:
        own.setdefault(b.key, []).append(
            Binding(b.key, b.action, b.description, show=b.show, priority=b.priority))
    keys.update(own)
    return BindingsMap.from_keys(keys)


def apply_to_widget(widget: Any, scope: str, km: Optional[KeyMap] = None) -> None:
    """Give a mounted or new widget the keymap's bindings for its scope."""
    km = km or active()
    widget._bindings = textual_bindings_map(type(widget), km.bindings(scope))


# ---------------------------------------------------------------------------
# Config writes and terminal advice
# ---------------------------------------------------------------------------

def set_configured_preset(name: str) -> None:
    """Write ``keys.preset`` to config.yaml (the default preset removes it)."""
    if name not in list_presets():
        raise KeyError(name)
    from .config import load_config, save_config
    config = dict(load_config())
    section = config.get("keys")
    section = dict(section) if isinstance(section, Mapping) else {}
    if name == DEFAULT_PRESET:
        section.pop("preset", None)
    else:
        section["preset"] = name
    if section:
        config["keys"] = section
    else:
        config.pop("keys", None)
    save_config(config)


def in_vscode(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return env.get("TERM_PROGRAM", "").lower() == "vscode"


def terminal_findings(env: Optional[Mapping[str, str]] = None,
                      preset: Optional[str] = None,
                      tmux_toggle_key: Optional[str] = None) -> List[str]:
    """Advice for `overcode doctor`: running in VSCode on the default keys."""
    if not in_vscode(env):
        return []
    out: List[str] = []
    preset = preset if preset is not None else configured_preset_name()
    if preset == DEFAULT_PRESET:
        out.append(
            "running in VSCode's terminal with the default keys — on Linux/Windows VSCode "
            "takes ^P ^K ^G (and ^E ^J in text fields) before overcode sees them; on macOS "
            "they get through but clash with VSCode habits. Switch with: overcode keys --use "
            "vscode  (or keep them and add e.g. \"-workbench.action.quickOpen\" to "
            "terminal.integrated.commandsToSkipShell, or turn on "
            "terminal.integrated.sendKeybindingsToShell)")
    if tmux_toggle_key == "C-Space":
        out.append("tmux toggle key Ctrl+Space is taken by VSCode's terminal suggest "
                   "(and macOS input switching) — pick another in `overcode tmux`")
    return out
