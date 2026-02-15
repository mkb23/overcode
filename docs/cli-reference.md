# CLI Reference

Complete reference for all overcode commands.

## Agent Commands

### `overcode launch`

Launch a new Claude Code agent in tmux.

```bash
overcode launch --name <name> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | **Required.** Name for the agent (becomes tmux window name) |
| `--directory` | `-d` | Working directory (defaults to current directory) |
| `--prompt` | `-p` | Initial prompt to send to the agent |
| `--skip-permissions` | | Auto-deny permission prompts |
| `--bypass-permissions` | | Bypass all permission checks (dangerous) |
| `--parent` | | Name of parent agent (auto-detected if launched from within an agent) |
| `--follow` | `-f` | Stream child output, block until report or timeout |
| `--on-stuck` | | Policy when child stops without reporting: `wait` (default), `fail`, `timeout:DURATION` |
| `--oversight-timeout` | | Shorthand for `--on-stuck timeout:DURATION` (e.g., `5m`, `1h`, `30s`) |
| `--session` | | Tmux session name (default: `agents`) |

**Examples:**
```bash
# Basic launch
overcode launch -n my-agent -d ~/project

# With initial prompt
overcode launch -n researcher -d ~/project -p "Analyze the authentication flow"

# Autonomous mode
overcode launch -n builder -d ~/project --bypass-permissions

# Launch as child agent with follow mode
overcode launch -n subtask --parent my-agent --follow -p "Fix the auth bug. When done: overcode report --status success"

# With oversight timeout (fail after 5 minutes without report)
overcode launch -n subtask --follow --oversight-timeout 5m -p "Fix the bug. When done: overcode report --status success"
```

### `overcode list`

List all running agents with status and statistics.

```bash
overcode list [name] [--show-done] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `name` | Optional. Show only this agent and its descendants |
| `--show-done` | Include "done" child agents |

Output shows: agent name, uptime, green/idle time, interactions, tokens, and current activity. In tree mode, children are indented under their parent.

### `overcode attach`

Attach to the tmux session containing agents.

```bash
overcode attach [--session <session>]
```

Use `Ctrl+b d` to detach, or `Ctrl+b n/p` to switch windows.

### `overcode kill`

Kill a running agent.

```bash
overcode kill <agent-name> [--no-cascade] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `--no-cascade` | Only kill this agent, orphan its children instead of killing them |

By default, killing a parent also kills all its descendants (deepest-first).

### `overcode cleanup`

Remove terminated sessions from tracking. Sessions whose tmux windows no longer exist are marked terminated; this command removes them from the session list.

```bash
overcode cleanup [--done] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `--done` | Also archive "done" child agents (kill tmux window, remove from tracking) |

### `overcode report`

Report completion from within a child agent session. Called by the child agent (not the parent) to signal that it finished its work.

```bash
overcode report --status <success|failure> [--reason <text>]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--status` | `-s` | **Required.** `success` or `failure` |
| `--reason` | `-r` | Optional explanation |

The command reads `OVERCODE_SESSION_NAME` and `OVERCODE_TMUX_SESSION` from the environment (automatically set for all agents launched by overcode).

Without a report, child agents that stop enter `waiting_oversight` status instead of `done`. The parent's `--follow` blocks until a report arrives (or the oversight policy triggers).

### `overcode follow`

Follow an already-running agent's output. Streams pane content to stdout and blocks until the agent reports completion (or the oversight policy triggers).

```bash
overcode follow <agent-name> [--session <session>]
```

Exit codes:
- `0` — child reported success
- `1` — child reported failure or terminated
- `2` — oversight timeout expired
- `130` — interrupted (Ctrl-C)

### `overcode budget`

Manage agent cost budgets.

```bash
overcode budget set <name> <amount>              # Set budget
overcode budget transfer <source> <target> <amount>  # Transfer between agents
overcode budget show [name]                      # Show budget status
```

The `transfer` command requires the source to be an ancestor of the target.

### `overcode send`

Send input to an agent.

```bash
overcode send <agent-name> <text> [options]
```

| Option | Description |
|--------|-------------|
| `--no-enter` | Don't press Enter after the text |
| `--session` | Tmux session name |

**Special keys:** `enter`, `escape`, `tab`, `up`, `down`, `left`, `right`

```bash
# Send a command
overcode send my-agent "Fix the bug in auth.py"

# Send without Enter
overcode send my-agent "y" --no-enter

# Send special key
overcode send my-agent escape
```

### `overcode show`

Show recent output from an agent.

```bash
overcode show <agent-name> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--lines` | `-n` | Number of lines to show (default: 50) |
| `--session` | | Tmux session name |

### `overcode set-value`

Set agent priority value for sorting.

```bash
overcode set-value <agent-name> <value> [--session <session>]
```

Default value is 1000. Higher values = higher priority (shown first when sorted by value).

### `overcode instruct`

Set or manage standing instructions for an agent.

```bash
overcode instruct <agent-name> <preset-or-text> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--clear` | `-c` | Clear standing instructions |
| `--list` | `-l` | List available presets |
| `--session` | | Tmux session name |

**Built-in presets:**
- `DO_NOTHING` - Supervisor ignores this agent
- `STANDARD` - General-purpose safe automation
- `PERMISSIVE` - Trusted agent, minimal friction
- `CAUTIOUS` - Sensitive project, careful oversight
- `RESEARCH` - Information gathering, exploration
- `CODING` - Active development work
- `TESTING` - Running and fixing tests
- `REVIEW` - Code review, analysis only
- `DEPLOY` - Deployment and release tasks
- `AUTONOMOUS` - Fully autonomous operation
- `MINIMAL` - Just keep it from stalling

```bash
# Use a preset
overcode instruct my-agent CODING

# Custom instructions
overcode instruct my-agent "Focus on performance. Avoid changing the API."

# Clear instructions
overcode instruct my-agent --clear
```

---

## Monitoring Commands

### `overcode monitor`

Launch the TUI dashboard (standalone, no supervisor).

```bash
overcode monitor [options]
```

| Option | Description |
|--------|-------------|
| `--diagnostics` | Disable auto-refresh timers (for debugging) |
| `--session` | Tmux session name |

### `overcode supervisor`

Launch the TUI with the embedded supervisor daemon.

```bash
overcode supervisor [options]
```

| Option | Description |
|--------|-------------|
| `--restart` | Restart if already running |
| `--session` | Tmux session name |

### `overcode serve`

Start a web dashboard server for remote/mobile monitoring.

```bash
overcode serve [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--host` | `-h` | Host to bind (default: `0.0.0.0`) |
| `--port` | `-p` | Port (default: `8080`) |
| `--session` | | Tmux session name |

The web dashboard is read-only and auto-refreshes every 5 seconds. Optimized for mobile viewing.

### `overcode web`

Launch the analytics web dashboard for historical data.

```bash
overcode web [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--host` | `-h` | Host to bind (default: `127.0.0.1`) |
| `--port` | `-p` | Port (default: `8080`) |

Features:
- Summary statistics and daily activity charts
- Session browser with sortable table
- Timeline view with status history
- Efficiency metrics and cost analysis
- Dark theme

### `overcode export`

Export session data to Parquet format for analysis.

```bash
overcode export <output-file.parquet> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--archived` | `-a` | Include archived sessions (default: true) |
| `--timeline` | `-t` | Include timeline data (default: true) |
| `--presence` | `-p` | Include presence data (default: true) |

### `overcode history`

Show archived session history.

```bash
overcode history [agent-name]
```

Omit agent name to see all archived sessions.

---

## Daemon Commands

### Monitor Daemon

The monitor daemon tracks agent status, accumulates time metrics, and syncs Claude Code stats.

```bash
# Start the daemon
overcode monitor-daemon start [--interval <seconds>] [--session <session>]

# Stop the daemon
overcode monitor-daemon stop [--session <session>]

# Check status
overcode monitor-daemon status [--session <session>]

# Watch logs
overcode monitor-daemon watch [--session <session>]
```

Default polling interval is 10 seconds.

### Supervisor Daemon

The supervisor daemon provides Claude-powered orchestration. It launches a "daemon claude" when agents need attention.

```bash
# Start the daemon (requires monitor daemon running)
overcode supervisor-daemon start [--interval <seconds>] [--session <session>]

# Stop the daemon
overcode supervisor-daemon stop [--session <session>]

# Check status
overcode supervisor-daemon status [--session <session>]

# Watch logs
overcode supervisor-daemon watch [--session <session>]
```

---

## Configuration Commands

### `overcode config init`

Create a config file with documented defaults.

```bash
overcode config init [--force]
```

Creates `~/.overcode/config.yaml`. Use `--force` to overwrite existing.

### `overcode config show`

Display current configuration.

```bash
overcode config show
```

### `overcode config path`

Show the config file path.

```bash
overcode config path
```

---

## Global Options

Most commands accept `--session <name>` to specify a tmux session other than the default `agents`.

This allows managing multiple independent sets of agents:

```bash
# Team A agents
overcode launch -n task1 -d ~/project --session team-a
overcode monitor --session team-a

# Team B agents
overcode launch -n task1 -d ~/other --session team-b
overcode monitor --session team-b
```
