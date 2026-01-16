#!/bin/bash
# Setup tmux layout for Overcode supervisor
# Top pane: TUI dashboard
# Bottom pane: Overcode agent (Claude session)

set -e

SESSION_NAME="${1:-agents}"
CONTROLLER_SESSION="overcode-controller"

# Check if controller session already exists
if tmux has-session -t "$CONTROLLER_SESSION" 2>/dev/null; then
    echo "Controller session already exists. Attaching..."
    exec tmux attach-session -t "$CONTROLLER_SESSION"
fi

# Find the overcode installation
OVERCODE_BIN=$(which overcode 2>/dev/null || echo "")
if [ -z "$OVERCODE_BIN" ]; then
    # Try local installation
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    VENV_PYTHON="$SCRIPT_DIR/../../.venv/bin/python"
    if [ -f "$VENV_PYTHON" ]; then
        TUI_CMD="$VENV_PYTHON -m overcode.tui"
    else
        echo "Error: Cannot find overcode installation"
        exit 1
    fi
else
    TUI_CMD="$OVERCODE_BIN tui-only"
fi

# Create new session with the TUI
tmux new-session -d -s "$CONTROLLER_SESSION" -n "controller"

# Split window horizontally (top 33%, bottom 66%)
tmux split-window -v -p 66 -t "$CONTROLLER_SESSION:0"

# Top pane: Run the TUI (without piping to preserve terminal control)
tmux send-keys -t "$CONTROLLER_SESSION:0.0" "PYTHONUNBUFFERED=1 python -m overcode.tui $SESSION_NAME" C-m

# Bottom pane: Launch Claude (no auto-prompt - let user interact naturally)
tmux send-keys -t "$CONTROLLER_SESSION:0.1" "claude code" C-m

# Set pane titles
tmux select-pane -t "$CONTROLLER_SESSION:0.0" -T "Overcode Monitor"
tmux select-pane -t "$CONTROLLER_SESSION:0.1" -T "Controller"

# Attach to the session
exec tmux attach-session -t "$CONTROLLER_SESSION"
