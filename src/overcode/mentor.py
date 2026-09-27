"""
The mentor (#483 P3): at most an occasional tip, only when it can help.

Off by default (`journey.mentor: off | occasional | coach` in config.yaml).
The director is a pure function of the journey, the mentor state and the
clock; the TUI decides *when* it may ask (idle, nothing open, nobody
waiting on you) and shows the tip in its footer.

The contract, from the design:
- earned: never teach what the log shows you already do
- rare: one unsolicited tip per TUI run on `occasional`, spaced by a gap
  that grows when tips are ignored and shrinks when they're taken up
- remembers: an ignored topic is snoozed for a week
- fades: after the first month only the single best tip is offered
- no retroactive party: achievements already earned when the mentor is
  first switched on are recorded silently
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

DIALS = ("off", "occasional", "coach")
BASE_GAP_MINUTES = {"occasional": 30.0, "coach": 10.0}
MAX_PER_RUN = {"occasional": 1, "coach": 4}
SNOOZE_DAYS = 7
TENURE_FADE_DAYS = 30
RECEPTIVENESS_RANGE = (-3.0, 3.0)


@dataclass
class MentorState:
    first_seen_ms: float = 0.0
    receptiveness: float = 0.0
    last_nudge_ms: float = 0.0
    last_topic: Optional[str] = None
    snoozed: dict = field(default_factory=dict)       # nudge id -> until ms
    celebrated: list = field(default_factory=list)    # achievement ids
    seeded: bool = False


@dataclass
class Nudge:
    id: str
    text: str
    action: Optional[str] = None   # taking this action counts as engaging
    kind: str = "frontier"         # continuity | hard_way | phantom | frontier


def state_path() -> Path:
    from .settings import get_overcode_dir
    return get_overcode_dir() / "journey_state.json"


def load_state() -> MentorState:
    try:
        data = json.loads(state_path().read_text())
        known = MentorState.__dataclass_fields__
        return MentorState(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return MentorState()


def save_state(state: MentorState) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(asdict(state), indent=1))
        tmp.replace(path)
    except OSError:
        pass


def mentor_dial() -> str:
    from .config import load_config
    section = load_config().get("journey")
    value = section.get("mentor") if isinstance(section, dict) else None
    value = str(value).lower() if value is not None else "off"
    if value in ("false", "0", "no"):
        return "off"
    return value if value in DIALS else "off"


def gap_ms(dial: str, receptiveness: float) -> float:
    """Minimum time between tips: the dial's base, halved per 1.5 points of receptiveness."""
    base = BASE_GAP_MINUTES.get(dial, 30.0) * 60_000
    return base * 2 ** (-receptiveness / 1.5)


def may_nudge(dial: str, state: MentorState, shown_this_run: int, now_ms: float) -> bool:
    if dial not in BASE_GAP_MINUTES:
        return False
    if shown_this_run >= MAX_PER_RUN[dial]:
        return False
    return now_ms - state.last_nudge_ms >= gap_ms(dial, state.receptiveness)


def candidates(journey, state: MentorState, now_ms: float) -> list[Nudge]:
    """Tips worth giving, best first. Only for things the journey shows you haven't got."""
    caps = {c.id: c for c in journey.catalog}
    out: list[Nudge] = []

    # 1. Continuity: next step in the track you were last shown.
    if state.last_topic:
        for t in journey.tracks:
            if any(c.id == state.last_topic for c in t.earned) and t.frontier:
                c = t.frontier[0]
                out.append(Nudge(f"next:{c.id}", f"Next in {t.label}: {c.name} — {c.why}",
                                 c.try_action, "continuity"))
                break

    # 2. The hard way: the most-used action done slowly, which has a key.
    for cid in journey.hard_way:
        cap = caps[cid]
        if cap.keys:
            m = journey.mastery[cid]
            out.append(Nudge(f"key:{cid}", f"{cap.title}: {cap.keys[0]} does it in one key "
                             f"(you've done it {m.uses}× the long way)", cid, "hard_way"))
            break

    # 3. A key you keep pressing that does nothing.
    for key, n in journey.phantom_keys:
        if n >= 3:
            out.append(Nudge(f"phantom:{key}", f"You've pressed {key} {n}× — it does nothing. "
                             "/ finds any command by name.", "command_palette", "phantom"))
            break

    # 4. The frontier: Basics first, then the rest in track order.
    for t in journey.tracks:
        for c in t.frontier:
            if c.try_action and journey.mastery.get(c.try_action) is not None \
                    and journey.mastery[c.try_action].level not in ("unaware", "seen"):
                continue  # already in use, just not enough to count yet
            key = ""
            cap = caps.get(c.try_action or "")
            if cap is not None and cap.keys:
                key = f" ({cap.keys[0]})"
            out.append(Nudge(f"next:{c.id}", f"{c.name}{key} — {c.why}", c.try_action, "frontier"))
            break

    seen: set = set()
    result = []
    for n in out:
        if n.id in seen or state.snoozed.get(n.id, 0) > now_ms:
            continue
        seen.add(n.id)
        result.append(n)
    if state.first_seen_ms and now_ms - state.first_seen_ms > TENURE_FADE_DAYS * 86_400_000:
        result = result[:1]
    return result


def choose(journey, state: MentorState, now_ms: float) -> Optional[Nudge]:
    c = candidates(journey, state, now_ms)
    return c[0] if c else None


def record_outcome(state: MentorState, nudge: Nudge, outcome: str, now_ms: float) -> None:
    """engaged: more tips welcome, un-snooze. ignored: fewer, and snooze this one for a week."""
    lo, hi = RECEPTIVENESS_RANGE
    if outcome == "engaged":
        state.receptiveness = min(hi, state.receptiveness + 1)
        state.snoozed.pop(nudge.id, None)
        if nudge.id.startswith("next:"):
            state.last_topic = nudge.id[5:]
    elif outcome == "ignored":
        state.receptiveness = max(lo, state.receptiveness - 0.5)
        state.snoozed[nudge.id] = now_ms + SNOOZE_DAYS * 86_400_000
    # Drop expired snoozes so the file can't grow without bound.
    state.snoozed = {k: v for k, v in state.snoozed.items() if v > now_ms}


def new_achievements(journey, state: MentorState) -> list:
    """Achievements to celebrate now. The first call seeds silently (no retroactive party)."""
    earned = [c for c, e in journey.achievements if e]
    if not state.seeded:
        state.celebrated = [c.id for c in earned]
        state.seeded = True
        return []
    fresh = [c for c in earned if c.id not in state.celebrated]
    state.celebrated += [c.id for c in fresh]
    return fresh


def now_ms() -> float:
    return time.time() * 1000
