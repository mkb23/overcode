# Agent Delegation Skill

Use `overcode launch --follow` to delegate work to child agents instead of the Task tool. Child agents are first-class tmux citizens that humans can observe and intervene in.

## Sequential Delegation (blocking)

Launch a child agent and wait for it to complete:

```bash
overcode launch --name <task-name> --follow --prompt "<detailed instructions>" --bypass-permissions
```

The `--follow` flag streams the child's output to your stdout and blocks until the child reaches Stop. When it finishes, the child is marked "done" and hidden from default views.

## Parallel Delegation (non-blocking)

Launch multiple children without `--follow`, then check on them:

```bash
# Launch several in parallel
overcode launch --name auth-refactor --prompt "Refactor the auth module to use JWT" --bypass-permissions
overcode launch --name api-tests --prompt "Write integration tests for the /users API" --bypass-permissions

# Check status
overcode list

# Read a child's output
overcode show auth-refactor --lines 100

# Follow a specific child (blocks until it finishes)
overcode follow auth-refactor
```

## Budget Management

Transfer budget to child agents before launching:

```bash
# Set your own budget first (if not already set)
overcode budget set my-agent 10.00

# Transfer budget to child
overcode budget transfer my-agent child-agent 2.00

# Check budget status
overcode budget show
```

## Key Rules

1. **Always pass full context in `--prompt`** - child agents start fresh with no shared context
2. **Use meaningful names** - they appear in the TUI and help humans understand the hierarchy
3. **One concern per child** - keep delegated tasks focused and well-scoped
4. **Check output after `--follow` returns** - verify the child completed successfully
5. **Set budgets** - prevent runaway costs by transferring explicit budgets to children

## Hierarchy

- Agents auto-detect their parent via the `OVERCODE_SESSION_NAME` environment variable
- Maximum nesting depth is 5 levels
- `overcode kill <parent>` cascades to all descendants by default
- `overcode list <parent>` shows the parent and all its descendants
- The TUI has a "By Tree" sort mode (press S to cycle) showing the hierarchy

## Cleanup

Done child agents stay alive in tmux but are hidden from default views:

```bash
# Show done agents
overcode list --show-done

# Archive all done agents
overcode cleanup --done
```
