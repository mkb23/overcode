#!/bin/bash
# Start one short background session (claude --bg) in a temp dir with the spike hook logger.
S="$(cd "$(dirname "$0")" && pwd)"
d="$S/runs/bgsess"; rm -rf "$d"; mkdir -p "$d/work"
cd "$S/runs/teamt/work"
env -i HOME="$HOME" PATH="$PATH" USER="$USER" LANG=en_US.UTF-8 TERM=xterm-256color SHELL=/bin/zsh \
  SPIKE_HOOKLOG="$d/hooks.jsonl" \
  claude --bg --model haiku --settings "$S/settings.json" --dangerously-skip-permissions -n spike-bg-probe \
  "Run the Bash command: sleep 20 && echo bg-probe-done . Then reply with its output only." \
  > "$d/bg_start.txt" 2>&1
echo "exit=$?"; cat "$d/bg_start.txt"
