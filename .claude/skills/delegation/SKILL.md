---
description: Delegate work to child Claude agents via overcode launch
---

# Delegation: Using Overcode Agents Instead of Subagents

When you need to delegate work to another Claude instance, use `overcode launch` instead of the Task tool. Overcode agents are full Claude Code sessions running in tmux — the human can watch them work in real-time, intervene, send instructions, and control them from the TUI. Built-in subagents are invisible black boxes by comparison.

## When to Use Overcode Agents

**Use `overcode launch` when:**
- The task is substantial (would take more than a few minutes)
- The human might want to observe or intervene
- You want the child to have its own git workspace and full tool access
- You're doing parallel work across multiple repos or directories
- The task involves writing code, running tests, or making commits

**Use the built-in Task tool when:**
- Quick research or file lookup (seconds, not minutes)
- Simple, self-contained queries that don't need intervention
- You need the result inline in your current context immediately

## Sequential Delegation (Blocking)

Launch a child and wait for it to finish:

```bash
overcode launch \
  --name fix-auth-bug \
  --directory ~/project \
  --follow \
  --prompt "Fix the authentication bug in src/auth.py where JWT tokens aren't being refreshed. Run tests to verify." \
  --bypass-permissions
```

- `--follow` blocks until the child stops, streaming its output to your terminal
- `--bypass-permissions` lets the child work autonomously (use `--skip-permissions` for safer mode)
- When the child finishes, it's marked "done" and you regain control
- Check the exit: 0 = clean stop, 1 = terminated, 130 = you interrupted

**After it returns**, verify the work:
```bash
overcode show fix-auth-bug --lines 50
```

## Parallel Delegation (Non-Blocking)

Launch several children and check on them:

```bash
# Launch multiple agents in parallel
overcode launch --name refactor-api --directory ~/project --prompt "Refactor the REST API to use FastAPI" --bypass-permissions
overcode launch --name write-tests --directory ~/project --prompt "Write unit tests for the auth module" --bypass-permissions
overcode launch --name update-docs --directory ~/project --prompt "Update the API documentation" --bypass-permissions

# Monitor progress
overcode list

# Read a specific child's recent output
overcode show refactor-api --lines 100

# Block on one when you need its result
overcode follow refactor-api
```

## Writing the Prompt

**This is the most important part.** The child starts a fresh Claude Code session with zero context. Your `--prompt` must be entirely self-contained:

- **State the goal clearly** — what should be different when the child is done?
- **Include file paths** — don't say "the auth module", say `src/auth/jwt.py`
- **Specify constraints** — "don't modify the public API", "keep backwards compatibility"
- **Include verification** — "run `pytest tests/auth/` and fix any failures"
- **Give context on architecture** — the child doesn't know what you know

Bad: `"Fix the bug"`
Good: `"Fix the JWT refresh bug in src/auth/jwt.py. The refresh_token() function on line 45 doesn't check token expiry before refreshing. Add an expiry check and update the unit test in tests/test_auth.py. Run pytest tests/test_auth.py to verify."`

## Budget Control

Prevent runaway costs by giving children explicit budgets:

```bash
# Transfer from your budget to the child
overcode budget transfer my-agent fix-auth-bug 2.00

# Or set directly on the child
overcode budget set fix-auth-bug 3.00

# Check all budgets
overcode budget show
```

When a child exceeds its budget, heartbeats and supervisor nudges stop — it naturally winds down.

## Managing Children

```bash
# See your subtree
overcode list my-agent

# Kill a child (and its children, recursively)
overcode kill fix-auth-bug

# Kill only the child, not its grandchildren
overcode kill fix-auth-bug --no-cascade

# See done children
overcode list --show-done

# Clean up all done children
overcode cleanup --done
```

## Naming Convention

Use descriptive, task-oriented names. The human sees these in the TUI:

- `fix-auth-bug` not `child-1`
- `refactor-api-v2` not `task`
- `test-payment-flow` not `worker`

## Hierarchy Rules

- Parent is auto-detected from `OVERCODE_SESSION_NAME` env var — you don't need `--parent` when running inside an overcode agent
- Maximum depth is 5 levels (you → child → grandchild → ...)
- `overcode kill` cascades to all descendants by default
- The TUI shows the tree when sorted by hierarchy (press `S` to cycle to "Tree" mode)
