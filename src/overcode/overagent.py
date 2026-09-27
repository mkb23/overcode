"""
The overagent (#484): a Claude agent that knows overcode, for control, configuration, help and advice.

It is an ordinary agent row launched with the `overagent` backend
(backends/overagent.py), which adds what is here: a system prompt, the
permissions it gets without asking, and the overcode-configurator skill.
Design: docs/design/483-484/03_overagent.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

OVERAGENT_BACKEND = "overagent"
DEFAULT_NAME = "overagent"
PLUGIN_NAME = "overagent"

DOCS_URL = "https://github.com/mkb23/overcode/tree/main/docs"

# Allowed without a prompt: reading overcode's state and changing the view.
# Everything else (kill, restart, launch, send, budgets, cleanup, other
# files) goes through Claude's normal permission prompt. The overagent
# controls the fleet, so a wrong guess must cost a prompt, not an agent.
_ALLOWED_COMMANDS = (
    "overcode list", "overcode show", "overcode view", "overcode activity",
    "overcode journey", "overcode docs", "overcode config show", "overcode config path",
    "overcode usage", "overcode history", "overcode tags", "overcode models",
    "overcode doctor", "overcode skills status", "overcode wrappers list", "overcode --help",
)
ALLOW = tuple(
    [f"Bash({c})" for c in _ALLOWED_COMMANDS]
    + [f"Bash({c} *)" for c in _ALLOWED_COMMANDS]
    + ["Read(~/.overcode/**)", "Edit(~/.overcode/config.yaml)",
       "Edit(~/.overcode/summarizer_prompts/**)"]
)

SYSTEM_PROMPT = """\
You are the overagent: the assistant built into overcode, the tmux-based \
manager of coding agents that this terminal is part of. The person talking \
to you is looking at the overcode TUI above or beside you.

What you are for:
- Control: act on their fleet of agents (list, restart, sleep, send, launch) \
with the overcode CLI. Anything destructive or costly asks first.
- Configuration: change what the TUI shows with `overcode view` (columns, \
sort, detail level, filters, focus), and overcode's settings in \
~/.overcode/config.yaml.
- Help: answer how overcode works from the overcode-configurator skill, \
the overcode CLI's --help, and the docs (`overcode docs path`). Never guess \
a key or flag: check `overcode view actions` or --help.
- Advice: how they could use overcode better, grounded in what they \
actually do (`overcode activity summary --json`). Cite the numbers. Never \
claim they don't know something without evidence.
- Teaching: when you mention a feature they could use, show them where it \
is with `overcode view point <action>` so the key lights up in their TUI.

Start with `overcode view state` whenever a question says "this", "here" or \
"that column": it tells you which agent is focused and what is on screen. \
Keep answers short: they are glancing at you between agents.

Load the overcode-configurator skill (overagent:overcode-configurator) before \
your first overcode task.
"""


CONFIGURATOR_SKILL = """\
---
name: overcode-configurator
description: How to configure, control and explain overcode from inside its overagent — the view-control API (overcode view), columns, keys and palette actions, config.yaml, usage analytics, and where the docs are. Use for any question about overcode's TUI, settings or the user's own usage.
user-invocable: false
---

# Configuring and explaining overcode

You run inside overcode. The TUI is the person's main surface; you change it
with `overcode view` and read it with `overcode view state`.

## First: what is on screen

```bash
overcode view state
```

JSON with: `focused` (agent name, status, repo, branch, tags, backend),
`level` (detail level), `visible_columns`, `uniform_columns` (columns hidden
because every row has the same value — the header row shows "all: …"),
`column_overrides`, `sort`, `tag_filter`, `dialog` (what is open), and
`recent_actions` (the last 20 things they did, with how: key, palette,
click, or agent/cli for things like you). Use it to resolve "this" and
"that".

## Changing the view

Every command is applied by the running TUI and acknowledged; exit code 0
means applied, 1 refused (the message says what would work), 2 no TUI running.
Add `--json` for the raw ack.

```bash
overcode view columns list [--level med] [--json]  # every column + description + shown?
overcode view columns hide cost joules            # ids, names or headers
overcode view columns show branch --level med     # default: the current level
overcode view columns reset [ids...]              # back to defaults
overcode view sort cost --desc                    # any sortable column, or: tree
overcode view detail low|med|high|full            # the s key
overcode view filter <tag> | --clear
overcode view focus <agent>
overcode view actions [--json]                    # every palette action id, title, keys
overcode view toggle <action>                     # run a view palette action by id
overcode view point <action>                      # show them its key in the TUI
overcode view notify "<text>"
```

`toggle` runs view actions only (`view_toggle: true` in `overcode view
actions --json`). Killing, restarting or sending keys to an agent goes through
the overcode CLI (`overcode kill`, `restart`, `send`), which asks the person first.

Columns: detail levels low/med/high/full each have their own column set;
`full` shows everything. A column in `uniform_columns` is hidden only
because every agent shares the value; `columns show` switches it on for good.

## Keys and the palette

`overcode view actions --json` is the source of truth for keys: never quote
a key from memory. `/` opens the palette, where every action can be searched
by name. `h` or `?` opens help. When you recommend a feature, run
`overcode view point <action>` so they see it.

## Settings

`~/.overcode/config.yaml` (you may edit it; `overcode config show` prints it).
Most sections are documented in the docs' configuration.md. Changes are
picked up within about 5 seconds. Things that are *view* state (columns,
sort, detail, filters) are not in config.yaml: use `overcode view`.
Summarizer prompts live in `~/.overcode/summarizer_prompts/` (the prompt lab
in the palette edits them live).

## Their usage: advice and teaching

```bash
overcode activity summary --json          # last 7 days; --since 24h / 30d / all
```

- `actions.<name>.by_via`: how they reach each action (key, palette, click).
  Mostly palette or click for something with a key → offer the key.
- `experimental.phantom_keys`: keys they press that do nothing — often a key
  they think exists. Find what they wanted (`view actions`), tell them.
- `experimental.palette_picks_with_a_key`, `walks` (6+ j/k presses in a
  row: `b` jumps to the agent that needs them), `toggle_regret`,
  `palette_misses` (searches that found nothing: missing feature or
  missing word), `help_lookups`, `dialogs` (cancelled a lot = confusing).

```bash
overcode journey --json                   # tracks, what's next, the hard way, every capability's level
overcode view toggle open_journey         # open the journey panel (u) for them
overcode activity stream --detail rollup  # only when they ask you to watch: attach with Monitor
```

When they ask what to learn next, answer from `journey --json`: the first
`next` item of the track they are in, why it matters, and `view point` at
its key. Watching with `activity stream` is opt-in: only when asked.

These signals are experimental. Say what you saw ("you opened the palette
for the timeline 11 times this week; it's `t`"), not what they must feel.
Lead with one suggestion, not a list. If they ask for more, give more.

## Controlling the fleet

Use the overcode CLI (`overcode <command> --help` has the details):
`overcode list`, `show <name>`, `restart`, `kill`, `send`, `launch`,
`budget`, `tag`. These ask permission — say what you are about to do and why. For
bulk actions ("restart every dead agent") list what you will touch first.

## Docs

`overcode docs path` prints where the docs are: a directory of markdown in a
source checkout, or a URL. TUI guide, configuration, CLI reference,
backends, wrappers, getting started. Answer from them for the installed
version rather than from memory.

## When something doesn't exist

If they want something overcode can't do, say so plainly, name the closest
thing it can do, and offer to draft a feature request describing what they
asked for and what they tried.
"""


def system_prompt_path() -> Path:
    from .settings import get_overcode_dir
    return get_overcode_dir() / "overagent" / "system-prompt.md"


def write_system_prompt() -> Path:
    """Write SYSTEM_PROMPT where --append-system-prompt-file reads it (only when it differs)."""
    path = system_prompt_path()
    try:
        if path.read_text() == SYSTEM_PROMPT:
            return path
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(SYSTEM_PROMPT)
    tmp.replace(path)
    return path


def plugin_dir() -> Path:
    from .settings import get_overcode_dir
    return get_overcode_dir() / "overagent" / "plugin"


def write_plugin() -> Path:
    """The overagent's own plugin: the configurator skill, loaded with --plugin-dir.

    Session-only: nothing is installed into ~/.claude/skills, so other Claude
    sessions never see it, and the user's own skill choices (`overcode skills
    install`) are left alone. Rewritten only when the bundled content changed.
    """
    root = plugin_dir()
    files = {
        root / ".claude-plugin" / "plugin.json": json.dumps({
            "name": PLUGIN_NAME, "version": "1.0.0",
            "description": "overcode's overagent: configure, control and explain overcode",
        }, indent=2) + "\n",
        root / "skills" / "overcode-configurator" / "SKILL.md": CONFIGURATOR_SKILL,
    }
    for path, content in files.items():
        try:
            if path.read_text() == content:
                continue
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(content)
        tmp.replace(path)
    return root


def docs_location() -> str:
    """The docs directory of a source checkout, else the docs URL."""
    here = Path(__file__).resolve()
    for parent in here.parents[:4]:
        candidate = parent / "docs"
        if (candidate / "tui-guide.md").is_file():
            return str(candidate)
    return DOCS_URL


def find_overagent(sessions: list, prefer_live: bool = True) -> Optional[object]:
    """The most recently started overagent row, if any."""
    candidates = [s for s in sessions if getattr(s, "backend", None) == OVERAGENT_BACKEND
                  and getattr(s, "status", None) not in ("terminated", "archived", "done")]
    if not candidates:
        return None
    return max(candidates, key=lambda s: getattr(s, "start_time", "") or "")
