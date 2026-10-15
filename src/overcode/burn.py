"""Token burn over a time window: tokens, cost and energy per agent and in total.

The engine computes it for the windows views ask for (#174) and publishes
each agent's share; views sum it for the fleet.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Set

from .clock import local_naive


@dataclass
class WindowBurnStats:
    """Token/cost spend over a time window — used for both per-session and
    aggregated totals. When holding an aggregate, ``per_session`` maps
    session id → that session's own WindowBurnStats (whose per_session is
    always empty)."""
    window_hours: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    energy_j: float = 0.0  # estimated, see energy.py (#522)
    per_session: dict = None  # Optional[Dict[str, WindowBurnStats]]

    def __post_init__(self):
        if self.per_session is None:
            self.per_session = {}

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def watts(self) -> float:
        """Average power over the window: estimated joules per second (#521)."""
        if self.window_hours <= 0:
            return 0.0
        return self.energy_j / (self.window_hours * 3600)

    @property
    def tokens_per_hour(self) -> float:
        if self.window_hours <= 0:
            return 0.0
        return self.total_tokens / self.window_hours

    @property
    def cost_per_hour(self) -> float:
        if self.window_hours <= 0:
            return 0.0
        return self.cost_usd / self.window_hours


def compute_window_burn(
    sessions,  # iterable of full Session objects (NOT SessionDaemonState)
    asleep_session_ids: Set[str],
    hours: float,
    now: Optional[datetime] = None,
) -> WindowBurnStats:
    """Aggregate token burn across sessions over the past ``hours`` window (#174).

    Re-parses each session's transcript files filtered by the window's start
    timestamp, sums tokens, and computes cost using each session's per-model
    pricing. Skips asleep sessions and sessions without start_directory or
    agent_session_ids.

    Designed to run on a worker thread — does file I/O proportional to
    `(active sessions) × (agent_session_ids per session)`. Safe for the
    typical 5-15 session range; cache externally if you have hundreds.
    """
    from .stats_reader import stats_reader_for_session
    from .energy import estimate_energy_joules
    from .pricing import calculate_cost_estimate, cost_of_1h_writes
    from .settings import get_user_config, get_model_pricing

    stats = WindowBurnStats(window_hours=hours)
    if hours <= 0:
        return stats

    # The daemon's tick time is aware: local-naive, like everything below
    now = datetime.now() if now is None else local_naive(now)
    # `since` is local-naive to match read_window_token_usage, which normalises
    # message timestamps to the local zone via .astimezone() before dropping
    # tzinfo. Mixing naive UTC with local-naive (as some older callers in
    # this module do) skews the cutoff by the local UTC offset.
    since = now - timedelta(hours=hours)

    config = None
    for session in sessions:
        if session.id in asleep_session_ids:
            continue

        u = stats_reader_for_session(session).get_window_token_usage(session, since)
        if not (u["input_tokens"] or u["output_tokens"]
                or u["cache_creation_tokens"] or u["cache_read_tokens"]):
            continue

        # Lazily load user config — only needed if we actually have tokens
        if config is None:
            config = get_user_config()
        mp = get_model_pricing(
            getattr(session, 'model', None), config,
            provider=getattr(session, 'provider', None),
        )
        cost = calculate_cost_estimate(
            u["input_tokens"],
            u["output_tokens"],
            u["cache_creation_tokens"],
            u["cache_read_tokens"],
            price_input=mp.input,
            price_output=mp.output,
            price_cache_write=mp.cache_write,
            price_cache_read=mp.cache_read,
        ) + cost_of_1h_writes(u.get("cache_creation_1h_tokens", 0), mp)

        stats.input_tokens += u["input_tokens"]
        stats.output_tokens += u["output_tokens"]
        stats.cache_creation_tokens += u["cache_creation_tokens"]
        stats.cache_read_tokens += u["cache_read_tokens"]
        stats.cost_usd += cost
        energy = estimate_energy_joules(
            getattr(session, 'model', None), u["input_tokens"], u["output_tokens"],
            u["cache_creation_tokens"], u["cache_read_tokens"],
        )
        stats.energy_j += energy

        # Per-session breakdown for the burn-rate column (#174)
        stats.per_session[session.id] = WindowBurnStats(
            window_hours=hours,
            input_tokens=u["input_tokens"],
            output_tokens=u["output_tokens"],
            cache_creation_tokens=u["cache_creation_tokens"],
            cache_read_tokens=u["cache_read_tokens"],
            cost_usd=cost,
            energy_j=energy,
        )

    return stats
