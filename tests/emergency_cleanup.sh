#!/bin/bash
# Emergency cleanup script for overcode E2E tests
#
# Use this if tests crash and leave processes/sessions behind
# This ONLY kills Claude processes belonging to the test tmux session,
# NOT all Claude processes (preserves user's interactive session)

set -e

TEST_TMUX_SESSION="overcode-e2e-test"

echo "=========================================="
echo "🚨 EMERGENCY CLEANUP"
echo "=========================================="
echo ""
echo "⚠️  This will kill Claude processes in test session: $TEST_TMUX_SESSION"
echo "   (Your other Claude sessions will NOT be affected)"
echo "   Press Ctrl+C within 3 seconds to cancel..."
echo ""

for i in 3 2 1; do
    echo "   $i..."
    sleep 1
done

echo ""
echo "Starting cleanup..."
echo ""

# Step 1: Kill Claude processes ONLY in the test tmux session
echo "[1/5] Killing Claude processes in test session..."

# Get pane PIDs from the test tmux session
PANE_PIDS=$(tmux list-panes -s -t "$TEST_TMUX_SESSION" -F "#{pane_pid}" 2>/dev/null || echo "")

if [ -n "$PANE_PIDS" ]; then
    KILLED_COUNT=0
    for pane_pid in $PANE_PIDS; do
        # Find claude processes that are children of this pane
        CLAUDE_PIDS=$(pgrep -P "$pane_pid" -f claude 2>/dev/null || echo "")
        for pid in $CLAUDE_PIDS; do
            if [ -n "$pid" ]; then
                kill -9 "$pid" 2>/dev/null && KILLED_COUNT=$((KILLED_COUNT + 1))
            fi
        done
    done
    if [ "$KILLED_COUNT" -gt 0 ]; then
        echo "      ✓ Killed $KILLED_COUNT Claude process(es) from test session"
    else
        echo "      ℹ️  No Claude processes found in test session"
    fi
else
    echo "      ℹ️  Test tmux session not found (already cleaned?)"
fi

# Step 2: Kill test tmux session
echo "[2/5] Killing test tmux session..."
if tmux kill-session -t "$TEST_TMUX_SESSION" 2>/dev/null; then
    echo "      ✓ Tmux session killed"
else
    echo "      ℹ️  No test tmux session found"
fi

# Step 3: Remove test files
echo "[3/5] Removing test files..."
if [ -d "/tmp/overcode_e2e_test" ]; then
    rm -rf /tmp/overcode_e2e_test
    echo "      ✓ Test directory removed"
else
    echo "      ℹ️  Test directory doesn't exist"
fi

# Step 4: Clean state file
echo "[4/5] Cleaning state file..."
python3 <<'EOF'
import json
from pathlib import Path

state_file = Path.home() / ".overcode" / "sessions" / "sessions.json"
if state_file.exists():
    try:
        with open(state_file, 'r') as f:
            state = json.load(f)
        original_count = len(state)
        updated = {k: v for k, v in state.items()
                  if not v.get('name', '').startswith('test-')}
        with open(state_file, 'w') as f:
            json.dump(updated, f, indent=2)
        cleaned_count = original_count - len(updated)
        if cleaned_count > 0:
            print(f"      ✓ Cleaned {cleaned_count} test session(s) from state file")
        else:
            print(f"      ℹ️  No test sessions in state file")
    except Exception as e:
        print(f"      ⚠️  Warning: Could not clean state file: {e}")
else:
    print("      ℹ️  State file doesn't exist")
EOF

# Step 5: Verify cleanup
echo "[5/5] Verifying cleanup..."
echo ""

# Check for orphaned Claude processes from test session
# Re-check if any panes still exist in the test session
PANE_PIDS=$(tmux list-panes -s -t "$TEST_TMUX_SESSION" -F "#{pane_pid}" 2>/dev/null || echo "")
if [ -n "$PANE_PIDS" ]; then
    ORPHAN_COUNT=0
    for pane_pid in $PANE_PIDS; do
        CLAUDE_PIDS=$(pgrep -P "$pane_pid" -f claude 2>/dev/null || echo "")
        for pid in $CLAUDE_PIDS; do
            if [ -n "$pid" ]; then
                echo "      ⚠️  Orphaned Claude process: PID $pid"
                ORPHAN_COUNT=$((ORPHAN_COUNT + 1))
            fi
        done
    done
    if [ "$ORPHAN_COUNT" -gt 0 ]; then
        echo "      ⚠️  WARNING: $ORPHAN_COUNT test Claude process(es) still running!"
        CLEANUP_FAILED=1
    else
        echo "      ✓ No orphaned test Claude processes found"
    fi
else
    echo "      ✓ Test session cleaned up (no panes to check)"
fi

# Check for test tmux session
if tmux has-session -t "$TEST_TMUX_SESSION" 2>/dev/null; then
    echo "      ⚠️  WARNING: Test tmux session still exists!"
    echo "      Manual kill: tmux kill-session -t $TEST_TMUX_SESSION"
    CLEANUP_FAILED=1
else
    echo "      ✓ No test tmux session found"
fi

# Check for test directory
if [ -d "/tmp/overcode_e2e_test" ]; then
    echo "      ⚠️  WARNING: Test directory still exists!"
    echo "      Manual removal: rm -rf /tmp/overcode_e2e_test"
    CLEANUP_FAILED=1
else
    echo "      ✓ Test directory removed"
fi

echo ""
echo "=========================================="

if [ -n "$CLEANUP_FAILED" ]; then
    echo "⚠️  CLEANUP INCOMPLETE"
    echo "=========================================="
    echo ""
    echo "Some resources could not be cleaned up automatically."
    echo "Please run the manual commands shown above."
    exit 1
else
    echo "✓ CLEANUP COMPLETE"
    echo "=========================================="
    echo ""
    echo "All test resources have been cleaned up."
    exit 0
fi
