#!/bin/sh
# Sample the running monitor TUI and monitor daemon with py-spy (#486).
#
#   sudo scripts/profile_tui.sh [session] [seconds]
#
# Defaults: session "agents", 60 seconds. Finds both PIDs itself, records
# both at once without pausing them (--nonblocking), and writes collapsed
# stacks (tui.txt, daemon.txt) to /tmp/overcode-profile-<time>/, owned by
# the user who ran sudo. py-spy needs root on macOS.

set -eu

SESSION="${1:-agents}"
DURATION="${2:-60}"
USER_HOME=$(eval echo "~${SUDO_USER:-$USER}")
PYSPY="${PYSPY:-$USER_HOME/.local/bin/py-spy}"

if [ "$(id -u)" -ne 0 ]; then
    echo "py-spy needs root: sudo $0 $*" >&2
    exit 1
fi
if [ ! -x "$PYSPY" ]; then
    echo "py-spy not found at $PYSPY (set PYSPY=/path/to/py-spy)" >&2
    exit 1
fi

# The python process itself, not the zsh wrapper that relaunches it
TUI_PID=$(pgrep -f "python.*overcode monitor --session $SESSION( |\$)" | head -1 || true)
DAEMON_PID=$(pgrep -f "overcode\.monitor_daemon --session $SESSION\$" | head -1 || true)

if [ -z "$TUI_PID" ] && [ -z "$DAEMON_PID" ]; then
    echo "No monitor TUI or daemon found for session '$SESSION'" >&2
    exit 1
fi

OUT="/tmp/overcode-profile-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT"

record() {  # name pid
    echo "Recording $1 (pid $2) for ${DURATION}s..."
    "$PYSPY" record --pid "$2" --duration "$DURATION" --rate 100 \
        --nonblocking --threads --format raw -o "$OUT/$1.txt" >"$OUT/$1.log" 2>&1 &
}

[ -n "$TUI_PID" ] && record tui "$TUI_PID" || echo "No TUI found, skipping"
[ -n "$DAEMON_PID" ] && record daemon "$DAEMON_PID" || echo "No daemon found, skipping"

# CPU% alongside the samples, for the headline number
ps -o pid=,pcpu=,etime=,command= -p "${TUI_PID:-0},${DAEMON_PID:-0}" >"$OUT/ps-start.txt" 2>/dev/null || true
wait
ps -o pid=,pcpu=,etime=,command= -p "${TUI_PID:-0},${DAEMON_PID:-0}" >"$OUT/ps-end.txt" 2>/dev/null || true

[ -n "${SUDO_USER:-}" ] && chown -R "$SUDO_USER" "$OUT"
echo
echo "Done: $OUT"
ls -l "$OUT"
