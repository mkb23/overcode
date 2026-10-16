"""Children presets: named oversight, cleanup and budget settings for a child.

A preset bundles what happens to a child agent that stops (its oversight
policy and timeout), how soon a finished child's window is closed (the
done-archive delay) and a default budget. ``overcode launch --preset
<name>`` picks one; a child launched without ``--preset`` gets the
configured default (``children.preset`` in config.yaml, else
``supervised``, which is overcode's behaviour before presets). Explicit
flags (``--on-stuck``, ``--oversight-timeout``, ``--archive-after``,
``--budget``) override the preset's values.

Config format in ~/.overcode/config.yaml::

    children:
      preset: balanced            # the default for children launched without --preset
      presets:
        autonomous:               # change a built-in, field by field
          archive_after: 30m
        relaxed:
          oversight_timeout: 3d   # shorthand for on_stuck: timeout:3d (0: wait)
        overnight:                # or add your own
          on_stuck: timeout:8h    # wait | fail | timeout:DURATION
          archive_after: never    # DURATION, or never
          budget: 5.00            # USD; taken from the parent's budget if it has one
          description: Long unattended runs

A field a preset leaves out takes the built-in of the same name's value,
else ``supervised``'s. Unknown preset names, unknown keys and unreadable
values are warned about and ignored; nothing here raises.
"""

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

# How long a done child keeps its window before the daemon closes it and
# marks it terminated (#244; the session archive then moves the record).
DEFAULT_ARCHIVE_AFTER_SECONDS = 3600.0

ON_STUCK_POLICIES = ("wait", "fail", "timeout")


@dataclass(frozen=True)
class ChildPreset:
    """One preset's settings, as stored on the child at launch."""

    name: str
    oversight_policy: str = "wait"          # wait | fail | timeout
    oversight_timeout_seconds: float = 0.0  # used with policy "timeout"
    archive_after_seconds: float = DEFAULT_ARCHIVE_AFTER_SECONDS  # negative: never
    budget_usd: Optional[float] = None      # None: no default budget
    description: str = ""


BUILTIN_PRESETS: Dict[str, ChildPreset] = {
    "supervised": ChildPreset(
        "supervised",
        description="Waits for you for ever: no oversight timeout",
    ),
    "balanced": ChildPreset(
        "balanced", oversight_policy="timeout", oversight_timeout_seconds=24 * 3600,
        description="A day's leash: a child that stops without a report is marked ⌛, then archived",
    ),
    "autonomous": ChildPreset(
        "autonomous", oversight_policy="timeout", oversight_timeout_seconds=3600,
        archive_after_seconds=600,
        description="Short leash and quick cleanup, for unattended work",
    ),
}

DEFAULT_PRESET = "supervised"

_PRESET_KEYS = ("on_stuck", "oversight_timeout", "archive_after", "budget", "description")
_SECTION_KEYS = ("preset", "presets")
_NEVER = ("never", "off", "none")


def parse_duration(value: Any) -> float:
    """Seconds from ``90``, ``"30s"``, ``"5m"``, ``"1h"`` or ``"2d"``; ValueError otherwise."""
    if isinstance(value, bool):
        raise ValueError(f"not a duration: {value!r}")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(text[-1:], None)
        number = text[:-1] if unit else text
        seconds = float(number) * (unit or 1)
    else:
        raise ValueError(f"not a duration: {value!r}")
    if seconds < 0 or seconds != seconds:  # negative or NaN
        raise ValueError(f"not a duration: {value!r}")
    return seconds


def parse_on_stuck(value: Any) -> Tuple[str, float]:
    """(policy, timeout seconds) from ``wait``, ``fail`` or ``timeout:DURATION``."""
    text = str(value).strip().lower() if isinstance(value, str) else ""
    if text in ("wait", "fail"):
        return text, 0.0
    if text.startswith("timeout:"):
        seconds = parse_duration(text[len("timeout:"):])
        if seconds <= 0:
            raise ValueError(f"timeout must be more than 0: {value!r}")
        return "timeout", seconds
    raise ValueError(f"not wait, fail or timeout:DURATION: {value!r}")


def parse_archive_after(value: Any) -> float:
    """Seconds from a duration, or -1 for ``never``."""
    if isinstance(value, str) and value.strip().lower() in _NEVER:
        return -1.0
    return parse_duration(value)


def format_duration(seconds: float) -> str:
    """``24h``, ``10m``, ``90s``; ``never`` for a negative value."""
    if seconds < 0:
        return "never"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        # Hours up to two days: "24h" reads better than "1d"
        if seconds >= size and seconds % size == 0 and (unit != "d" or seconds > 2 * 86400):
            return f"{int(seconds // size)}{unit}"
    return f"{seconds:g}s"


def describe(preset: ChildPreset) -> str:
    """One line of what a preset does, for launch output and listings."""
    if preset.oversight_policy == "timeout":
        oversight = f"oversight timeout {format_duration(preset.oversight_timeout_seconds)}"
    elif preset.oversight_policy == "fail":
        oversight = "fail if it stops without a report"
    else:
        oversight = "waits for a report"
    parts = [oversight]
    if preset.archive_after_seconds < 0:
        parts.append("never archived automatically")
    else:
        parts.append(f"archived {format_duration(preset.archive_after_seconds)} after done")
    if preset.budget_usd:
        parts.append(f"budget ${preset.budget_usd:.2f}")
    return ", ".join(parts)


def _apply_fields(base: ChildPreset, name: str, raw: Mapping, warnings: List[str]) -> ChildPreset:
    """``base`` with the fields ``raw`` sets; bad ones warned about and skipped."""
    where = f"children.presets.{name}"
    changes: Dict[str, Any] = {}
    for key in raw:
        if key not in _PRESET_KEYS:
            warnings.append(f"{where}: unknown key '{key}' — ignored")
    if "on_stuck" in raw and "oversight_timeout" in raw:
        warnings.append(f"{where}: both on_stuck and oversight_timeout — using oversight_timeout")
    if "on_stuck" in raw and "oversight_timeout" not in raw:
        try:
            policy, seconds = parse_on_stuck(raw["on_stuck"])
            changes.update(oversight_policy=policy, oversight_timeout_seconds=seconds)
        except ValueError:
            warnings.append(f"{where}.on_stuck: {raw['on_stuck']!r} is not wait, fail "
                            f"or timeout:DURATION — ignored")
    if "oversight_timeout" in raw:
        try:
            seconds = parse_duration(raw["oversight_timeout"])
            if seconds > 0:
                changes.update(oversight_policy="timeout", oversight_timeout_seconds=seconds)
            else:
                changes.update(oversight_policy="wait", oversight_timeout_seconds=0.0)
        except ValueError:
            warnings.append(f"{where}.oversight_timeout: {raw['oversight_timeout']!r} "
                            f"is not a duration — ignored")
    if "archive_after" in raw:
        try:
            changes["archive_after_seconds"] = parse_archive_after(raw["archive_after"])
        except ValueError:
            warnings.append(f"{where}.archive_after: {raw['archive_after']!r} is not a "
                            f"duration or never — ignored")
    if "budget" in raw:
        budget = raw["budget"]
        if budget is None or (isinstance(budget, (int, float)) and not isinstance(budget, bool)
                              and budget >= 0):
            changes["budget_usd"] = float(budget) if budget else None
        else:
            warnings.append(f"{where}.budget: {budget!r} is not an amount in USD — ignored")
    if "description" in raw and isinstance(raw["description"], str):
        changes["description"] = raw["description"]
    return replace(base, name=name, **changes)


def _config_section() -> Any:
    from .config import _get_config_value
    return _get_config_value("children", {})


def load_presets(section: Any = None) -> Tuple[Dict[str, ChildPreset], str, List[str]]:
    """The presets in effect, the default preset's name, and config warnings.

    ``section`` is the ``children`` mapping from config.yaml (read when
    None). Built-ins come first, then the user's own in config order.
    """
    section = _config_section() if section is None else section
    warnings: List[str] = []
    presets = dict(BUILTIN_PRESETS)
    if not isinstance(section, Mapping):
        if section not in (None, {}):
            warnings.append("children: should be a mapping — ignored")
        return presets, DEFAULT_PRESET, warnings
    for key in section:
        if key not in _SECTION_KEYS:
            warnings.append(f"children: unknown key '{key}' — ignored")
    user = section.get("presets") or {}
    if not isinstance(user, Mapping):
        warnings.append("children.presets: should be a mapping of preset names — ignored")
        user = {}
    for name, raw in user.items():
        name = str(name).strip()
        if not name:
            continue
        if not isinstance(raw, Mapping):
            warnings.append(f"children.presets.{name}: should be a mapping — ignored")
            continue
        base = presets.get(name, BUILTIN_PRESETS[DEFAULT_PRESET])
        presets[name] = _apply_fields(base, name, raw, warnings)
    default = section.get("preset") or DEFAULT_PRESET
    default = str(default).strip()
    if default not in presets:
        warnings.append(f"children.preset: unknown preset '{default}' "
                        f"(known: {', '.join(presets)}) — using {DEFAULT_PRESET}")
        default = DEFAULT_PRESET
    return presets, default, warnings


def resolve_preset(name: Optional[str], *, is_child: bool,
                   section: Any = None) -> Tuple[Optional[ChildPreset], List[str]]:
    """The preset a launch uses, and warnings to show.

    ``name`` is ``--preset``'s value: an unknown one is warned about and
    the default used instead. Without one, a child gets the default preset
    and a top-level agent none (presets are about children).
    """
    presets, default, warnings = load_presets(section)
    if name is None:
        return (presets[default] if is_child else None), warnings
    key = name.strip()
    if key not in presets:
        warnings.append(f"unknown preset '{name}' (known: {', '.join(presets)}) "
                        f"— using {default}")
        key = default
    return presets[key], warnings


def archive_after_of(session) -> float:
    """A session's done-archive delay in seconds (negative: never)."""
    value = getattr(session, "archive_after_seconds", None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return DEFAULT_ARCHIVE_AFTER_SECONDS
