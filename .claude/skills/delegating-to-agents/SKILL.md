---
name: delegating-to-agents
description: Delegate substantial work to child Claude agents using overcode launch for parallel execution. Use when needing to run independent tasks in parallel, launch long-duration work that benefits from real-time monitoring, or distribute work across multiple repositories. Prefer over the built-in Task tool for anything involving code changes, test runs, or tasks the human may want to observe.
user-invocable: false
---

# Delegating Work to Child Agents

Use `overcode launch` instead of the Task tool when work is substantial, involves code/tests/commits, or the human may want to intervene. Overcode agents are full Claude Code sessions in tmux with real-time visibility. Use the Task tool only for quick lookups (seconds, not minutes) where you need the result inline.

## Sequential (Blocking)

```bash
overcode launch --name fix-auth-bug -d ~/project --follow --bypass-permissions \
  -p "Fix the JWT refresh bug in src/auth/jwt.py — refresh_token() doesn't check expiry. Add check, update tests/test_auth.py, run pytest. When done: overcode report --status success --reason 'Fixed and tests pass'"
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

**Most important part.** The child has zero context — `--prompt` must be self-contained:

- **State the goal** — what should be different when done?
- **Include file paths** — `src/auth/jwt.py` not "the auth module"
- **Specify constraints** — "don't change the public API"
- **Include verification** — "run `pytest tests/auth/`"
- **End with `overcode report`** — child must signal completion

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

- Parent auto-detected from `OVERCODE_SESSION_NAME` — no `--parent` needed inside an overcode agent
- Max depth: 5 levels. `overcode kill` cascades by default (`--no-cascade` to orphan)
- Use task-oriented names: `fix-auth-bug` not `child-1`
