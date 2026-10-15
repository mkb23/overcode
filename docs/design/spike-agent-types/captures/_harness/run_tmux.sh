#!/bin/bash
# usage: run_tmux.sh <name> [extra claude args...]  -- starts interactive claude on private socket spike-<name>
S="$(cd "$(dirname "$0")" && pwd)"
name="$1"; shift
d="$S/runs/$name"; rm -rf "$d"; mkdir -p "$d/work"
cd "$d/work" && git init -q . && echo "spike $name" > README.md && git add . && git -c user.email=s@x -c user.name=s commit -qm init
tmux -L "spike-$name" kill-server 2>/dev/null
env -i HOME="$HOME" PATH="$PATH" USER="$USER" LANG=en_US.UTF-8 TERM=xterm-256color SHELL=/bin/zsh \
  tmux -L "spike-$name" -f /dev/null new-session -d -s main -x 200 -y 50 -c "$d/work" \
  env SPIKE_HOOKLOG="$d/hooks.jsonl" $SPIKE_EXTRA_ENV claude --model haiku --settings "$S/settings.json" \
  --dangerously-skip-permissions "$@"
echo "started spike-$name in $d"
