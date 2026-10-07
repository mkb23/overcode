"""Energy estimates from token counts and a guess at the GPUs serving the model (#522).

overcode used to convert dollars to joules as if the whole API price were
electricity at $0.07/kWh. Electricity is a few percent of what a token
costs, so that overstated energy 10-100x ("1 MW of compute").

This models each model family as one serving replica instead:

    joules per output token = gpus x gpu_watts x pue / decode_tps
    joules per input token  = gpus x gpu_watts x pue / prefill_tps

``decode_tps`` and ``prefill_tps`` are the replica's throughput summed over
its whole batch, so the batching that makes serving efficient is already
divided in. Cache reads cost ``cache_read_factor`` of a fresh input token
(the prefix is not recomputed); cache writes cost ``cache_write_factor``.

Every number here is a guess, not a measurement: providers do not publish
replica sizes or throughput. The defaults are deliberately round and live in
one place, and ``config.yaml``'s ``energy:`` section overrides any of them::

    energy:
      pue: 1.2
      cache_read_factor: 0.1
      classes:                 # replace or add size classes
        large: {gpus: 16, gpu_watts: 1000, decode_tps: 1500, prefill_tps: 50000}
      models:                  # model-name substring -> class or profile,
        opus: large            # checked in order before the built-in rules
        llama-3-8b: {gpus: 1, gpu_watts: 450, decode_tps: 400, prefill_tps: 8000}
        some-model: {joules_per_output_token: 2.0, joules_per_input_token: 0.05}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class EnergyProfile:
    """One serving replica: its accelerators and how many tokens it moves.

    ``gpu_watts`` is per accelerator under load, including its share of the
    host (CPUs, memory, network). Alternatively a profile can state
    ``joules_per_output_token`` / ``joules_per_input_token`` outright, which
    take precedence over the GPU-derived figures.
    """

    gpus: float = 8.0
    gpu_watts: float = 1000.0
    decode_tps: float = 3000.0
    prefill_tps: float = 100_000.0
    joules_per_output_token: Optional[float] = None
    joules_per_input_token: Optional[float] = None

    def per_output(self, pue: float) -> float:
        if self.joules_per_output_token is not None:
            return self.joules_per_output_token
        return self.gpus * self.gpu_watts * pue / self.decode_tps

    def per_input(self, pue: float) -> float:
        if self.joules_per_input_token is not None:
            return self.joules_per_input_token
        return self.gpus * self.gpu_watts * pue / self.prefill_tps


# Size classes. A frontier model is guessed at two 8-GPU nodes per replica,
# a mid-size model at one node, a small one at half a node; throughput falls
# with size. These give ~13 / ~3 / ~0.8 J per output token.
DEFAULT_CLASSES: Dict[str, EnergyProfile] = {
    "large": EnergyProfile(gpus=16, gpu_watts=1000, decode_tps=1500, prefill_tps=50_000),
    "medium": EnergyProfile(gpus=8, gpu_watts=1000, decode_tps=3000, prefill_tps=100_000),
    "small": EnergyProfile(gpus=4, gpu_watts=1000, decode_tps=6000, prefill_tps=200_000),
}

# Model-name substring -> class, first match wins. Small markers come first
# so "gpt-5-mini" is small rather than large.
DEFAULT_RULES: Tuple[Tuple[str, str], ...] = (
    ("haiku", "small"),
    ("mini", "small"),
    ("nano", "small"),
    ("flash", "small"),
    ("lite", "small"),
    ("opus", "large"),
    ("fable", "large"),
    ("gpt-5", "large"),
    ("grok", "large"),
    ("o3", "large"),
    ("sonnet", "medium"),
)

DEFAULT_CLASS = "medium"


@dataclass(frozen=True)
class EnergyConfig:
    pue: float = 1.2
    cache_read_factor: float = 0.1
    cache_write_factor: float = 1.0
    classes: Dict[str, EnergyProfile] = field(default_factory=lambda: dict(DEFAULT_CLASSES))
    # User rules, in file order, ahead of DEFAULT_RULES: substring -> class
    # name or an inline profile
    rules: Tuple[Tuple[str, Any], ...] = ()
    default_class: str = DEFAULT_CLASS

    def profile_for(self, model: Optional[str]) -> EnergyProfile:
        name = (model or "").lower()
        if name:
            for pattern, target in (*self.rules, *DEFAULT_RULES):
                if pattern in name:
                    if isinstance(target, EnergyProfile):
                        return target
                    if target in self.classes:
                        return self.classes[target]
        return self.classes.get(self.default_class) or DEFAULT_CLASSES[DEFAULT_CLASS]

    @classmethod
    def from_dict(cls, raw: Any) -> "EnergyConfig":
        """Parse config.yaml's ``energy:`` section; bad values keep the defaults."""
        if not isinstance(raw, dict):
            return cls()
        classes = dict(DEFAULT_CLASSES)
        raw_classes = raw.get("classes")
        if isinstance(raw_classes, dict):
            for name, spec in raw_classes.items():
                profile = _profile(spec, base=classes.get(str(name)))
                if profile is not None:
                    classes[str(name)] = profile
        rules: List[Tuple[str, Any]] = []
        raw_models = raw.get("models")
        if isinstance(raw_models, dict):
            for pattern, target in raw_models.items():
                if isinstance(target, str):
                    rules.append((str(pattern).lower(), target))
                else:
                    profile = _profile(target)
                    if profile is not None:
                        rules.append((str(pattern).lower(), profile))
        default_class = raw.get("default_class")
        return cls(
            pue=_positive(raw.get("pue"), 1.2),
            cache_read_factor=_non_negative(raw.get("cache_read_factor"), 0.1),
            cache_write_factor=_non_negative(raw.get("cache_write_factor"), 1.0),
            classes=classes,
            rules=tuple(rules),
            default_class=default_class if default_class in classes else DEFAULT_CLASS,
        )


_PROFILE_FIELDS = ("gpus", "gpu_watts", "decode_tps", "prefill_tps",
                   "joules_per_output_token", "joules_per_input_token")


def _profile(spec: Any, base: Optional[EnergyProfile] = None) -> Optional[EnergyProfile]:
    """An EnergyProfile from a mapping; keys it omits come from ``base``."""
    if not isinstance(spec, dict):
        return None
    values = {
        name: getattr(base, name) for name in _PROFILE_FIELDS
    } if base is not None else {}
    for name in _PROFILE_FIELDS:
        if name in spec:
            number = _positive(spec[name], None)
            if number is None:
                return None
            values[name] = number
    return EnergyProfile(**values)


def _positive(value: Any, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _non_negative(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def estimate_energy_joules(
    model: Optional[str],
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    config: Optional[EnergyConfig] = None,
) -> float:
    """Estimated joules to serve these tokens on ``model``."""
    if config is None:
        config = _configured()
    profile = config.profile_for(model)
    per_input = profile.per_input(config.pue)
    return (
        output_tokens * profile.per_output(config.pue)
        + input_tokens * per_input
        + cache_creation_tokens * per_input * config.cache_write_factor
        + cache_read_tokens * per_input * config.cache_read_factor
    )


def _configured() -> EnergyConfig:
    from .settings import get_user_config

    try:
        configured = get_user_config().energy
    except Exception:
        configured = None
    # A config that isn't one (a stub, a partly loaded object) must never
    # take the stats sync down with it.
    return configured if isinstance(configured, EnergyConfig) else EnergyConfig()


def format_si(value: float, symbol: str) -> str:
    """``value`` with an SI prefix in a stable 5-cell field: " 850W", "1.2kJ".

    Picks the smallest prefix whose rounded value still fits in three
    digits, so 999,999 reads "1.0M", never the six-cell "1000k".
    """
    for scale, prefix in ((1.0, ""), (1e3, "k"), (1e6, "M"), (1e9, "G"), (1e12, "T")):
        v = value / scale
        if not prefix:
            if round(v) < 1000:
                return f"{v:.0f}{symbol}".rjust(5)
            continue
        if round(v, 1) < 10:
            return f"{v:.1f}{prefix}{symbol}".rjust(5)
        if round(v) < 1000:
            return f"{v:.0f}{prefix}{symbol}".rjust(5)
    return f"{value / 1e15:.0f}P{symbol}".rjust(5)


def format_watts(watts: float) -> str:
    """Power, e.g. "1.2kW" (5 cells)."""
    return format_si(watts, "W")
