---
name: overcode-cli
description: Reference for overcode CLI commands to launch, monitor, and control Claude Code agent sessions in tmux. Use when needing to interact with agents via overcode commands.
user-invocable: false
---

# Overcode CLI Reference

Overcode manages multiple Claude Code agent sessions in tmux.

## Launch & Lifecycle

```bash
# Launch a new agent
overcode launch -n <name> [-d <path>] [-p "<prompt>"] [--follow] [--bypass-permissions]
overcode launch -n <name> --follow --oversight-timeout 5m -p "... When done: overcode report --status success"

# Launch options
#   --skip-permissions       Auto-deny permission prompts (--permission-mode dontAsk)
#   --bypass-permissions     Bypass all permission checks (--dangerously-skip-permissions)
#   --parent <name>          Set parent agent for hierarchy (auto-detected inside overcode agents)
#   --follow, -f             Stream output and block until done
#   --on-stuck <policy>      When child stops: wait (default), fail, timeout:DURATION
#   --oversight-timeout <dur> Shorthand for --on-stuck timeout:DURATION (e.g. 5m, 1h)
#   --allowed-tools <list>   Comma-separated tools (e.g. 'Bash,Read,Write,Edit')
#   --claude-arg <flag>      Extra Claude CLI flag (repeatable, e.g. '--model haiku')

# Report completion (child agents call this when done)
overcode report --status success|failure [--reason "..."]

# Kill an agent (cascades to children by default)
overcode kill <name> [--no-cascade]

# Remove terminated sessions from tracking
overcode cleanup [--done]           # --done also archives 'done' child agents
```

## Monitoring

```bash
overcode list [name] [--show-done] [--cost] [--sisters]
#   (no args)    Show all agents with depth-based indentation
#   <name>       Show agent + all descendants
#   --cost       Show $ cost instead of token counts
#   --sisters    Include remote sister agents

overcode show <name> [-n 50]        # Agent details + recent output
overcode show <name> --stats-only   # Stats only (context %, tokens, cost, timing)
overcode show <name> --no-stats     # Output only, no stats

overcode follow <name>              # Stream output, block until Stop (Ctrl-C to detach)
```

## Agent Interaction

```bash
overcode send <name> "message"       # Send text + Enter
overcode send <name> enter           # Approve permission
overcode send <name> escape          # Reject permission
overcode send <name> --no-enter "y"  # Send text without Enter

overcode instruct <name> <preset>           # Set standing instructions from preset
overcode instruct <name> "custom text"      # Set custom standing instructions
overcode instruct <name> --clear            # Clear standing instructions
overcode instruct --list                    # List available presets

overcode annotate <name> "Working on auth"  # Set annotation (visible in TUI)
overcode annotate <name>                    # Clear annotation

overcode set-value <name> <int>             # Set sort priority (default 1000, higher = top)
```

## Heartbeat

```bash
overcode heartbeat <name> -e -f 5m -i "Status check"   # Enable with frequency + instruction
overcode heartbeat <name> --show                        # Show current config
overcode heartbeat <name> --pause                       # Temporarily pause
overcode heartbeat <name> --resume                      # Resume paused heartbeat
overcode heartbeat <name> --disable                     # Disable completely
```

## Budget

```bash
overcode budget set <name> <amount>                     # Set cost budget ($)
overcode budget transfer <source> <target> <amount>     # Transfer budget between agents
overcode budget show [name]                             # Show budget status
```

## Subscription Usage

```bash
overcode usage        # Show Claude Code subscription limits (5h session + 7d weekly)
```

## Standing Instructions Presets

`overcode instruct <name> <preset>` — available presets:

| Preset | Purpose |
|--------|---------|
| `DO_NOTHING` | Supervisor ignores this agent (default) |
| `AUTONOMOUS` | Fully autonomous operation |
| `STANDARD` | General-purpose safe automation |
| `PERMISSIVE` | Trusted agent, minimal friction |
| `CAUTIOUS` | Sensitive project, careful oversight |
| `CODING` | Active development work |
| `TESTING` | Running and fixing tests |
| `RESEARCH` | Information gathering, exploration |
| `REVIEW` | Code review, analysis only |
| `DEPLOY` | Deployment and release tasks |
| `MINIMAL` | Just keep it from stalling |

Custom presets: `~/.overcode/presets.json`

## TUI & Daemons

```bash
overcode monitor                     # Launch standalone TUI monitor
overcode supervisor [--restart]      # TUI monitor with embedded controller Claude
overcode attach [--name <agent>]     # Attach to tmux session

# Monitor Daemon (metrics/state tracking)
overcode monitor-daemon start|stop|status|watch

# Supervisor Daemon (Claude orchestration)
overcode supervisor-daemon start|stop|status|watch
```

## Web Dashboard

```bash
overcode web                         # Start on localhost:8080
overcode web --port 3000             # Custom port
overcode web --host 0.0.0.0         # LAN access (requires api_key in config)
overcode web --stop                  # Stop server
# Serves: / (analytics), /dashboard (live monitoring), /api/status (sister endpoint)
```

## Setup & Configuration

```bash
# Claude Code integration
overcode hooks install|uninstall|status      # Manage hook integration
overcode skills install|uninstall|status     # Manage skill files
overcode perms install|uninstall|status      # Manage tool permissions

# Configuration
overcode config init                 # Create config with documented defaults
overcode config show                 # Show current configuration
overcode config path                 # Show config file path

# Cross-machine monitoring
overcode sister list|add|remove|allow-control
```

## Data & History

```bash
overcode export output.parquet [-a] [-t] [-p]    # Export to Parquet for Jupyter
overcode history [name]                           # Show archived session history
```

## Status Indicators

| Status | Meaning |
|--------|---------|
| GREEN | Running actively |
| YELLOW | No standing instructions |
| ORANGE | Waiting for supervisor |
| RED | Waiting for user input |
| YELLOW with eye | Waiting for oversight report |

## Unblocking Stuck Agents

```bash
overcode show my-agent -n 100   # See what it's stuck on
overcode send my-agent enter     # Approve permission
overcode send my-agent escape    # Reject permission
overcode send my-agent "yes"     # Send text response
```

## File Locations

```
~/.overcode/
├── sessions/<tmux-session>/
│   ├── sessions.json              # Session state
│   ├── monitor_daemon_state.json  # Daemon state (TUI reads this)
│   ├── report_<agent>.json        # Oversight reports
│   └── *.log                      # Daemon logs
├── config.yaml
└── presets.json
```
