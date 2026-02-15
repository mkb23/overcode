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
| 👁️ YELLOW | Waiting for oversight report |

## Unblocking Stuck Agents

```bash
overcode show my-agent -n 100   # See what it's stuck on
overcode send my-agent enter     # Approve permission
overcode send my-agent escape    # Reject permission
overcode send my-agent "yes"     # Send text response
```

## Standing Instructions Presets

`overcode instruct <name> <preset>` — available presets: `DO_NOTHING`, `STANDARD`, `PERMISSIVE`, `CAUTIOUS`, `RESEARCH`, `CODING`, `TESTING`, `REVIEW`, `DEPLOY`, `AUTONOMOUS`, `MINIMAL`.

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
