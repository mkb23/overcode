# TUI Eye - Visual TUI Testing Skill

```yaml
---
name: tui-eye
description: Interactive visual testing of TUI applications. Use when testing the overcode supervisor TUI, validating layouts, or running smoke tests.
disable-model-invocation: true
---
```

You are performing visual TUI testing using the `tui-eye` tool. This tool gives you "eyes" into TUI applications by capturing screenshots as PNG images that you can read and analyze.

## Core Commands

```bash
# Start a TUI in a controlled tmux session (220x40 default)
tui-eye start "overcode monitor" --size 220x40

# Capture screenshot for visual inspection
tui-eye screenshot /tmp/tui.png

# Read the screenshot (use Claude Code's Read tool)
# Then analyze: layout, alignment, colors, text overflow, etc.

# Send keystrokes
tui-eye send j j enter      # Navigate down twice, press enter
tui-eye send h              # Toggle help overlay
tui-eye send escape         # Close dialogs/cancel

# Wait for content to appear
tui-eye wait-for "Session:" --timeout 10

# Get text-only capture (for searching/assertions)
tui-eye capture --text

# Check session status
tui-eye status

# Clean up when done
tui-eye stop
```

## Workflow: Visual Testing

1. **Start the TUI**
   ```bash
   tui-eye start "overcode monitor" --size 220x40
   ```

2. **Capture & Analyze**
   ```bash
   tui-eye screenshot /tmp/check.png
   ```
   Then read `/tmp/check.png` and visually inspect:
   - Is the layout correct?
   - Are columns aligned?
   - Is text truncated or wrapped unexpectedly?
   - Are colors/status indicators showing correctly?

3. **Interact**
   ```bash
   tui-eye send j       # Navigate
   tui-eye send enter   # Select/confirm
   tui-eye send h       # Toggle help
   ```

4. **Verify Changes**
   ```bash
   tui-eye screenshot /tmp/after.png
   ```
   Compare to expected state.

5. **Clean Up**
   ```bash
   tui-eye stop
   ```

## Key Mappings

| Key | tmux Name | Description |
|-----|-----------|-------------|
| `j` | j | Navigate down |
| `k` | k | Navigate up |
| `enter` | Enter | Confirm/select |
| `escape` | Escape | Cancel/close |
| `h` | h | Toggle help |
| `q` | q | Quit (some TUIs) |
| `tab` | Tab | Next field |
| `space` | Space | Toggle/expand |

## Example: Smoke Test

```bash
# Start supervisor TUI
tui-eye start "overcode monitor" --size 220x45

# Wait for initial render
tui-eye wait-for "Timeline:" --timeout 10

# Capture initial state
tui-eye screenshot /tmp/smoke-1.png
# [Read /tmp/smoke-1.png - verify layout looks correct]

# Test help overlay
tui-eye send h
tui-eye screenshot /tmp/smoke-help.png
# [Read - verify help is displayed]

tui-eye send h
tui-eye screenshot /tmp/smoke-help-closed.png
# [Read - verify help closed, main view restored]

# Navigate if there are sessions
tui-eye send j j
tui-eye screenshot /tmp/smoke-nav.png
# [Read - verify navigation worked]

# Done
tui-eye stop
```

## Example: Multi-Agent Monitoring

```bash
# Launch some test agents first
overcode launch --name test-agent-1 --prompt "Write hello world"
overcode launch --name test-agent-2 --prompt "List files"

# Start monitor
tui-eye start "overcode monitor" --size 220x45

# Periodic monitoring loop
tui-eye wait-for "test-agent" --timeout 30
tui-eye screenshot /tmp/monitor-1.png
# [Read - check agent statuses, timelines]

# If an agent needs attention, navigate and interact
tui-eye send j enter   # Select agent
tui-eye screenshot /tmp/agent-detail.png
# [Read - see agent output]

# Continue monitoring...
tui-eye stop
```

## Visual Checks to Perform

When reading screenshots, check for:

- **Layout**: Header, timeline, agent list all visible?
- **Alignment**: Columns aligned, percentages right-justified?
- **Colors**: Status indicators using correct colors (green=running, red=waiting)?
- **Text**: No unexpected wrapping or truncation?
- **Timeline**: Bars extending full width? Percentage shown?
- **Responsiveness**: After interactions, UI updated correctly?

## Troubleshooting

**Screenshot too narrow / lines wrapping:**
```bash
tui-eye screenshot /tmp/x.png --width 220 --height 45
```

**Can't see full content:**
Increase height:
```bash
tui-eye start "overcode monitor" --size 220x60
```

**Session already exists:**
```bash
tui-eye stop
tui-eye start "overcode monitor"
```

**Keys not working:**
Check session is running:
```bash
tui-eye status
```

## Arguments

`$ARGUMENTS` - Optional test scenario to run. Examples:
- `help-toggle` - Test the help overlay toggle
- `navigation` - Test up/down navigation
- `full-smoke` - Run complete smoke test
