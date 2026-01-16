#!/bin/bash
# Quick E2E test runner for overcode
# Run this script to execute the multi-agent jokes test

set -e

echo "=========================================="
echo "Overcode E2E Test Runner"
echo "=========================================="
echo ""

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v tmux &> /dev/null; then
    echo "❌ ERROR: tmux is not installed"
    echo "   Install with: sudo apt-get install tmux (Ubuntu/Debian)"
    echo "             or: brew install tmux (macOS)"
    exit 1
fi

if ! command -v claude &> /dev/null; then
    echo "❌ ERROR: claude command not found"
    echo "   Make sure Claude Code is installed and in your PATH"
    exit 1
fi

echo "✅ tmux found: $(tmux -V)"
echo "✅ claude found: $(which claude)"
echo ""

# Check for API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  WARNING: ANTHROPIC_API_KEY not set"
    echo "   The test may fail if Claude cannot authenticate"
    echo ""
fi

# Ensure we're in the right directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Clean up any existing test sessions
echo "Cleaning up existing test sessions..."
tmux kill-session -t overcode-e2e-test 2>/dev/null || true
rm -rf /tmp/overcode_e2e_test 2>/dev/null || true
echo "✅ Cleanup complete"
echo ""

# Run the test
echo "Starting E2E test..."
echo "This will take approximately 5-10 minutes"
echo ""
echo "Test phases:"
echo "  1. Launch two Claude agents"
echo "  2. Both agents write jokes to files"
echo "  3. Both agents wait for feedback"
echo "  4. Provide feedback via tmux"
echo "  5. Verify agents unblock and improve jokes"
echo ""
echo "Press Ctrl+C to cancel (not recommended)"
echo ""
sleep 2

# Run test with Python directly (faster than pytest)
python tests/test_e2e_multi_agent_jokes.py

# Check exit code
if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "✅ E2E TEST PASSED!"
    echo "=========================================="
    echo ""
    echo "What was tested:"
    echo "  ✓ Multi-agent coordination"
    echo "  ✓ File I/O from Claude agents"
    echo "  ✓ Status detection (waiting state)"
    echo "  ✓ Feedback delivery via tmux"
    echo "  ✓ Agent unblocking and output improvement"
    echo ""
    exit 0
else
    echo ""
    echo "=========================================="
    echo "❌ E2E TEST FAILED"
    echo "=========================================="
    echo ""
    echo "Debugging tips:"
    echo "  1. Check if tmux session exists: tmux ls"
    echo "  2. Attach to session: tmux attach -t overcode-e2e-test"
    echo "  3. Check test output files: ls -la /tmp/overcode_e2e_test/"
    echo "  4. Review state file: cat ~/.overcode/sessions/sessions.json"
    echo ""
    echo "Clean up manually:"
    echo "  tmux kill-session -t overcode-e2e-test"
    echo "  rm -rf /tmp/overcode_e2e_test"
    echo ""
    exit 1
fi
