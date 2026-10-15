#!/bin/bash
# usage: run_p.sh <name> <prompt> [extra claude args...]
# Runs claude -p in a fresh temp dir with a clean env and the spike hook logger.
S="$(cd "$(dirname "$0")" && pwd)"
name="$1"; shift; prompt="$(cat "$S/prompts/$name.txt")"
d="$S/runs/$name"; rm -rf "$d"; mkdir -p "$d/work"
cd "$d/work" && git init -q . && echo "spike $name" > README.md && git add . && git -c user.email=s@x -c user.name=s commit -qm init
env -i HOME="$HOME" PATH="$PATH" USER="$USER" LANG=en_US.UTF-8 TERM=xterm-256color SHELL=/bin/zsh \
  SPIKE_HOOKLOG="$d/hooks.jsonl" $SPIKE_EXTRA_ENV \
  claude -p "$prompt" --model haiku --settings "$S/settings.json" \
  --output-format stream-json --verbose --dangerously-skip-permissions "$@" \
  > "$d/stream.jsonl" 2> "$d/stderr.txt"
echo "exit=$? dir=$d"
