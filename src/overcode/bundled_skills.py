"""
Bundled skill content for installation into Claude Code skill directories.

Skills are installed as directories with a SKILL.md entry point, following
the Claude Code skill format. Each skill has a description and content field.
"""

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
overcode launch -n refactor-api -d ~/project -p "Refactor REST API. When done: overcode report --status success" --bypass-permissions
overcode launch -n write-tests -d ~/project -p "Write auth tests. When done: overcode report --status success" --bypass-permissions

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
overcode budget transfer my-agent child-agent 2.00   # Transfer from your budget
overcode budget set child-agent 3.00                  # Set directly
```

Exceeded budget = heartbeats and supervisor stop, agent winds down naturally.

## Rules

- Parent auto-detected from `OVERCODE_SESSION_NAME` \u2014 no `--parent` needed inside an overcode agent
- Max depth: 5 levels. `overcode kill` cascades by default (`--no-cascade` to orphan)
- Use task-oriented names: `fix-auth-bug` not `child-1`
""",
    },
}
