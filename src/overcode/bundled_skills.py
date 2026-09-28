"""
Bundled skill content for installation into Claude Code skill directories.

Skills are installed as directories with a SKILL.md entry point, following
the Claude Code skill format. Each skill has a description and content field.
"""

from pathlib import Path

# Skill names that were renamed or merged — installer removes these on install.
# delegating-to-agents was merged into the overcode skill.
DEPRECATED_SKILL_NAMES = ["delegation", "delegating-to-agents"]

OVERCODE_SKILLS: dict[str, dict] = {
    "overcode": {
        "description": "Run and manage other coding agents through overcode",
        "content": """\
---
name: overcode
description: Run other coding agents through overcode. Hand a separate piece of work to a child agent (Claude Code, opencode, codex, grok and others), check on it, unblock it, collect its result, or run a long shell command as a tracked job. Use when a task is large or independent enough to give to another agent, when the user asks you to launch, delegate to or check on agents, or when you are an overcode child agent that has to report back.
---

# overcode

overcode runs coding agents in tmux windows the user can watch and step into.
If `$OVERCODE_SESSION_NAME` is set, you are one of them, and agents you launch
become your children.

## When to delegate

Hand work to a child agent when it will take many minutes, can be described
completely in a prompt, and doesn't need your judgment along the way, or when
independent pieces can run in parallel. The user can watch and steer a child,
which they can't do with your own subagents.

Keep the work yourself when it's quick, when you need the result to carry on
thinking, or when explaining it would take as long as doing it.

## Launching a child

```bash
overcode launch -n fix-jwt-refresh -d ~/project --follow --oversight-timeout 30m -p "<prompt>"
```

The prompt is everything the child knows. Say what should be true when it's
done, name the files involved, give the constraints and how to check the work,
and end with the report line:

    When finished, run: overcode report --status success --reason "<one line>"
    (or --status failure, with the reason, if you couldn't do it)

A child that never reports is never counted as done.

- `--follow` blocks, streaming the child's output, until it reports. Exit code
  0 means success, 1 failure, 2 timed out, 130 interrupted. The
  `--oversight-timeout` stops a child that halts without reporting from
  blocking you forever.
- Without `--follow`, launch several children and check on them later.
  `overcode follow <name>` waits for one.
- Name children after their task (`fix-jwt-refresh`, not `child-1`). The user
  sees these names.

Children inherit your permission mode, model, provider and wrapper. Add
`--bypass-permissions` only when the user wants unattended work; otherwise the
child's permission prompts wait for the user, or for you (see below).

Other options:
- `-B opencode` (or `codex`, `grok`, ...) launches a different agent CLI.
- `-m <model>` picks the model.
- `--budget 2.00` caps its spend, and is taken from your budget if you have one.
- `--allowed-tools "Read,Grep,Glob"` makes a read-only Claude Code child.
- `--skills <profile>` switches on a skill profile.

## Checking in and unblocking

```bash
overcode list "$OVERCODE_SESSION_NAME"   # you and your children, with status
overcode show <name> -n 80               # an agent's recent output
overcode send <name> "text"              # answer its question
overcode send <name> approve             # or reject: a permission prompt, any backend
overcode kill <name>                     # also kills its children (--no-cascade keeps them)
```

Read a child's output before you trust its report, and check the claims that
matter yourself, for example by running the tests.

## Long shell commands

Run commands that take many minutes (full test suites, builds, deploys) as a
tracked job in their own window, linked to you:

```bash
overcode bash "pytest tests/" -n full-tests
overcode jobs tail full-tests -n 50      # last lines; without -n it streams to the end
overcode jobs list
overcode jobs kill full-tests
```

## If you are a child

Do the task in your prompt, then run the `overcode report` line it gave you, or
`overcode report --status failure --reason "..."` if you couldn't. Your parent
may be blocked waiting on you and can't answer questions, so make reasonable
calls yourself and mention them in `--reason`.

## More

`overcode <command> --help` documents every command and flag.
`overcode docs path` prints where overcode's docs are: markdown shipped with it,
such as cli-reference.md, backends.md and skill-profiles.md.
""",
    },
}


def get_available_skills(project_dir: str | None = None) -> list[str]:
    """Scan for installed skill directories (user-level + project-level).

    Returns sorted list of skill names found in ~/.claude/skills/
    and optionally .claude/skills/ relative to project_dir.
    """
    skills: set[str] = set()

    # User-level skills
    user_skills = Path.home() / ".claude" / "skills"
    if user_skills.is_dir():
        for d in user_skills.iterdir():
            if d.is_dir() and (d / "SKILL.md").exists():
                skills.add(d.name)

    # Project-level skills
    if project_dir:
        proj_skills = Path(project_dir) / ".claude" / "skills"
        if proj_skills.is_dir():
            for d in proj_skills.iterdir():
                if d.is_dir() and (d / "SKILL.md").exists():
                    skills.add(d.name)

    return sorted(skills)


def any_skills_stale() -> bool:
    """Check if any installed skills are outdated vs bundled versions.

    A retired skill still installed (e.g. delegating-to-agents, now part of
    the overcode skill) counts too: `overcode skills install` removes it.
    """
    base = Path.home() / ".claude" / "skills"
    if any((base / old / "SKILL.md").exists() for old in DEPRECATED_SKILL_NAMES):
        return True
    for name, skill in OVERCODE_SKILLS.items():
        skill_file = base / name / "SKILL.md"
        if skill_file.exists() and skill_file.read_text() != skill["content"]:
            return True
    return False
