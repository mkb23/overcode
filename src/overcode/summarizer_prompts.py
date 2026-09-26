"""
Prompts for the AI summarizer, editable in ~/.overcode/prompts/ (#491).

Two prompts drive the summary column: ``short`` (what the agent is doing
this second) and ``context`` (the task it is working on). Each lives in
``~/.overcode/prompts/summary-<mode>.md`` once saved; until then the
built-in default below is used. The files are only ever written by an
explicit save (the prompt lab dialog), never as a side effect of reading.

Templates may use these placeholders; anything else in braces is left
alone, so a prompt can quote JSON or code without escaping:

    {pane_content}      the captured terminal text
    {lines}             how many lines were captured
    {previous_summary}  the last summary (anti-oscillation)
    {status}            the agent's detected status (running, waiting_user…)
"""

import os
import threading
from pathlib import Path
from typing import Dict, Tuple

from .settings import get_overcode_dir

MODES = ("short", "context")

PLACEHOLDERS = ("pane_content", "lines", "previous_summary", "status")

# Short summary prompt - focuses on IMMEDIATE ACTION (verb-first, what's happening this second)
DEFAULT_PROMPT_SHORT = """What is the agent doing RIGHT NOW? Answer with the immediate action only.

## Terminal (last {lines} lines):
{pane_content}

## Previous:
{previous_summary}

FORMAT: Start with a verb. Examples:
- "reading src/auth.py"
- "running pytest -v"
- "waiting for approval"
- "writing migration file"
- "editing line 45"

RULES:
- Verb first, always (reading/writing/running/waiting/editing/fixing)
- Name the specific file or command if visible
- Max 40 chars
- If unchanged: UNCHANGED"""

# Context summary prompt - focuses on THE TASK (noun-first, the feature/bug/goal)
DEFAULT_PROMPT_CONTEXT = """What TASK or FEATURE is being worked on? Not the current action - the goal.

## Terminal (last {lines} lines):
{pane_content}

## Previous:
{previous_summary}

FORMAT: Describe the task/feature/bug. Examples:
- "JWT auth migration"
- "user search pagination"
- "fix: race condition in queue"
- "PR #42 review comments"
- "new settings dark mode"

RULES:
- Noun/task first (not a verb like "implementing")
- Include ticket/PR numbers if mentioned
- Focus on WHAT is being built/fixed, not HOW
- Max 60 chars
- If unchanged: UNCHANGED"""

DEFAULT_PROMPTS = {"short": DEFAULT_PROMPT_SHORT, "context": DEFAULT_PROMPT_CONTEXT}

# path -> ((st_mtime_ns, st_size), text). The summarizer asks for a prompt
# on every call (several a second across a fleet), so a saved file costs
# one stat per call and is re-read only when it changes.
_cache: Dict[Path, Tuple[Tuple[int, int], str]] = {}
_cache_lock = threading.Lock()


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown summary prompt mode '{mode}' (expected one of {MODES})")


def prompt_path(mode: str) -> Path:
    """Where the ``mode`` prompt is saved: ~/.overcode/prompts/summary-<mode>.md."""
    _check_mode(mode)
    return get_overcode_dir() / "prompts" / f"summary-{mode}.md"


def load_prompt(mode: str) -> str:
    """The ``mode`` prompt: the saved file when there is a non-blank one,
    else the built-in default."""
    path = prompt_path(mode)
    try:
        st = path.stat()
    except OSError:
        return DEFAULT_PROMPTS[mode]
    sig = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        cached = _cache.get(path)
        if cached is not None and cached[0] == sig:
            text = cached[1]
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return DEFAULT_PROMPTS[mode]
            _cache[path] = (sig, text)
    return text if text.strip() else DEFAULT_PROMPTS[mode]


def is_customised(mode: str) -> bool:
    """True when a saved prompt file overrides the default."""
    return load_prompt(mode) != DEFAULT_PROMPTS[mode]


def save_prompt(mode: str, text: str) -> Path:
    """Save ``text`` as the ``mode`` prompt (atomically); returns the path."""
    path = prompt_path(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path


def reset_prompt(mode: str) -> None:
    """Delete the saved ``mode`` prompt so the built-in default applies."""
    try:
        prompt_path(mode).unlink()
    except FileNotFoundError:
        pass


def render_prompt(
    template: str,
    *,
    pane_content: str,
    lines: int,
    previous_summary: str,
    status: str,
) -> str:
    """Fill the known placeholders; leave every other brace untouched.

    Not ``str.format``: a hand-edited prompt with a stray ``{`` (or quoted
    JSON) must still work rather than raise on every call. Substitution is
    a single pass, so braces inside the pane content are never expanded.
    """
    values = {
        "pane_content": pane_content,
        "lines": str(lines),
        "previous_summary": previous_summary or "(no previous summary)",
        "status": status,
    }
    out = []
    i = 0
    while True:
        j = template.find("{", i)
        if j < 0:
            out.append(template[i:])
            break
        k = template.find("}", j)
        name = template[j + 1:k] if k > j else None
        if name in values:
            out.append(template[i:j])
            out.append(values[name])
            i = k + 1
        else:
            out.append(template[i:j + 1])
            i = j + 1
    return "".join(out)


def unknown_placeholders(template: str) -> list:
    """``{names}`` in ``template`` that render_prompt will not fill — shown
    as a warning in the lab, since they are usually typos."""
    import re
    found = re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template)
    return sorted({n for n in found if n not in PLACEHOLDERS})


def missing_pane_content(template: str) -> bool:
    """True when the template never includes the terminal text."""
    return "{pane_content}" not in template
