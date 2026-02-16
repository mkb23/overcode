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
| Tree | Parent/child hierarchy with tree connectors |

### CLI Access

```bash
overcode set-value my-agent 2000  # High priority
overcode set-value cleanup-bot 100  # Low priority
```

## Heartbeat

Heartbeat sends a periodic instruction to an agent, keeping it working even when it stalls at a prompt or finishes a task.

### Setting Up

1. Select an agent and press `H`
2. Enter a frequency (e.g., `5m`, `1h`, `300`)
3. Enter the instruction to send at each heartbeat (e.g., "continue working on the task")

The agent will receive the instruction at the configured interval whenever it's not actively running.

### How It Works

- The monitor daemon checks heartbeat timers every loop (~10s)
- If the agent is idle and the heartbeat interval has elapsed, the instruction is sent to the agent's tmux window
- The agent starts working as if a user typed the instruction
- Heartbeat timing resets after each send

### Pausing

Press `H` and enter `off` to disable heartbeat. The configuration is preserved — re-enable with `H` and a new frequency.

### Interaction with Cost Budgets

When an agent exceeds its cost budget, heartbeats are **silently skipped**. The heartbeat configuration stays intact — nothing is disabled or modified. Once the budget is raised or cleared, heartbeats resume automatically on the next daemon loop.

This prevents runaway spending: an agent can't burn through budget indefinitely via heartbeat-driven work.

### Interaction with Sleep Mode

Sleeping agents also skip heartbeats. Wake the agent with `z` to resume.

## Cost Budgets

Set a spending limit on individual agents to manage usage.

### Setting a Budget

**TUI**: Select an agent and press `B`, enter a dollar amount (e.g., `5.00`). Enter `0` to clear.

**CLI**:
```bash
overcode set-budget my-agent 5.00    # $5 budget
overcode set-budget my-agent 0       # Clear budget
```

### Display

When `show_cost` is enabled (`$` key), agents with budgets show `$cost/$budget` instead of just `$cost`:
- **Orange** — under 80% of budget
- **Yellow** — 80-99% of budget
- **Red** — budget exceeded

The `overcode show <agent>` command also displays the budget on the Cost line.

### What Happens When Budget Is Exceeded

Budget enforcement is **soft** — the agent is not killed or put to sleep. Instead:

1. **Heartbeats are skipped** — the agent won't receive periodic instructions, so it naturally stops when it finishes its current work or hits a permission prompt
2. **Supervision is skipped** — the supervisor daemon won't launch a daemon Claude to approve prompts or steer the agent

The agent can still finish whatever it's currently doing. It just won't receive any more automated nudges.

### Resuming After Budget

To continue past a budget, either raise it or clear it:

```bash
overcode set-budget my-agent 10.00   # Raise to $10
overcode set-budget my-agent 0       # Remove limit entirely
```

Heartbeats and supervision resume automatically on the next daemon loop (~10s).

### No Global Default

Budgets are per-agent only. There is no global default budget in config.

## Agent Hierarchy

Agents can spawn child agents, creating a parent/child tree. This enables delegation patterns where a parent agent launches children to handle subtasks.

### Launching Child Agents

**CLI:**
```bash
# Explicit parent
overcode launch -n subtask --parent my-agent -d ~/project -p "Fix the auth bug"

# With follow mode (blocks until child finishes)
overcode launch -n subtask --parent my-agent --follow -p "Fix the auth bug"
```

**Auto-detection:** When an agent runs `overcode launch` from within its own session, the parent is auto-detected from the `OVERCODE_SESSION_NAME` environment variable.

**Depth limit:** Hierarchies are limited to 5 levels deep.

### Follow Mode

Follow mode streams a child agent's output to stdout and blocks until the child reports completion via `overcode report`.

```bash
# Launch and follow — prompt must include overcode report at the end
overcode launch -n child --follow -p "Do the task. When done: overcode report --status success"

# Follow an already-running agent
overcode follow child-agent
```

When the child reports success, it's marked "done" — it stays alive in tmux but is hidden from default views.

### Reporting Completion

Child agents must explicitly signal completion by running `overcode report`:

```bash
overcode report --status success --reason "All tests pass"
overcode report --status failure --reason "Build failed"
```

Without a report, the child enters `waiting_oversight` status (👁️) when it stops. The parent's follow mode blocks until a report arrives or the oversight policy triggers.

**Always include `overcode report` at the end of your `--prompt`.** This is the most common mistake when setting up delegation.

### Oversight Policies

Control what happens when a child stops without reporting:

```bash
# Default: wait indefinitely for a report
overcode launch -n child --follow -p "..."

# Fail immediately if child stops without reporting
overcode launch -n child --follow --on-stuck fail -p "..."

# Wait up to 5 minutes, then fail
overcode launch -n child --follow --oversight-timeout 5m -p "..."
```

The TUI shows a countdown timer (⏳) for agents with a timeout policy.

### Budget Transfer

Transfer budget from a parent to a child agent:

```bash
overcode budget transfer parent-agent child-agent 2.50
overcode budget show          # See all budgets
overcode budget show my-agent # See one agent's budget
```

The source must be an ancestor of the target.

### Cascade Kill

Killing a parent kills all its descendants (deepest-first):

```bash
overcode kill parent-agent           # Kills parent + all children
overcode kill parent-agent --no-cascade  # Kills only parent, orphans children
```

### TUI Tree View

Press `S` to cycle to "Tree" sort mode. Agents display in a hierarchy with tree connectors:

```
parent-agent    🟢  2.5h  ...
├─child-1       🟢  1.2h  ...
│ └─grandchild  🟠  0.5h  ...
└─child-2       ✓   0.8h  ...
```

- **`X`** — Collapse/expand children of the focused agent
- **`D`** — Show/hide "done" child agents (hidden by default)

The child count column (👶) shows how many direct children each agent has.

### Done Agents

When a child reports success or failure via `overcode report`, it enters the "done" state:
- Hidden from default list and TUI views
- Press `D` in TUI or use `--show-done` in CLI to reveal them
- Auto-archived by the monitor daemon after 1 hour
- Manually archive with `overcode cleanup --done`

### Waiting for Oversight

If a child stops without calling `overcode report`, it enters `waiting_oversight` (👁️):
- Visible in the TUI with a yellow indicator
- Parent's follow mode remains blocked, waiting for a report
- With `--oversight-timeout`, a countdown timer appears in the TUI
- Resolve by attaching to the child's tmux window and running `overcode report` manually, or let the timeout expire

### Filtered List

View a subtree from the CLI:

```bash
overcode list parent-agent    # Shows parent + all descendants
overcode list --show-done     # Include done agents
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

## Sister Integration

Aggregate agents from multiple machines into a single TUI. When running overcode on 2-3 machines simultaneously, sister integration gives you read-only visibility into all agents from one place.

### Prerequisites

Each machine must be running `overcode serve`:

```bash
# On each remote machine
overcode serve
```

### Setup Option A: SSH Tunnels (Recommended)

SSH tunnels keep all traffic encrypted with no API key needed.

On the **monitoring machine**, add tunnels in `~/.ssh/config`:

```
Host macbook-pro
  HostName 192.168.1.10
  LocalForward 15337 localhost:8080

Host desktop
  HostName 192.168.1.20
  LocalForward 25337 localhost:8080
```

Open the tunnels (or use `autossh` for persistence):

```bash
ssh -N macbook-pro &
ssh -N desktop &
```

Configure sisters in `~/.overcode/config.yaml`:

```yaml
hostname: "mac-studio"

sisters:
  - name: "macbook-pro"
    url: "http://localhost:15337"
  - name: "desktop"
    url: "http://localhost:25337"
```

### Setup Option B: Direct LAN with API Key

Expose the web server directly on the LAN. Requires an API key for security.

On each **remote machine**, add to `~/.overcode/config.yaml`:

```yaml
web:
  api_key: "your-shared-secret"
```

Start the server bound to all interfaces:

```bash
overcode serve --host 0.0.0.0
```

On the **monitoring machine**, configure sisters:

```yaml
hostname: "mac-studio"

sisters:
  - name: "macbook-pro"
    url: "http://192.168.1.10:8080"
    api_key: "your-shared-secret"
  - name: "desktop"
    url: "http://192.168.1.20:8080"
    api_key: "your-shared-secret"
```

### What It Looks Like

When sisters are configured, a **Host** column appears in the TUI:

```
Host           Name       Status     Cost    Green    Activity
mac-studio     agent1     running    $1.23   12m      Writing tests...
mac-studio     agent2     waiting    $0.45    8m      (waiting for user)
macbook-pro    agent3     running    $2.34   25m      Refactoring auth...
desktop        agent5     running    $0.67    5m      Adding endpoint...
```

- Local agents appear first, remote agents grouped by host
- Local hostname is dimmed, remote hostnames are bold magenta
- The footer shows: `Sisters: macbook-pro(2/3) desktop(1/1)`
- Remote agents are read-only (interactive actions are blocked)

### Troubleshooting

- **Sister shows `--`**: Check that `overcode serve` is running on the remote machine and the tunnel/URL is reachable
- **401 Unauthorized**: API key mismatch between server and client config
- **No Host column**: Sisters config is empty or missing — the column is auto-hidden

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
