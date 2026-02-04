# Advanced Features

This guide covers overcode's advanced capabilities for power users.

## Sleep Mode

Sleep mode lets you pause agents and exclude them from statistics.

### How It Works

1. Press `z` on an agent to toggle sleep mode
2. Sleeping agents show a grey/sleepy indicator (💤)
3. They're excluded from:
   - Green time calculations
   - Cost summaries
   - Supervisor oversight
4. Press `Z` to hide sleeping agents from the list entirely

### Use Cases

- **Focus time**: Sleep agents you're not actively monitoring
- **Pause without killing**: Keep agent state but stop activity
- **Clean metrics**: Exclude agents from efficiency calculations
- **Handover prep**: Sleep non-critical agents before handoff

### Constraints

- **Cannot sleep running agents**: If Claude is actively working, you must wait for it to pause before sleeping. This prevents accidentally ignoring an agent mid-task.

## Agent Priority & Sorting

Organize agents by importance with priority values.

### Setting Priority

1. Press `V` on an agent to edit its value
2. Default is 1000; higher = more important
3. Press `S` to cycle sort modes until you reach "value" sorting

### Sort Modes

| Mode | Description |
|------|-------------|
| Alphabetical | Sort by agent name |
| Status | Red (stalled) first, then yellow, orange, green |
| Value | Highest priority values first |

### CLI Access

```bash
overcode set-value my-agent 2000  # High priority
overcode set-value cleanup-bot 100  # Low priority
```

## Jump to Attention

Press `b` to quickly navigate to agents needing attention.

### Priority Order

1. **Bell** (🔔) - Unvisited stalled agents (highest priority)
2. **Red** - Waiting for user input (already visited)
3. **Yellow** - No standing instructions
4. **Orange** - Waiting for supervisor

Press `b` repeatedly to cycle through all attention-needing agents.

### Bell Indicator

The bell (🔔) appears when an agent stalls and you haven't visited it yet. Focus the agent to mark it as "visited" and clear the bell. This helps you track which stalled agents you've already seen.

## Handover Mode

Prepare all active agents for handoff to another person or session.

### How to Use

1. Put any non-critical agents to sleep with `z`
2. Press `H` twice (double-press for confirmation)
3. Each awake agent receives instructions to:
   - Create a new branch (if on main/master)
   - Commit current changes
   - Push to the branch
   - Create a draft PR if none exists
   - Post a handover summary as a PR comment

### What Gets Committed

Each agent commits its work-in-progress with a summary of:
- What was being worked on
- Current state
- Any blockers or next steps

This creates a clean checkpoint for async collaboration.

## Sync to Main

Quickly reset an agent to the main branch.

### How to Use

1. Select the agent
2. Press `c` twice (double-press for confirmation)
3. The agent runs:
   - `git checkout main && git pull`
   - `/clear` to reset conversation context

### Use Cases

- Agent went down a wrong path
- Starting fresh on a new task
- Pulling in changes from other agents

## Remote Monitoring

Monitor agents from anywhere with the web dashboard.

### Mobile Dashboard

Start the mobile-optimized dashboard:

```bash
overcode serve --host 0.0.0.0 --port 8080
```

Then access `http://<your-ip>:8080` from your phone or tablet.

Features:
- Read-only view
- Auto-refreshes every 5 seconds
- Shows agent status, costs, activity
- Optimized for mobile screens

### Analytics Dashboard

For historical analysis:

```bash
overcode web --port 8080
```

Features:
- Summary statistics
- Daily activity charts
- Session browser with sorting
- Timeline visualization
- Efficiency metrics
- Cost analysis

### TUI Toggle

Press `w` in the TUI to start/stop the web server. The URL appears in the daemon panel.

## AI Summarizer

Get AI-generated summaries of agent activity.

### Setup

1. Set your API key:
   ```bash
   export OPENAI_API_KEY="sk-..."
   ```

2. Enable in TUI by pressing `a`

3. Press `l` to cycle to AI summary mode (💬 or 📖)

### Summary Modes

| Mode | Description |
|------|-------------|
| AI Short (💬) | Brief summary of current activity |
| AI Long (📖) | Detailed summary with broader context and goals |

### Custom Provider

Use any OpenAI-compatible API:

```yaml
# In ~/.overcode/config.yaml
summarizer:
  api_url: https://your-provider.com/v1/chat/completions
  model: your-model-name
  api_key_var: YOUR_API_KEY_VAR
```

### Cost

The summarizer only runs when enabled and the TUI is open. It makes requests every few seconds, so costs are minimal with GPT-4o-mini but can add up with larger models.

## Human Annotations

Add notes to agents for yourself or collaborators.

### Adding Annotations

1. Select an agent
2. Press `I` to edit annotation
3. Type your note
4. Press `Enter` to save

### Viewing

Press `l` to cycle to annotation mode (✏️) to see annotations in the summary line.

### Use Cases

- "Waiting on API credentials"
- "Don't touch until John reviews"
- "Blocked by issue #123"
- "Ready for testing"

## Data Export

Export session data for analysis in Jupyter or other tools.

### Parquet Export

```bash
overcode export analysis.parquet
```

Options:
- `--archived` / `-a`: Include archived sessions (default: true)
- `--timeline` / `-t`: Include status timeline (default: true)
- `--presence` / `-p`: Include presence data (default: true)

### Included Data

- Session metadata (name, directory, repo)
- Token usage and costs
- Time metrics (green, idle, sleep)
- Status history timeline
- User presence (if available)

### Jupyter Analysis

```python
import pandas as pd

df = pd.read_parquet('analysis.parquet')

# Cost by agent
df.groupby('name')['cost'].sum().sort_values(ascending=False)

# Green time efficiency
df['efficiency'] = df['green_time'] / (df['green_time'] + df['idle_time'])
```

## Presence Tracking (macOS)

Overcode can track when you're at your computer to correlate with agent activity.

### How It Works

On macOS, overcode uses IOKit to detect idle time. When you're away (keyboard/mouse inactive), it logs your presence state.

### Data Location

```
~/.overcode/presence_log.csv
```

### Analytics

The web dashboard (`overcode web`) overlays presence data on timelines, showing when agents were working while you were away vs. actively monitoring.

## Cloud Relay

Push status to a remote endpoint for custom integrations.

### Configuration

```yaml
relay:
  enabled: true
  url: https://your-worker.workers.dev/update
  api_key: your-secret-key
  interval: 30  # seconds
```

### Payload

The relay sends JSON with:
- All agent statuses
- Token usage
- Costs
- Current activity

### Use Cases

- Custom dashboards
- Slack notifications
- Integration with project management tools

## Multiple Daemon Warning

If the TUI detects multiple monitor daemon processes, it shows a warning every 5 seconds.

### Resolution

Press `\` to restart the monitor daemon. This kills existing daemons and starts a fresh one.

### Prevention

This typically happens if:
- Multiple TUIs are started simultaneously
- Previous daemon didn't shut down cleanly

## Diagnostic Mode

For debugging TUI issues:

```bash
overcode monitor --diagnostics
```

This disables auto-refresh timers, letting you manually refresh with `r` and observe state changes.

## Terminal Compatibility

### Color Issues

If colors render incorrectly, press `M` for monochrome mode. This strips ANSI codes from the preview pane.

### Mouse Issues

If mouse events interfere with your terminal:
1. Press `y` to enter copy mode
2. Mouse capture is disabled
3. Select text normally
4. Press `y` again to re-enable

### Large Repos

For repos with many files, Claude's output can be verbose. Use `v` to cycle through detail line counts (5/10/20/50) to manage preview pane size.
