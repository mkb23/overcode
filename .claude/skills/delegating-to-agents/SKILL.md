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
- `--bypass-permissions` for full autonomy; `--skip-permissions` for safer mode (auto-denies)
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

### Scoping the Child

Use `--allowed-tools` to restrict what the child can do:

```bash
overcode launch -n research-task --allowed-tools 'Read,Glob,Grep,WebSearch' \
  -p "Research how auth middleware works in this codebase..."
```

Use `--claude-arg` for extra Claude CLI flags (repeatable):

```bash
overcode launch -n quick-task --claude-arg '--model haiku' -p "..."
```

## Reporting & Oversight

Children must call `overcode report --status success|failure [--reason "..."]` when done. Without it, the child enters `waiting_oversight` (not `done`) and `--follow` keeps blocking.

**Stuck policies** control what happens if a child stops without reporting:

| Flag | Behavior |
|------|----------|
| (default) | Wait indefinitely for report |
| `--on-stuck fail` | Exit 1 immediately |
| `--oversight-timeout 5m` | Wait up to duration, then exit 2 |

Note: `--oversight-timeout 5m` is shorthand for `--on-stuck timeout:5m`.

## Heartbeat

Use heartbeat for periodic check-ins with long-running children. The heartbeat sends an instruction to the agent at a regular interval — useful for status updates, budget reminders, or keeping agents from stalling.

```bash
# Enable with 5-minute frequency
overcode heartbeat my-agent -e -f 5m -i "Give a brief status update. Check your budget with 'overcode budget show'."

# Monitor and control
overcode heartbeat my-agent --show      # Check current config
overcode heartbeat my-agent --pause     # Temporarily pause (keeps config)
overcode heartbeat my-agent --resume    # Resume paused heartbeat
overcode heartbeat my-agent --disable   # Disable completely
```

**When to use heartbeat:**
- Long tasks (>10 min) where you want periodic status
- Budget-constrained children (include budget check in the instruction)
- Agents that might stall waiting for input you can provide via `overcode send`

## Budget Control

### Setting Budgets

Always set a budget for children doing substantial work:

```bash
overcode launch -n task-agent -d ~/project --bypass-permissions -p "..."
overcode budget set task-agent 2.00    # $2 budget
```

Or transfer from your own budget:

```bash
overcode budget transfer my-agent child-agent 2.00
```

### Monitoring Spend

```bash
overcode budget show                    # All agents
overcode budget show task-agent         # Specific agent
overcode list --cost                    # Cost column in list view
overcode show task-agent --stats-only   # Full stats including cost
```

### What Happens When Budget Runs Out

When an agent exceeds its budget, heartbeats and supervisor stop, and the agent winds down naturally. The agent won't be forcefully killed — it finishes its current turn but receives no further heartbeat nudges.

### Budget Guidelines

| Task Type | Suggested Budget |
|-----------|-----------------|
| Quick fix (single file, known location) | $0.50–$1.00 |
| Feature (multi-file, with tests) | $2.00–$5.00 |
| Research / exploration | $1.00–$2.00 |
| Large refactor | $5.00–$10.00 |

These are rough guides — actual cost depends on model, file sizes, and iteration cycles.

## Agent Self-Monitoring

Agents can (and should) monitor their own resource consumption. Include self-monitoring instructions in the prompt for any non-trivial child agent.

### What Agents Can Check

| Command | What It Shows |
|---------|---------------|
| `overcode budget show` | Own budget limit and spend |
| `overcode show <own-name> --stats-only` | Context window %, tokens, cost, timing |
| `overcode usage` | Subscription limits (5h session, 7d weekly) |

### Prompt Template for Budget-Aware Agents

Include this (adapted) in the child prompt for budget-sensitive work:

```
Resource management:
- Periodically run 'overcode budget show' to check your cost against budget.
- If you've used >75% of your budget, finish your current subtask, commit progress, and report.
- If context window is >80% (check with 'overcode show <name> --stats-only'), summarize your progress and report rather than risk losing context.
- Prefer targeted reads/greps over broad exploration to conserve tokens.
```

### Graceful Wind-Down

When an agent is running low on budget or context, it should:

1. **Finish the current subtask** — don't stop mid-edit
2. **Commit any work in progress** — `git add` + `git commit` with a descriptive message
3. **Report status** — `overcode report --status success --reason 'Completed X, Y still pending'` or `--status failure --reason 'Budget limit — completed A but B remains'`
4. **Leave breadcrumbs** — mention what's done and what's left so a follow-up agent can continue

### Combining Heartbeat with Self-Monitoring

For the most robust setup, combine a heartbeat instruction with self-monitoring:

```bash
overcode heartbeat my-agent -e -f 5m -i \
  "Check your resources: run 'overcode budget show' and 'overcode show my-agent --stats-only'. If budget >75% used or context >80%, wrap up current work, commit, and overcode report."
```

This gives the agent a periodic nudge to check its resources even if it gets deeply focused on a task.

## Rules

- Parent auto-detected from `OVERCODE_SESSION_NAME` — no `--parent` needed inside an overcode agent
- Max depth: 5 levels. `overcode kill` cascades by default (`--no-cascade` to orphan children)
- Use task-oriented names: `fix-auth-bug` not `child-1`
- Always set a budget for children doing non-trivial work
- Always include `overcode report` in the child prompt — without it, `--follow` blocks forever
