"""
The naming policy: checks on an agent name, with no model (#542).

docs/design/auto-naming.md §2a and §5 steps 1-2. One checker serves both
the CLI, which checks the ``-n`` a parent agent gives a child, and the
namer (P2), which filters its model's candidates through it, so a name
the namer picks always passes:

    ctx = NamingContext.from_fleet(rules, sessions, ...)
    check(name, ctx) -> Verdict          # one name: failures + a suggestion
    choose(candidates, ctx) -> name      # the first that passes, else a disambiguated one

The free-text advice (``naming.advice``, ``<repo>/.overcode/naming.md``)
is the primary policy; it is read by a model (the namer, ``llm_check``),
never here. The rules here are an optional supplement, every one off by
default. Two checks always apply (``HARD_RULES``): overcode's own name
pattern, and a name that is taken (a live agent's name or alias), and
``--force-name`` gets past neither.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

from .exceptions import InvalidSessionNameError

# Rule ids, as a failure names them
RULE_BASE_PATTERN = "base_pattern"
RULE_RESERVED = "reserved"
RULE_TAKEN = "taken"
RULE_PATTERN = "pattern"
RULE_MAX_LENGTH = "max_length"
RULE_FORBID = "forbid"
RULE_REQUIRE = "require"
RULE_NO_REPO_PREFIX = "no_repo_prefix"
RULE_RECENT_ARCHIVED = "recent_archived"
RULE_ADVICE = "advice"

HARD_RULES = frozenset({RULE_BASE_PATTERN, RULE_RESERVED, RULE_TAKEN})

# The supervisor leaves an agent called this alone (rename.RESERVED_AGENT_NAMES)
RESERVED_NAMES = frozenset({"daemon_claude"})

_BASE_PATTERN = re.compile(InvalidSessionNameError.VALID_PATTERN)
_BASE_MAX = 64


@dataclass(frozen=True)
class NamingRules:
    """``naming.rules`` (config.get_naming_config): every check off unless set."""

    pattern: Optional[str] = None
    max_length: Optional[int] = None
    forbid: Sequence[str] = ()
    require: Optional[str] = None
    no_repo_prefix: bool = False
    recent_archived_hours: float = 0.0
    llm_check: bool = False
    warn_humans: bool = False

    @classmethod
    def from_config(cls, rules: Mapping) -> "NamingRules":
        return cls(
            pattern=rules.get("pattern"),
            max_length=rules.get("max_length"),
            forbid=tuple(rules.get("forbid") or ()),
            require=rules.get("require"),
            no_repo_prefix=bool(rules.get("no_repo_prefix")),
            recent_archived_hours=float(rules.get("recent_archived_hours") or 0.0),
            llm_check=bool(rules.get("llm_check")),
            warn_humans=bool(rules.get("warn_humans")),
        )


@dataclass
class NamingContext:
    """What a name is checked against: the rules and the fleet.

    ``taken`` maps each live name and live alias to who holds it ("agent
    'x'", "alias of 'y'"); ``recent`` the names archived within
    ``rules.recent_archived_hours``. ``self_id``'s own names are not taken
    (renaming an agent back to an old name of its own).
    """

    rules: NamingRules = field(default_factory=NamingRules)
    taken: Mapping[str, str] = field(default_factory=dict)
    recent: Mapping[str, str] = field(default_factory=dict)
    repo: Optional[str] = None
    repo_column_shown: bool = False

    @classmethod
    def from_fleet(
        cls,
        rules: NamingRules,
        sessions: Iterable,
        *,
        archived: Iterable[Mapping] = (),
        repo: Optional[str] = None,
        self_id: Optional[str] = None,
        now: Optional[datetime] = None,
        names_taken: bool = True,
    ) -> "NamingContext":
        """A context from session records and archive records (raw dicts).

        Without ``names_taken`` an agent's current name is not taken (its
        old names still are): ``launch`` and ``fork`` decide what an
        existing name means, returning a live agent and replacing a stale
        record, as they did before the policy.

        Sister agents are left out (not named, not unique across machines).
        The repo column shows when the agents (with ``repo``) are in more
        than one repo, as the TUI decides (``hide_when_uniform``).
        """
        taken: dict = {}
        repos = {repo} if repo else set()
        for s in sessions:
            if getattr(s, "is_remote", False) or s.id == self_id:
                continue
            if names_taken:
                taken.setdefault(s.name, f"agent '{s.name}'")
            for alias in s.previous_names or ():
                taken.setdefault(alias, f"an old name of '{s.name}'")
            if s.repo_name:
                repos.add(s.repo_name)
        recent: dict = {}
        hours = rules.recent_archived_hours
        if hours > 0:
            cutoff = (now or datetime.now()) - timedelta(hours=hours)
            for record in archived:
                when = record.get("end_time") or record.get("start_time")
                try:
                    ended = datetime.fromisoformat(when) if isinstance(when, str) else None
                except ValueError:
                    ended = None
                if ended is None:
                    continue
                if ended.tzinfo is not None:
                    ended = ended.astimezone().replace(tzinfo=None)
                if ended < cutoff or record.get("id") == self_id:
                    continue
                for name in [record.get("name"), *(record.get("previous_names") or [])]:
                    if isinstance(name, str) and name:
                        recent.setdefault(name, f"archived agent '{record.get('name')}'")
        return cls(rules=rules, taken=taken, recent=recent, repo=repo,
                   repo_column_shown=len(repos) > 1)


@dataclass(frozen=True)
class Failure:
    rule: str
    message: str

    @property
    def hard(self) -> bool:
        return self.rule in HARD_RULES


@dataclass
class Verdict:
    """A name's check: what failed, and a name that would pass (None if none found)."""

    name: str
    failures: List[Failure] = field(default_factory=list)
    suggestion: Optional[str] = None

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def hard(self) -> bool:
        """A failure ``--force-name`` cannot get past."""
        return any(f.hard for f in self.failures)

    def message(self, advice: str = "") -> str:
        """The refusal, written for the agent that chose the name."""
        reasons = "; ".join(f.message for f in self.failures)
        text = f"name '{self.name}' breaks the naming policy ({reasons})"
        if self.suggestion:
            text += f"; try '{self.suggestion}'"
        if not self.hard:
            text += ", or pass --force-name"
        if advice:
            text += f"\nnaming advice:\n{advice}"
        return text

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "hard": self.hard,
            "failures": [{"rule": f.rule, "message": f.message, "hard": f.hard}
                         for f in self.failures],
            "suggestion": self.suggestion,
        }


def normalise(name: str) -> str:
    """Lowercase kebab: every run of other characters one ``-``, none at the ends."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _words(name: str) -> List[str]:
    return [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]


def _repo_prefixed(name: str, repo: str) -> bool:
    n, r = name.lower(), repo.lower()
    return n == r or (n.startswith(r) and not n[len(r):len(r) + 1].isalnum())


def failures(name: str, ctx: NamingContext) -> List[Failure]:
    """Every rule ``name`` breaks, hard ones first."""
    out: List[Failure] = []
    rules = ctx.rules
    if not name or not _BASE_PATTERN.match(name):
        out.append(Failure(RULE_BASE_PATTERN,
                           f"letters, digits, - and _ only, at most {_BASE_MAX} characters"))
    if name in RESERVED_NAMES:
        out.append(Failure(RULE_RESERVED, "reserved by overcode"))
    if name in ctx.taken:
        out.append(Failure(RULE_TAKEN, f"taken by {ctx.taken[name]}"))
    if rules.pattern and not re.search(rules.pattern, name):
        out.append(Failure(RULE_PATTERN, f"does not match the pattern {rules.pattern}"))
    if rules.max_length and len(name) > rules.max_length:
        out.append(Failure(RULE_MAX_LENGTH,
                           f"{len(name)} characters, more than {rules.max_length}"))
    words = _words(name)
    for word in rules.forbid:
        if word in words:
            out.append(Failure(RULE_FORBID, f"forbidden word '{word}'"))
            break
    if rules.require and not re.search(rules.require, name):
        out.append(Failure(RULE_REQUIRE, f"must match {rules.require}"))
    if (rules.no_repo_prefix and ctx.repo and ctx.repo_column_shown
            and _repo_prefixed(name, ctx.repo)):
        out.append(Failure(RULE_NO_REPO_PREFIX,
                           f"repeats the repo name '{ctx.repo}', which has its own column"))
    if name in ctx.recent and name not in ctx.taken:
        out.append(Failure(RULE_RECENT_ARCHIVED, f"recently used by {ctx.recent[name]}"))
    return out


def _passes(name: str, ctx: NamingContext) -> bool:
    return bool(name) and not failures(name, ctx)


def _repair(name: str, ctx: NamingContext) -> str:
    """``name`` with what can be fixed mechanically fixed: forbidden words
    and a repo prefix dropped, cut to the length limit at a word boundary."""
    rules = ctx.rules
    fixed = name
    if rules.pattern and not re.search(rules.pattern, fixed):
        fixed = normalise(fixed) or fixed
    sep = "-" if "-" in fixed or "_" not in fixed else "_"
    parts = [p for p in re.split(r"[-_]+", fixed) if p]
    if rules.no_repo_prefix and ctx.repo and ctx.repo_column_shown:
        repo_parts = [p for p in re.split(r"[^A-Za-z0-9]+", ctx.repo) if p]
        if len(parts) > len(repo_parts) and [p.lower() for p in parts[:len(repo_parts)]] == [
                p.lower() for p in repo_parts]:
            parts = parts[len(repo_parts):]
    if rules.forbid:
        kept = [p for p in parts if p.lower() not in rules.forbid]
        parts = kept or parts
    fixed = sep.join(parts) or fixed
    limit = rules.max_length
    if limit and len(fixed) > limit:
        cut = fixed[:limit]
        if sep in cut and fixed[limit:limit + 1] not in ("", sep):
            cut = cut[:cut.rindex(sep)]
        fixed = cut.strip("-_") or fixed[:limit]
    return fixed


def _numbered(name: str, ctx: NamingContext) -> Iterable[str]:
    """The shortest disambiguators: the next number of a numbered name
    (``wiki1`` → ``wiki2``), else ``-2``, ``-3``…, kept within the length limit."""
    limit = ctx.rules.max_length or _BASE_MAX
    m = re.fullmatch(r"(.*?)([0-9]+)", name)
    for n in range(2, 100):
        if m and m.group(1):
            stem, suffix = m.group(1), str(int(m.group(2)) + n - 1)
        else:
            stem, suffix = name, f"-{n}"
        yield stem[:max(1, limit - len(suffix))].rstrip("-_") + suffix


def suggest(name: str, ctx: NamingContext) -> Optional[str]:
    """A name close to ``name`` that passes, or None."""
    base = _repair(name, ctx)
    if not _BASE_PATTERN.match(base or "-"):
        base = normalise(name)
    if not base:
        return None
    left = failures(base, ctx)
    if not left:
        return base if base != name else None
    # A number disambiguates a collision; it must not pass for an issue number
    if any(f.rule not in (RULE_TAKEN, RULE_RECENT_ARCHIVED) for f in left):
        return None
    for candidate in _numbered(base, ctx):
        if _passes(candidate, ctx):
            return candidate
    return None


def check(name: str, ctx: NamingContext) -> Verdict:
    """Check one name as given (not normalised)."""
    found = failures(name, ctx)
    return Verdict(name=name, failures=found, suggestion=suggest(name, ctx) if found else None)


def choose(candidates: Sequence[str], ctx: NamingContext) -> Optional[str]:
    """The namer's pick (§5 steps 1-3): each candidate normalised, the first
    that passes; else the first one with the shortest disambiguator. None
    when no candidate yields a passing name."""
    names = [n for n in (normalise(c) for c in candidates if isinstance(c, str)) if n]
    for name in names:
        if _passes(name, ctx):
            return name
    for name in names:
        fixed = suggest(name, ctx)
        if fixed is not None:
            return fixed
    return None


# ── advice ────────────────────────────────────────────────────────────────


PROJECT_ADVICE = Path(".overcode") / "naming.md"


def project_advice_path(directory: Optional[str]) -> Optional[Path]:
    """``<repo>/.overcode/naming.md`` for the repo holding ``directory``, if there is one.

    Looks in ``directory`` and its parents up to the repo root (the first
    one with ``.git``); outside a repo, in ``directory`` only.
    """
    if not directory:
        return None
    try:
        start = Path(directory).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    for d in (start, *start.parents):
        candidate = d / PROJECT_ADVICE
        if candidate.is_file():
            return candidate
        if (d / ".git").exists():
            return None
    return None


def load_advice(config_advice: str, directory: Optional[str] = None) -> str:
    """The naming advice: config's ``naming.advice``, then the project's file."""
    parts = [config_advice.strip()] if config_advice and config_advice.strip() else []
    path = project_advice_path(directory)
    if path is not None:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            text = ""
        if text:
            parts.append(text)
    return "\n\n".join(parts)


# ── the model check (rules.llm_check) ────────────────────────────────────


LLM_CHECK_PROMPT = """An AI agent wants to name a child agent '{name}'. Does the name follow this naming advice?

## Naming advice:
{advice}

Reply with JSON only: {{"ok": true}} or {{"ok": false, "why": "<one short reason>", "suggestion": "<a better name>"}}"""

LLM_CHECK_TIMEOUT = 5.0


def llm_check(name: str, advice: str, client=None,
              timeout: float = LLM_CHECK_TIMEOUT) -> Optional[Tuple[Failure, Optional[str]]]:
    """Ask the summarizer model whether ``name`` follows ``advice``: one call.

    Returns None when it does, else the failure and the model's suggested
    name (None if it gave none). Fails open: no advice, no client, an
    error, a timeout or an unreadable reply all return None.
    """
    import json
    if not advice.strip():
        return None
    try:
        if client is None:
            from .summarizer_client import SummarizerClient
            client = SummarizerClient()
        if not client.available:
            return None
        reply = client.complete(LLM_CHECK_PROMPT.format(name=name, advice=advice),
                                max_tokens=120, timeout=timeout)
    except Exception:
        return None
    if not reply:
        return None
    m = re.search(r"\{.*\}", reply, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("ok") is not False:
        return None
    why = data.get("why") if isinstance(data.get("why"), str) else "does not follow the advice"
    suggestion = data.get("suggestion")
    suggestion = normalise(suggestion) if isinstance(suggestion, str) else None
    return Failure(RULE_ADVICE, f"advice: {why.strip()[:120]}"), suggestion or None
