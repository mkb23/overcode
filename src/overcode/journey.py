"""
The learning journey (#483): what you have found in overcode, and what's next.

Everything here is derived, never stored: the usage log (activity_log /
usage_analytics) plus residue — state files that show a feature in use (an
agent with a parent agent, a standing order, a wrapper, a sister host) —
fold into a mastery level per capability and progress through tracks of
competencies. The same compute_journey() feeds the TUI panel (`u`),
`overcode journey [--json]` and the overagent.

Design: docs/design/483-484/02_learning_journey.md. The tracks are a first
cut, and every threshold is a setting until real usage calibrates it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .usage_analytics import Summary

# ── mastery ladder ─────────────────────────────────────────────────────

LEVELS = ("unaware", "seen", "tried", "habitual", "fluent")
GLYPHS = {"unaware": "○", "seen": "◔", "tried": "◑", "habitual": "◐", "fluent": "●"}
HABITUAL_AT = 3
FLUENT_AT = 8
FLUENT_EFFICIENT_SHARE = 0.5
DEFAULT_DECAY_GRACE_DAYS = 30
DEFAULT_DECAY_STEP_DAYS = 45


@dataclass
class Capability:
    id: str
    title: str
    category: str
    keys: tuple = ()
    actions: tuple = ()            # usage-log action names that count
    residue: Optional[str] = None  # a Residue field whose count also counts
    tracked: bool = True           # False: nothing observable, "available to discover"
    residue_is_uses: bool = False  # the residue count *is* the uses (agents run on a backend)


@dataclass
class Mastery:
    level: str
    uses: int
    efficient_share: float
    last_t: Optional[float]
    hard_way: bool                 # used often, rarely by its key


@dataclass
class Residue:
    """Counts from state files: features in use, whatever way they were set up."""
    agents: int = 0
    live_agents: int = 0
    child_of_agent: int = 0        # agents launched by another agent
    standing_orders: int = 0
    budgets: int = 0
    tags: int = 0
    wrappers: int = 0
    heartbeats: int = 0
    annotations: int = 0
    values: int = 0                # agent_value changed from the default
    backends: int = 0              # distinct backends used, overagent and shell rows included
    agent_clis: int = 0            # distinct agent CLIs (claude-code, opencode, codex, …)
    backend_agents: dict = field(default_factory=dict)  # backend -> agents run on it
    shells: int = 0                # plain shell rows (#496)
    overagents: int = 0
    sisters: int = 0
    column_overrides: int = 0
    summarizer_prompts: int = 0
    # Skill profiles (#499)
    skill_profiles: int = 0        # profiles in config
    library_skills: int = 0        # skills in the library (installed but off)
    library_paths: int = 0         # extra library folders, e.g. skill repos
    folder_pins: int = 0
    default_profile: int = 0       # new_agent_defaults.skill_profile is set
    personal_skills: int = 0       # always-on skills Claude Code loads everywhere
    profiled_agents: int = 0       # agents launched with a profile
    mixed_profiles: int = 0        # profiles used on agents of two or more CLIs

    def get(self, name: str) -> int:
        if name.startswith("backend:"):
            return int(self.backend_agents.get(name.split(":", 1)[1], 0))
        return int(getattr(self, name, 0) or 0)


def mastery_for(uses: int, efficient_share: float, last_t: Optional[float], has_key: bool,
                now_ms: float, grace_days: float = DEFAULT_DECAY_GRACE_DAYS,
                step_days: float = DEFAULT_DECAY_STEP_DAYS, seen: bool = False) -> Mastery:
    if uses <= 0:
        level = "seen" if seen else "unaware"
    elif uses < HABITUAL_AT:
        level = "tried"
    elif has_key:
        level = "fluent" if efficient_share >= FLUENT_EFFICIENT_SHARE and uses >= HABITUAL_AT else "habitual"
    else:
        level = "fluent" if uses >= FLUENT_AT else "habitual"
    if uses > 0 and last_t is not None:
        age_days = (now_ms - last_t) / 86_400_000
        if age_days > grace_days:
            steps = 1 + int((age_days - grace_days) // step_days)
            level = LEVELS[max(LEVELS.index("tried"), LEVELS.index(level) - steps)]
    hard_way = has_key and uses >= HABITUAL_AT and efficient_share < FLUENT_EFFICIENT_SHARE
    return Mastery(level, uses, round(efficient_share, 3), last_t, hard_way)


# ── catalog ────────────────────────────────────────────────────────────

# Capabilities that aren't a palette action: CLI use and residue.
EXTRA_CAPABILITIES = (
    Capability("cli_launch", "Launch agents from the CLI", "CLI", actions=("cli:launch",)),
    Capability("child_agents", "Agents that launch agents", "Orchestration", residue="child_of_agent"),
    Capability("wrappers", "Launch through a wrapper", "Orchestration", residue="wrappers"),
    Capability("sisters", "Watch agents on other machines", "Orchestration", residue="sisters",
               actions=("open_sister_selection",)),
    Capability("backends", "Mix agent CLIs (codex, opencode, …)", "Orchestration", residue="agent_clis"),
    *(Capability(f"backend_{b}", f"Run {label} agents", "Backends", residue=f"backend:{b}",
                 residue_is_uses=True)
      for b, label in (("claude-code", "Claude Code"), ("opencode", "opencode"), ("codex", "Codex"),
                       ("grok", "Grok"), ("hermes", "Hermes"), ("shell", "plain shell"))),
    Capability("skill_profile_launch", "Launch agents with a skill profile", "Skills",
               residue="profiled_agents", residue_is_uses=True),
    Capability("cli_skills", "Manage skills from the CLI (overcode skills)", "CLI",
               actions=tuple(f"cli:skills {c}" for c in ("list", "profile", "pin", "unpin",
                                                          "library", "adopt"))),
    Capability("cli_view", "Script the TUI (overcode view)", "CLI",
               actions=tuple(f"cli:view {v}" for v in ("columns", "sort", "detail", "filter",
                                                        "focus", "toggle", "point", "notify"))),
    Capability("cli_activity", "Read your own usage (overcode activity)", "CLI",
               actions=("cli:activity summary", "cli:activity keys")),
    Capability("shareable_config", "Share a config across machines", "Settings", tracked=False),
)

# Palette actions that also count through residue (set up another way).
RESIDUE_FOR_ACTION = {
    "focus_standing_orders": "standing_orders",
    "edit_cost_budget": "budgets",
    "filter_by_tag": "tags",
    "configure_heartbeat": "heartbeats",
    "focus_human_annotation": "annotations",
    "edit_agent_value": "values",
    "open_column_config": "column_overrides",
    "open_summary_prompt_lab": "summarizer_prompts",
    "open_overagent": "overagents",
    "open_skills": "skill_profiles",
}

# Same capability, another action name that counts too.
EXTRA_ACTIONS = {
    "choose_sort": ("sort_by_column", "reverse_sort"),
    "new_agent": ("cli:launch",),
    "fork_focused": ("cli:fork",),
    "rename_focused": ("cli:rename",),
    "kill_focused": ("cli:kill",),
    "restart_focused": ("cli:restart",),
}


def build_catalog(keys_by_action: dict) -> list[Capability]:
    """Every palette command is a capability, plus the extras."""
    from .command_palette import COMMANDS, key_label
    caps = []
    for c in COMMANDS:
        caps.append(Capability(
            id=c.action, title=c.title, category=c.category,
            keys=tuple(key_label(k) for k in keys_by_action.get(c.action, [])),
            actions=(c.action,) + EXTRA_ACTIONS.get(c.action, ()),
            residue=RESIDUE_FOR_ACTION.get(c.action),
        ))
    return caps + list(EXTRA_CAPABILITIES)


# ── curriculum ─────────────────────────────────────────────────────────

@dataclass
class Signals:
    mastery: dict                  # capability id -> Mastery
    summary: Summary
    residue: Residue

    def uses(self, *ids: str) -> int:
        """Uses of capabilities, or of plain actions that aren't in the catalog (the palette key)."""
        total = 0
        for i in ids:
            if i in self.mastery:
                total += self.mastery[i].uses
            elif i in self.summary.actions:
                total += self.summary.actions[i].user_uses
        return total

    def key_uses(self, action: str) -> int:
        a = self.summary.actions.get(action)
        return a.by_via.get("key", 0) if a else 0


@dataclass
class Competency:
    id: str
    name: str
    why: str
    track: str
    criterion: Callable[[Signals], bool]
    prereqs: tuple = ()
    tier: str = "core"             # core counts toward the level; concept/advanced don't
    kind: str = "competency"       # or "achievement"
    try_action: Optional[str] = None


TRACKS = (
    ("basics", "Basics", "The moves every overcode user needs."),
    ("fleet", "Fleet", "Run many agents without losing any."),
    ("oversight", "Oversight", "See who needs you, fast."),
    ("orchestration", "Orchestration", "Agents that run agents, across machines."),
    ("skills", "Skills", "The right skills on for each agent, and nothing else."),
)

APPROVE = ("send_enter_to_focused", "send_1_to_focused", "send_2_to_focused", "send_3_to_focused")

COMPETENCIES = (
    # Basics
    Competency("b_navigate", "Move between agents", "j/k, or click a row.", "basics",
               lambda s: s.uses("focus_next_session", "focus_previous_session") >= 3,
               try_action="focus_next_session"),
    Competency("b_help", "Look something up in help", "h or ? lists every key.", "basics",
               lambda s: s.uses("toggle_help") >= 1, try_action="toggle_help"),
    Competency("b_palette", "Find anything with /", "Search every command by name; its key is shown.",
               "basics", lambda s: s.uses("command_palette") >= 1 and
               any(a.by_via.get("palette") for a in s.summary.actions.values()),
               try_action="command_palette"),
    Competency("b_send", "Send an instruction without attaching", "i opens the command bar for the focused agent.",
               "basics", lambda s: s.uses("focus_command_bar") >= 1, ("b_navigate",), try_action="focus_command_bar"),
    Competency("b_approve", "Answer an agent from the list", "Enter, 1, 2, 3 go to the focused agent's prompt.",
               "basics", lambda s: s.uses(*APPROVE) >= 3, ("b_navigate",)),
    # Fleet
    Competency("f_new", "Launch an agent", "n in the TUI, or overcode launch.", "fleet",
               lambda s: s.uses("new_agent", "cli_launch") >= 1 or s.residue.agents >= 2, try_action="new_agent"),
    Competency("f_rename", "Rename an agent", "Ctrl+N keeps its conversation.", "fleet",
               lambda s: s.uses("rename_focused") >= 1, ("f_new",), try_action="rename_focused"),
    Competency("f_lifecycle", "Restart or clean up an agent", "R restarts with its conversation; x kills.",
               "fleet", lambda s: s.uses("restart_focused", "kill_focused") >= 1, ("f_new",)),
    Competency("f_sleep", "Put an agent to sleep", "z: it stops counting toward stats.", "fleet",
               lambda s: s.uses("toggle_sleep") >= 1, ("f_new",), try_action="toggle_sleep"),
    Competency("f_orders", "Give standing orders", "o: instructions the supervisor keeps an agent to.",
               "fleet", lambda s: s.uses("focus_standing_orders") >= 1, ("f_new",), try_action="focus_standing_orders"),
    Competency("f_budget", "Cap an agent's spend", "B sets a cost budget.", "fleet",
               lambda s: s.uses("edit_cost_budget") >= 1, ("f_new",), try_action="edit_cost_budget"),
    Competency("f_fork", "Fork an agent", "F: a new agent with the same conversation.", "fleet",
               lambda s: s.uses("fork_focused") >= 1, ("f_new",)),
    Competency("f_shell", "Keep a shell in the list", "-B shell: a plain terminal as a row, next to your agents.",
               "fleet", lambda s: s.residue.shells >= 1, ("f_new",), tier="advanced"),
    # Oversight
    Competency("o_attention", "Jump to whoever needs you", "b goes straight to the next agent waiting on you.",
               "oversight", lambda s: s.uses("jump_to_attention") >= 3, ("b_navigate",), try_action="jump_to_attention"),
    Competency("o_sort", "Sort by what matters now", "S, or click a column header.", "oversight",
               lambda s: s.uses("choose_sort") >= 1, try_action="choose_sort"),
    Competency("o_detail", "Change how much each row shows", "s cycles low → med → high → full.", "oversight",
               lambda s: s.uses("cycle_summary") >= 2, try_action="cycle_summary"),
    Competency("o_columns", "Choose your columns", "C picks the columns for each detail level.", "oversight",
               lambda s: s.uses("open_column_config") >= 1, ("o_detail",), try_action="open_column_config"),
    Competency("o_timeline", "Read the timeline", "t: who was busy, when.", "oversight",
               lambda s: s.uses("toggle_timeline") >= 1, try_action="toggle_timeline"),
    Competency("o_tags", "Group agents with tags", "overcode tag, then T to filter.", "oversight",
               lambda s: s.uses("filter_by_tag") >= 1, ("f_new",), try_action="filter_by_tag"),
    Competency("o_summarizer", "Let AI summarise each agent", "A turns on the summarizer.", "oversight",
               lambda s: s.uses("toggle_summarizer") >= 1),
    # Orchestration
    Competency("x_overagent", "Ask the overagent", "e: overcode's own assistant.", "orchestration",
               lambda s: s.uses("open_overagent") >= 1, try_action="open_overagent"),
    Competency("x_children", "Have agents launch agents", "The overcode skill, or overcode launch from an agent.",
               "orchestration", lambda s: s.residue.child_of_agent >= 1, ("f_new",)),
    Competency("x_jobs", "Run background jobs", "overcode jobs, and J to see them.", "orchestration",
               lambda s: s.uses("toggle_tui_mode") >= 1),
    Competency("x_backends", "Mix agent CLIs", "codex, opencode, grok, hermes next to Claude.", "orchestration",
               lambda s: s.residue.agent_clis >= 2, ("f_new",)),
    Competency("x_sisters", "Watch other machines", "Sister hosts show their agents in your list.", "orchestration",
               lambda s: s.residue.sisters >= 1),
    Competency("x_wrappers", "Launch through a wrapper", "Containers or custom environments per agent.",
               "orchestration", lambda s: s.residue.wrappers >= 1, ("f_new",)),
    Competency("x_scripting", "Script overcode", "overcode view and overcode activity from scripts or agents.",
               "orchestration", lambda s: s.uses("cli_view", "cli_activity") >= 1, tier="advanced"),
    # Skills (#499)
    Competency("s_dialog", "Open the skills dialog", "W lists every skill, the ones agents use most first.",
               "skills", lambda s: s.uses("open_skills") >= 1, try_action="open_skills"),
    Competency("s_library", "Stock the library", "Skills in ~/.overcode/skills or a skills repo: installed, but off.",
               "skills", lambda s: s.residue.library_skills >= 1 or s.residue.library_paths >= 1),
    Competency("s_profile", "Make a skill profile", "A named set of skills: n in the W dialog, or overcode skills profile set.",
               "skills", lambda s: s.residue.skill_profiles >= 1, try_action="open_skills"),
    Competency("s_launch", "Launch an agent with a profile", "Its skills on, your other personal skills hidden, for that agent only.",
               "skills", lambda s: s.residue.profiled_agents >= 1, ("s_profile", "f_new"), try_action="new_agent"),
    Competency("s_pin", "Pin a profile to a folder", "p in the W dialog: new agents there get it by default.",
               "skills", lambda s: s.residue.folder_pins >= 1, ("s_profile",), try_action="open_skills"),
    Competency("s_several", "A profile per kind of work", "Three or more profiles, e.g. research, frontend, ops.",
               "skills", lambda s: s.residue.skill_profiles >= 3, ("s_profile",)),
    Competency("s_mixed", "One profile across CLIs", "The same profile on Claude Code and opencode agents.",
               "skills", lambda s: s.residue.mixed_profiles >= 1, ("s_launch", "x_backends")),
    Competency("s_default", "Set a default profile", "G: new agents get it when neither parent nor folder sets one.",
               "skills", lambda s: s.residue.default_profile >= 1, ("s_profile",), tier="advanced",
               try_action="open_new_agent_defaults"),
    # Achievements: workflows, not clicks
    Competency("a_polyglot", "Polyglot", "Ran the same action by key, palette and click.", "",
               lambda s: any({"key", "palette", "click"} <= set(a.by_via) for a in s.summary.actions.values()),
               kind="achievement"),
    Competency("a_keyboard", "Keyboard native", "Fifteen different actions by their keys.", "",
               lambda s: sum(1 for a in s.summary.actions.values() if a.by_via.get("key")) >= 15,
               kind="achievement"),
    Competency("a_fleet", "Fleet of five", "Five agents live at once.", "",
               lambda s: s.residue.live_agents >= 5, kind="achievement"),
    Competency("a_hands_off", "Hands off the wheel", "Answered twenty prompts without attaching.", "",
               lambda s: s.uses(*APPROVE) >= 20, kind="achievement"),
    Competency("a_three_clis", "Three CLIs", "Agents on three different agent CLIs.", "",
               lambda s: s.residue.agent_clis >= 3, kind="achievement"),
    Competency("a_profiles", "Profile collector", "Five skill profiles.", "",
               lambda s: s.residue.skill_profiles >= 5, kind="achievement"),
    Competency("a_nothing_always_on", "Nothing always on", "Every skill in the library, none loaded everywhere.", "",
               lambda s: s.residue.library_skills >= 1 and s.residue.personal_skills == 0,
               kind="achievement"),
)


# ── the journey ────────────────────────────────────────────────────────

@dataclass
class TrackProgress:
    id: str
    label: str
    blurb: str
    earned: list = field(default_factory=list)
    frontier: list = field(default_factory=list)
    locked: list = field(default_factory=list)
    upcoming: list = field(default_factory=list)
    core_total: int = 0

    @property
    def level(self) -> int:
        return sum(1 for c in self.earned if c.tier == "core")


@dataclass
class Journey:
    catalog: list
    mastery: dict
    tracks: list
    achievements: list             # (Competency, earned)
    hard_way: list                 # Capability ids used often, rarely by key
    phantom_keys: list             # (key, count)
    earned_ids: set
    records: int

    def to_dict(self) -> dict:
        def comp(c: Competency) -> dict:
            return {"id": c.id, "name": c.name, "why": c.why, "tier": c.tier,
                    "try_action": c.try_action, "prereqs": list(c.prereqs)}
        caps = {c.id: c for c in self.catalog}
        return {
            "records": self.records,
            "tracks": [{
                "id": t.id, "label": t.label, "blurb": t.blurb,
                "level": t.level, "core_total": t.core_total,
                "earned": [comp(c) for c in t.earned],
                "next": [comp(c) for c in t.frontier],
                "locked": [{**comp(c), "needs": [p for p in c.prereqs if p not in self.earned_ids]}
                           for c in t.locked],
            } for t in self.tracks],
            "achievements": [{**comp(c), "earned": e} for c, e in self.achievements],
            "hard_way": [{"id": i, "title": caps[i].title, "keys": list(caps[i].keys),
                          "uses": self.mastery[i].uses,
                          "by_key_share": self.mastery[i].efficient_share} for i in self.hard_way],
            "phantom_keys": [{"key": k, "count": n} for k, n in self.phantom_keys],
            "capabilities": {
                c.id: {"title": c.title, "category": c.category, "keys": list(c.keys),
                       "tracked": c.tracked, "level": self.mastery[c.id].level,
                       "uses": self.mastery[c.id].uses}
                for c in self.catalog
            },
        }


def compute_journey(summary: Summary, residue: Residue, keys_by_action: dict,
                    now_ms: Optional[float] = None, seen: frozenset = frozenset(),
                    grace_days: float = DEFAULT_DECAY_GRACE_DAYS,
                    step_days: float = DEFAULT_DECAY_STEP_DAYS) -> Journey:
    now_ms = now_ms if now_ms is not None else time.time() * 1000
    catalog = build_catalog(keys_by_action)
    mastery: dict = {}
    for cap in catalog:
        uses = efficient = 0
        last_t = None
        for name in cap.actions:
            a = summary.actions.get(name)
            if a is None:
                continue
            uses += a.user_uses
            efficient += sum(a.by_via.get(v, 0) for v in ("key", "palette"))
            if a.last_t is not None:
                last_t = a.last_t if last_t is None else max(last_t, a.last_t)
        if cap.residue_is_uses:
            uses = max(uses, residue.get(cap.residue))
        elif cap.residue and residue.get(cap.residue) and uses == 0:
            uses = 1  # set up some other way: at least tried
        share = efficient / uses if uses else 0.0
        if cap.keys and uses:
            # For a keyed action, "efficient" means by its key specifically.
            keyed = sum(summary.actions[n].by_via.get("key", 0) for n in cap.actions if n in summary.actions)
            share = keyed / uses
        mastery[cap.id] = mastery_for(uses, share, last_t, bool(cap.keys), now_ms,
                                      grace_days, step_days, seen=cap.id in seen)
    signals = Signals(mastery, summary, residue)

    earned: set = set()
    # Prereqs can point forward in the catalog: settle in passes.
    for _ in range(3):
        for c in COMPETENCIES:
            if c.id not in earned and _safe(c.criterion, signals):
                earned.add(c.id)

    tracks = []
    for tid, label, blurb in TRACKS:
        tp = TrackProgress(tid, label, blurb)
        for c in COMPETENCIES:
            if c.track != tid or c.kind != "competency":
                continue
            if c.tier == "core":
                tp.core_total += 1
            if c.id in earned:
                tp.earned.append(c)
            elif all(p in earned for p in c.prereqs):
                (tp.frontier if len(tp.frontier) < 3 else tp.upcoming).append(c)
            else:
                tp.locked.append(c)
        tracks.append(tp)

    achievements = [(c, c.id in earned) for c in COMPETENCIES if c.kind == "achievement"]
    hard_way = sorted((cid for cid, m in mastery.items() if m.hard_way),
                      key=lambda cid: -mastery[cid].uses)
    return Journey(catalog, mastery, tracks, achievements, hard_way,
                   summary.phantom_keys.most_common(5), earned, summary.records)


NAMES = {c.id: c.name for c in COMPETENCIES}


def needs(c: Competency, earned: set) -> str:
    """The unmet prerequisites, by name."""
    return ", ".join(NAMES.get(p, p) for p in c.prereqs if p not in earned)


def _safe(fn: Callable, arg) -> bool:
    try:
        return bool(fn(arg))
    except Exception:
        return False


# ── gathering (I/O) ────────────────────────────────────────────────────

# Per-session counters summed into a Residue. Sets: tags, and "backend" plus
# "profile\tbackend" pairs (#499), so a profile used on two CLIs shows up.
_SESSION_COUNTERS = ("agents", "child_of_agent", "standing_orders", "budgets", "wrappers",
                     "heartbeats", "annotations", "values", "overagents", "shells",
                     "profiled_agents")
_ARCHIVE_CACHE_VERSION = 2
# Rows that aren't an agent CLI: overcode's own assistant and plain shells.
_NOT_AGENT_CLIS = frozenset({"overagent", "shell"})


def _count_sessions(sessions, counts: dict, tags: set, backends: set,
                    backend_agents: Optional[dict] = None,
                    profile_backends: Optional[set] = None) -> None:
    for s in sessions:
        backend = getattr(s, "backend", None) or "claude-code"
        profile = getattr(s, "skill_profile", None)
        if backend_agents is not None:
            backend_agents[backend] = backend_agents.get(backend, 0) + 1
        if profile_backends is not None and isinstance(profile, str) and profile:
            profile_backends.add(f"{profile}\t{backend}")
        counts["shells"] += backend == "shell"
        counts["profiled_agents"] += bool(isinstance(profile, str) and profile)
        counts["agents"] += 1
        counts["child_of_agent"] += bool(s.parent_session_id)
        counts["standing_orders"] += bool(s.standing_instructions)
        counts["budgets"] += bool(s.cost_budget_usd)
        counts["wrappers"] += bool(s.wrapper)
        counts["heartbeats"] += bool(s.heartbeat_enabled)
        counts["annotations"] += bool(s.human_annotation)
        counts["values"] += s.agent_value != 1000
        counts["overagents"] += backend == "overagent"
        tags.update(t for t in (s.tags or []) if isinstance(t, str))
        backends.add(backend)


def _archive_counts(sm) -> tuple[dict, set, set, dict, set]:
    """Counters over every archived session, read incrementally.

    The archive only grows (20k sessions is the design target), so the counts
    are cached next to it with the byte offset they cover, and each call
    parses only what was appended since. A replaced or shrunk archive is
    recounted from the start.
    """
    path = sm.state_dir / "archive_residue.json"
    counts = dict.fromkeys(_SESSION_COUNTERS, 0)
    tags: set = set()
    backends: set = set()
    backend_agents: dict = {}
    profile_backends: set = set()
    offset, inode = 0, None
    try:
        cached = json.loads(path.read_text())
        if cached.get("v") == _ARCHIVE_CACHE_VERSION:
            counts.update({k: int(cached["counts"][k]) for k in _SESSION_COUNTERS})
            tags, backends = set(cached["tags"]), set(cached["backends"])
            backend_agents = {str(k): int(v) for k, v in cached["backend_agents"].items()}
            profile_backends = set(cached["profile_backends"])
            offset, inode = int(cached["offset"]), cached["inode"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    sessions, new_offset, new_inode, reset = sm.archived_sessions_since(offset, inode)
    if reset:
        counts = dict.fromkeys(_SESSION_COUNTERS, 0)
        tags, backends, backend_agents, profile_backends = set(), set(), {}, set()
    _count_sessions(sessions, counts, tags, backends, backend_agents, profile_backends)
    if new_offset != offset or new_inode != inode:
        try:
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({
                "v": _ARCHIVE_CACHE_VERSION, "offset": new_offset, "inode": new_inode,
                "counts": counts, "tags": sorted(tags), "backends": sorted(backends),
                "backend_agents": backend_agents, "profile_backends": sorted(profile_backends)}))
            os.replace(tmp, path)
        except OSError:
            pass
    return counts, tags, backends, backend_agents, profile_backends


def gather_residue() -> Residue:
    """Count features in use from sessions.json, the archive, config and prefs."""
    from .session_manager import SessionManager
    r = Residue()
    counts = dict.fromkeys(_SESSION_COUNTERS, 0)
    tags: set = set()
    backends: set = set()
    backend_agents: dict = {}
    profile_backends: set = set()
    try:
        sm = SessionManager()
        live = sm.list_sessions()
        counts, tags, backends, backend_agents, profile_backends = _archive_counts(sm)
        _count_sessions(live, counts, tags, backends, backend_agents, profile_backends)
    except Exception:
        live = []
    for name, n in counts.items():
        setattr(r, name, n)
    r.tags = len(tags)
    r.backends = len(backends)
    r.agent_clis = len(backends - _NOT_AGENT_CLIS)
    r.backend_agents = backend_agents
    clis_by_profile: dict = {}
    for pair in profile_backends:
        profile, _, backend = pair.partition("\t")
        if backend not in _NOT_AGENT_CLIS:
            clis_by_profile.setdefault(profile, set()).add(backend)
    r.mixed_profiles = sum(1 for clis in clis_by_profile.values() if len(clis) >= 2)
    try:
        _skills_residue(r)
    except Exception:
        pass
    r.live_agents = sum(1 for s in live if s.status not in ("terminated", "done", "archived"))
    try:
        from .config import get_sisters_config
        r.sisters = len(get_sisters_config())
    except Exception:
        pass
    try:
        from .settings import TUIPreferences, get_overcode_dir
        r.column_overrides = int(bool(TUIPreferences.load("agents").column_config))
        prompts = get_overcode_dir() / "summarizer_prompts"
        r.summarizer_prompts = len(list(prompts.glob("*"))) if prompts.is_dir() else 0
    except Exception:
        pass
    return r


def _skills_residue(r: Residue) -> None:
    """Skill profiles, library and pins from config; always-on skills from disk (#499)."""
    from . import skill_library
    from .config import get_new_agent_defaults
    r.skill_profiles = len(skill_library.get_profiles())
    r.library_paths = len(skill_library.library_paths())
    r.folder_pins = len(skill_library.get_folder_pins())
    r.library_skills = len(skill_library.scan_library())
    r.personal_skills = len(skill_library.personal_skills("claude-code"))
    r.default_profile = int(bool(get_new_agent_defaults().get("skill_profile")))


def journey_settings() -> tuple[float, float]:
    from .config import load_config
    section = load_config().get("journey")
    section = section if isinstance(section, dict) else {}
    try:
        grace = float(section.get("decay_grace_days", DEFAULT_DECAY_GRACE_DAYS))
        step = float(section.get("decay_step_days", DEFAULT_DECAY_STEP_DAYS))
    except (TypeError, ValueError):
        grace, step = DEFAULT_DECAY_GRACE_DAYS, DEFAULT_DECAY_STEP_DAYS
    return grace, max(1.0, step)


def load_journey(keys_by_action: dict, bound_keys: frozenset) -> Journey:
    """The whole journey from the usage log and state files: the one loader every surface uses."""
    from .activity_log import summarize_log
    summary = summarize_log(None, bound_keys, keys_by_action)
    grace, step = journey_settings()
    return compute_journey(summary, gather_residue(), keys_by_action,
                           grace_days=grace, step_days=step)


def render_journey(j: Journey) -> str:
    """Plain text for `overcode journey`."""
    out = [f"Your overcode journey ({j.records} records)"]
    for t in j.tracks:
        bar = _bar(t.level, t.core_total, 12)
        out.append(f"\n  {t.label:<14} {bar} {t.level}/{t.core_total}   {t.blurb}")
        for c in t.earned:
            out.append(f"    ✓ {c.name}")
        for c in t.frontier:
            out.append(f"    → {c.name} — {c.why}")
        for c in t.locked:
            out.append(f"    🔒 {c.name} (needs {needs(c, j.earned_ids)})")
    if j.hard_way:
        out.append("\n  The hard way")
        caps = {c.id: c for c in j.catalog}
        for cid in j.hard_way[:5]:
            c, m = caps[cid], j.mastery[cid]
            out.append(f"    {c.title}: {m.uses}× but only {int(m.efficient_share * 100)}% by its key "
                       f"({', '.join(c.keys)})")
    if j.phantom_keys:
        out.append("\n  Keys you press that do nothing: "
                   + ", ".join(f"{k} ({n}×)" for k, n in j.phantom_keys))
    got = [c.name for c, e in j.achievements if e]
    out.append(f"\n  Achievements: {len(got)}/{len(j.achievements)}"
               + (f" — {', '.join(got)}" if got else ""))
    return "\n".join(out)


def _bar(n: int, total: int, width: int) -> str:
    filled = round(width * n / total) if total else 0
    return "█" * filled + "░" * (width - filled)
