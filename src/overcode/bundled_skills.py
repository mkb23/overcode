"""
Bundled skill content for installation into Claude Code skill directories.

Skills are installed as directories with a SKILL.md entry point, following
the Claude Code skill format. Each skill has a description and content field.
"""

from pathlib import Path

# Skill names that were renamed — installer removes these on install
DEPRECATED_SKILL_NAMES = ["delegation"]

OVERCODE_SKILLS: dict[str, dict] = {
    "overcode": {
        "description": "Overcode CLI reference for managing Claude Code agent sessions in tmux",
        "content": """\
---
name: overcode-cli
description: Reference for overcode CLI commands to launch, monitor, and control Claude Code agent sessions in tmux. Use when needing to interact with agents via overcode commands.
user-invocable: false
---

# Overcode CLI Reference

Overcode manages multiple Claude Code agent sessions in tmux.

## Quick Reference

```bash
# Launch
overcode launch -n <name> [-d <path>] [-p "<prompt>"] [--follow] [--bypass-permissions]
overcode launch -n <name> --follow --oversight-timeout 5m -p "... When done: overcode report --status success"
overcode launch -n <name> --allowed-tools "Read,Glob,Grep" --skip-permissions
overcode launch -n <name> --claude-arg "--model haiku" --claude-arg "--effort low"
overcode launch -n <name> --budget 2.00 -p "..."  # Set cost budget (auto-deducted from parent)

# Monitor
overcode list [name] [--show-done]
overcode show <name> [-n 50]
overcode follow <name>

# Control
overcode send <name> "message"       # Send text + Enter
overcode send <name> enter           # Approve permission
overcode send <name> escape          # Reject permission
overcode kill <name> [--no-cascade]
overcode instruct <name> "instructions"

# Report completion (child agents call this when done)
overcode report --status success|failure [--reason "..."]

# Budget
overcode budget set <name> <amount>
overcode budget transfer <source> <target> <amount>
overcode budget show [name]

# Cleanup
overcode cleanup [--done]

# TUI / daemons
overcode monitor
overcode supervisor [--restart]
overcode attach [--name <agent>]
```

## Status Indicators

| Status | Meaning |
|--------|---------|
| GREEN | Running actively |
| YELLOW | No standing instructions |
| ORANGE | Waiting for supervisor |
| RED | Waiting for user input |
| \U0001f441\ufe0f YELLOW | Waiting for oversight report |

## Unblocking Stuck Agents

```bash
overcode show my-agent -n 100   # See what it's stuck on
overcode send my-agent enter     # Approve permission
overcode send my-agent escape    # Reject permission
overcode send my-agent "yes"     # Send text response
```

## Jobs (Long-Running Bash Commands)

For long-running commands (10min+ \u2014 test suites, builds, deploys), use `overcode bash` to launch them as tracked jobs in a separate tmux session. This keeps the output visible and lets you monitor multiple concurrent jobs from the TUI.

```bash
# Launch a job
overcode bash "pytest tests/ -x" --name unit-tests
overcode bash "npm run build" -d ~/frontend
overcode bash "make deploy-staging" --agent my-agent   # Link to an agent

# Manage jobs
overcode jobs list [--all]        # List running (or all) jobs
overcode jobs tail <name>         # Stream output (works without TTY)
overcode jobs tail <name> -n 50   # Last 50 lines and exit
overcode jobs kill <name>         # Kill a running job
overcode jobs attach <name>       # Attach to job's tmux window (needs TTY)
overcode jobs clear               # Remove completed/failed/killed jobs

# TUI: press J to toggle jobs view, j/k to navigate, x to kill, c to clear
overcode monitor --jobs           # Start TUI directly in jobs view
```

## Standing Instructions Presets

`overcode instruct <name> <preset>` \u2014 available presets: `DO_NOTHING`, `STANDARD`, `PERMISSIVE`, `CAUTIOUS`, `RESEARCH`, `CODING`, `TESTING`, `REVIEW`, `DEPLOY`, `AUTONOMOUS`, `MINIMAL`.

## File Locations

```
~/.overcode/
\u251c\u2500\u2500 sessions/<tmux-session>/
\u2502   \u251c\u2500\u2500 sessions.json              # Session state
\u2502   \u251c\u2500\u2500 monitor_daemon_state.json  # Daemon state (TUI reads this)
\u2502   \u251c\u2500\u2500 report_<agent>.json        # Oversight reports
\u2502   \u2514\u2500\u2500 *.log                      # Daemon logs
\u251c\u2500\u2500 config.yaml
\u2514\u2500\u2500 presets.json
```
""",
    },
    "delegating-to-agents": {
        "description": "Delegate substantial work to child Claude agents using overcode launch",
        "content": """\
---
name: delegating-to-agents
description: Delegate substantial work to child Claude agents using overcode launch for parallel execution. Use when needing to run independent tasks in parallel, launch long-duration work that benefits from real-time monitoring, or distribute work across multiple repositories. Prefer over the built-in Task tool for anything involving code changes, test runs, or tasks the human may want to observe.
user-invocable: false
---

# Delegating Work to Child Agents

Use `overcode launch` instead of the Task tool when work is substantial, involves code/tests/commits, or the human may want to intervene. Overcode agents are full Claude Code sessions in tmux with real-time visibility. Use the Task tool only for quick lookups (seconds, not minutes) where you need the result inline.

## Sequential (Blocking)

```bash
overcode launch --name fix-auth-bug -d ~/project --follow --bypass-permissions \\
  -p "Fix the JWT refresh bug in src/auth/jwt.py \u2014 refresh_token() doesn't check expiry. Add check, update tests/test_auth.py, run pytest. When done: overcode report --status success --reason 'Fixed and tests pass'"
```

- `--follow` blocks until child calls `overcode report`, streaming output
- `--bypass-permissions` for full autonomy; `--skip-permissions` for safer mode
- Exit codes: 0 = success, 1 = failure/terminated, 2 = timeout, 130 = interrupted
- Verify after: `overcode show fix-auth-bug -n 50`

## Parallel (Non-Blocking)

```bash
overcode launch -n refactor-api -d ~/project --budget 3.00 -p "Refactor REST API. When done: overcode report --status success" --bypass-permissions
overcode launch -n write-tests -d ~/project --budget 2.00 -p "Write auth tests. When done: overcode report --status success" --bypass-permissions

overcode list                       # Monitor progress
overcode show refactor-api -n 100   # Read output
overcode follow refactor-api        # Block on one when needed
```

## Writing the Prompt

**Most important part.** The child has zero context \u2014 `--prompt` must be self-contained:

- **State the goal** \u2014 what should be different when done?
- **Include file paths** \u2014 `src/auth/jwt.py` not "the auth module"
- **Specify constraints** \u2014 "don't change the public API"
- **Include verification** \u2014 "run `pytest tests/auth/`"
- **End with `overcode report`** \u2014 child must signal completion

Bad: `"Fix the bug"`
Good: `"Fix the JWT refresh bug in src/auth/jwt.py. The refresh_token() on line 45 doesn't check expiry. Add check, update tests/test_auth.py, run pytest. When done: overcode report --status success --reason 'Fixed JWT refresh and tests pass'"`

## Reporting & Oversight

Children must call `overcode report --status success|failure [--reason "..."]` when done. Without it, the child enters `waiting_oversight` (not `done`) and `--follow` keeps blocking.

**Stuck policies** control what happens on Stop without report:

| Flag | Behavior |
|------|----------|
| (default) | Wait indefinitely for report |
| `--on-stuck fail` | Exit 1 immediately |
| `--oversight-timeout 5m` | Wait up to duration, then exit 2 |

## Budget Control

```bash
# Preferred: set budget at launch (auto-deducted from parent if parent has budget)
overcode launch -n child-agent -d ~/project --bypass-permissions --budget 2.00 -p "..."

# Manual budget management
overcode budget transfer my-agent child-agent 2.00   # Transfer from your budget
overcode budget set child-agent 3.00                  # Set directly
```

Exceeded budget = heartbeats and supervisor stop, agent winds down naturally.

## Tool Restrictions

Scope agent capabilities with `--allowed-tools` for safety:

```bash
# Read-only agent — cannot modify files
overcode launch -n safe-reader --follow --allowed-tools "Read,Glob,Grep" \\
  -p "Analyze the codebase. Do NOT modify files. When done: overcode report --status success"

# Code-only agent — no shell access
overcode launch -n coder --follow --allowed-tools "Read,Write,Edit,Glob,Grep" --skip-permissions \\
  -p "Refactor auth module. When done: overcode report --status success"
```

Pass arbitrary Claude CLI flags with `--claude-arg` (repeatable):

```bash
overcode launch -n fast --claude-arg "--model haiku" --claude-arg "--effort low" -p "Quick review"
```

## Long-Running Shell Commands (Jobs)

When a task involves a long-running shell command (10min+ \u2014 full test suites, builds, deploys, docker compose), use `overcode bash` instead of running it inline. This launches the command as a tracked job in a separate tmux session with its own window, so you can monitor it without blocking your agent session.

```bash
# Launch a test suite as a tracked job
overcode bash "pytest tests/ -x --timeout=600" --name full-tests

# Launch a build linked to the current agent
overcode bash "npm run build" --name frontend-build --agent my-agent

# Check on it later
overcode jobs list
overcode jobs tail full-tests      # Stream output (no TTY needed)
overcode jobs tail full-tests -n 50  # Last 50 lines snapshot
overcode jobs kill full-tests      # Kill if needed
```

**When to use `overcode bash` vs running inline:**
- Inline (`Bash` tool): Quick commands (<2 min) where you need the result to continue
- `overcode bash`: Long commands (10min+) \u2014 test suites, builds, deploys, docker operations
- `overcode launch`: When you need a full Claude Code agent with AI reasoning

Jobs are visible in the TUI jobs view (press `J`) and auto-clean after 24h (configurable via `jobs.retention_hours` in config).

## Rules

- Parent auto-detected from `OVERCODE_SESSION_NAME` \u2014 no `--parent` needed inside an overcode agent
- Max depth: 5 levels. `overcode kill` cascades by default (`--no-cascade` to orphan)
- Use task-oriented names: `fix-auth-bug` not `child-1`
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
    """Check if any installed skills are outdated vs bundled versions."""
    base = Path.home() / ".claude" / "skills"
    for name, skill in OVERCODE_SKILLS.items():
        skill_file = base / name / "SKILL.md"
        if skill_file.exists() and skill_file.read_text() != skill["content"]:
            return True
    return False
