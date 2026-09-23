# Overcode 0.5.5 Release Notes

- **No more runaway CPU (#479).** The TUI and daemon no longer re-read transcripts, state files or history on every tick. The TUI pauses and the daemon slows to 10 s while nobody is watching.
- **Terminated agents are archived** to `archive.jsonl` an hour after they end, so `sessions.json` stops growing. `archive.json` is migrated on first run.
- **opencode status is accurate (#474).** Sub-agents no longer end the parent's turn, errors stay visible, and `/exit` shows as terminated.
- **`overcode rename` and Ctrl+N (#478)** rename an agent and keep its conversation. The old name still works as an alias, and busy agents are refused unless forced.
- **Re-running `overcode tmux` works again (#480).** A stale setup lock used to block it.
- **Help lists every key** (Ctrl+R, Ctrl+P and J were missing), and the defunct "handover all" feature is gone.
