# CLI Reference

Complete reference for all overcode commands.

## Agent Commands

### `overcode launch`

Launch a new Claude Code agent in tmux.

```bash
overcode launch --name <name> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | **Required.** Name for the agent (becomes tmux window name) |
| `--directory` | `-d` | Working directory (defaults to current directory) |
| `--prompt` | `-p` | Initial prompt to send to the agent |
| `--skip-permissions` | | Auto-deny permission prompts |
| `--bypass-permissions` | | Bypass all permission checks (dangerous) |
| `--parent` | | Name of parent agent (auto-detected if launched from within an agent) |
| `--no-parent` | | Launch top-level instead of as the calling agent's child. Only the overagent may use it from inside an agent, for agents you will drive yourself (a guard against accidents, not a sandbox) |
| `--follow` | `-f` | Stream child output, block until report or timeout |
| `--on-stuck` | | Policy when child stops without reporting: `wait` (default), `fail`, `timeout:DURATION` |
| `--oversight-timeout` | | Shorthand for `--on-stuck timeout:DURATION` (e.g., `5m`, `1h`, `30s`) |
| `--allowed-tools` | | Comma-separated tools to allow (e.g., `Read,Glob,Grep,Edit`). Claude Code only — maps to `--allowedTools`; ignored for opencode, which has no such flag |
| `--backend-arg` | | Extra agent-CLI flag (repeatable). Each value is a space-separated flag+value string. `--claude-arg` is a deprecated alias |
| `--backend` | `-B` | Agent CLI backend: `claude-code` (default), `opencode`, `opencode2`, `codex`, or `grok`. See [Backends](backends.md) |
| `--budget` | `-b` | Cost budget in USD (deducted from parent if parent has budget) |
| `--wrapper` | `-w` | Wrapper script: path or name from `~/.overcode/wrappers/` (e.g., `devcontainer`) |
| `--no-inherit` | | Don't inherit settings from the parent agent (see below) |
| `--skills` | | Skill profile to switch on; `none` for none. Default: the parent's, then the folder's pinned profile. See [Skill Profiles](skill-profiles.md) |
| `--session` | | Tmux session name (default: `agents`) |

**Parent settings inheritance (#433):** when launched from within an agent (or with `--parent`), the child inherits the parent's `provider`, `model`, `wrapper`, agent-teams setting, and permission mode for any of those not given explicitly. Resolution order is: explicit flag > parent setting > `new_agent_defaults` in `~/.overcode/config.yaml` > built-in default. So a parent pinned to Bedrock spawns Bedrock children unless told otherwise. Use `--no-inherit` to skip the parent and resolve from config defaults only.

**Examples:**
```bash
# Basic launch
overcode launch -n my-agent -d ~/project

# With initial prompt
overcode launch -n researcher -d ~/project -p "Analyze the authentication flow"

# Autonomous mode
overcode launch -n builder -d ~/project --bypass-permissions

# Launch as child agent with follow mode
overcode launch -n subtask --parent my-agent --follow -p "Fix the auth bug. When done: overcode report --status success"

# With oversight timeout (fail after 5 minutes without report)
overcode launch -n subtask --follow --oversight-timeout 5m -p "Fix the bug. When done: overcode report --status success"

# Restrict agent to read-only tools
overcode launch -n reviewer -d ~/project --allowed-tools "Read,Glob,Grep" --skip-permissions

# Pass extra agent-CLI flags
overcode launch -n fast -d ~/project --backend-arg "--model haiku" --backend-arg "--effort low"

# Enable Claude-in-Chrome integration (requires the Claude for Chrome extension
# and a claude.ai Pro/Max/Team/Enterprise subscription — NOT supported with
# --provider bedrock as of 2026-04; see anthropics/claude-code#16128)
overcode launch -n web -d ~/project --backend-arg --chrome

# Launch with a cost budget (auto-deducted from parent if parent has budget)
overcode launch -n task --budget 2.00 -p "Fix the bug. When done: overcode report --status success"

# Launch inside a devcontainer (auto-installs wrapper on first use)
overcode launch -n builder -d ~/project --wrapper devcontainer --bypass-permissions
```

### `overcode list`

List all running agents with status and statistics.

```bash
overcode list [name] [--show-done] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `name` | Optional. Show only this agent and its descendants |
| `--show-done` | Include "done" child agents |

Output shows: agent name, uptime, green/idle time, interactions, tokens, and current activity. In tree mode, children are indented under their parent.

### `overcode attach`

Attach to the tmux session containing agents.

```bash
overcode attach [--session <session>]
```

Use `Ctrl+b d` to detach, or `Ctrl+b n/p` to switch windows.

### `overcode kill`

Kill a running agent.

```bash
overcode kill <agent-name> [--no-cascade] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `--no-cascade` | Only kill this agent, orphan its children instead of killing them |

By default, killing a parent also kills all its descendants (deepest-first).

### `overcode restart`

Restart an agent with the same launch configuration, resuming its conversation.

```bash
overcode restart <agent-name> [--fresh] [--model <model>] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `--fresh` | Start a new conversation instead of resuming |
| `--model`, `-m` | Relaunch on this model (kept for later restarts) |

If the agent's tmux window is gone (it was killed, or the machine rebooted),
`restart` revives it in a new window instead.

### `overcode revive`

Bring back agents whose tmux window is gone, for example after a reboot.

```bash
overcode revive <agent-name> [--fresh] [--dry-run]
overcode revive --all [--all-sessions] [--fresh] [--dry-run]
```

| Option | Description |
|--------|-------------|
| `--all`, `-a` | Revive every dead agent, parents before children |
| `--all-sessions` | With `--all`: every overcode tmux session, not just this one |
| `--fresh` | Start new conversations instead of resuming |
| `--dry-run`, `-n` | List what would be revived, change nothing |

Each agent is relaunched in a new window with its full launch context. It
resumes its previous conversation when one is recorded and its backend can
resume. Otherwise it starts fresh, and the output gives the reason. Done
children and children that filed a report are skipped. The monitor daemon is
started if it isn't running. In the TUI, the command palette has the same
action: **Revive all dead agents**.

### `overcode shutdown`

Stop everything overcode is running.

```bash
overcode shutdown [--dry-run] [--yes] [--force] [--keep-jobs] [--all] [--timeout <s>]
```

| Option | Description |
|--------|-------------|
| `--dry-run`, `-n` | List what would be stopped, change nothing |
| `--yes`, `-y` | Skip the confirmation (the plan is printed first otherwise) |
| `--force` | Kill agent windows without a graceful exit |
| `--keep-jobs` | Leave the `jobs` tmux session running |
| `--all`, `--all-sessions` | Every overcode tmux session, not just this one |
| `--timeout` | Seconds to wait for agents to exit before killing their windows (default 10) |

Steps run in this order:

1. The supervisor daemon.
2. Agents, children first. Each gets its backend's exit keys, then its window is killed.
3. The `jobs` tmux session.
4. The monitor daemon, the API server, the presence logger, and any legacy PID files.
5. overcode's tmux sessions: the agents session, `oc-view-*`, the `overcode` split layout and the supervisor controller.

Agent records are kept, so `overcode revive --all` brings the agents back
later with their conversations. The shared pieces (the split and controller
sessions, the presence logger) are left alone while another overcode tmux
session is still running.

The tmux session you run the command from is killed last. An agent that runs
`shutdown` itself is not exited, and its window closes with that session.

In the TUI, the command palette has **Shut down overcode**. Run it twice to
confirm, and the TUI exits at the end. An API server with
`web.allow_control: true` also takes `POST /api/shutdown`. Its default body
stops only the supervisor, the monitor and the API server, and agents keep
running. `{"scope": "all"}` does the full shutdown, and `"dry_run": true`
returns the plan instead.

### `overcode rename`

Rename an agent, keeping its conversation, status history and costs.

```bash
overcode rename <agent-name> <new-name> [--force] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `--force` | Rename even if the agent is busy (its current turn is cancelled) |

A live agent is stopped and resumed under the new name, in the same window
with the same conversation. With its next prompt it is told it was renamed.

The old name keeps working as an alias. `send`, `follow`, `kill`, `show` and
the other name-taking commands still reach the agent and print
`note: agent '<old>' was renamed to '<new>'` on stderr. A parent following a
child through a rename keeps following it. An alias is dropped when a new
agent is launched with that name.

An agent that is busy (working, or showing a permission dialog) is not
renamed: stopping it would cancel its turn and kill whatever command it was
running, and the resumed agent would not carry on by itself. Wait until it
is idle, or pass `--force`. A terminated agent just has its record renamed,
and `restart`/`revive` bring it back under the new name.

### `overcode cleanup`

Remove terminated sessions from tracking. Sessions whose tmux windows no longer exist are marked terminated; this command removes them from the session list.

```bash
overcode cleanup [--done] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `--done` | Also archive "done" child agents (kill tmux window, remove from tracking) |

### `overcode report`

Report completion from within a child agent session. Called by the child agent (not the parent) to signal that it finished its work.

```bash
overcode report --status <success|failure> [--reason <text>]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--status` | `-s` | **Required.** `success` or `failure` |
| `--reason` | `-r` | Optional explanation |

The command reads `OVERCODE_SESSION_NAME` and `OVERCODE_TMUX_SESSION` from the environment (automatically set for all agents launched by overcode).

Without a report, child agents that stop enter `waiting_oversight` status instead of `done`. The parent's `--follow` blocks until a report arrives (or the oversight policy triggers).

### `overcode follow`

Follow an already-running agent's output. Streams pane content to stdout and blocks until the agent reports completion (or the oversight policy triggers).

```bash
overcode follow <agent-name> [--session <session>]
```

Exit codes:
- `0` — child reported success
- `1` — child reported failure or terminated
- `2` — oversight timeout expired
- `130` — interrupted (Ctrl-C)

### `overcode budget`

Manage agent cost budgets.

```bash
overcode budget set <name> <amount>              # Set budget
overcode budget transfer <source> <target> <amount>  # Transfer between agents
overcode budget show [name]                      # Show budget status
```

The `transfer` command requires the source to be an ancestor of the target.

### `overcode send`

Send input to an agent.

```bash
overcode send <agent-name> <text> [options]
```

| Option | Description |
|--------|-------------|
| `--no-enter` | Don't press Enter after the text |
| `--session` | Tmux session name |

**Special keys:** `enter`, `escape`, `tab`, `up`, `down`, `left`, `right`

```bash
# Send a command
overcode send my-agent "Fix the bug in auth.py"

# Send without Enter
overcode send my-agent "y" --no-enter

# Send special key
overcode send my-agent escape
```

### `overcode show`

Show recent output from an agent.

```bash
overcode show <agent-name> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--lines` | `-n` | Number of lines to show (default: 50) |
| `--session` | | Tmux session name |

### `overcode set-value`

Set agent priority value for sorting.

```bash
overcode set-value <agent-name> <value> [--session <session>]
```

Default value is 1000. Higher values = higher priority (shown first when sorted by value).

### `overcode instruct`

Set or manage standing instructions for an agent.

```bash
overcode instruct <agent-name> <preset-or-text> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--clear` | `-c` | Clear standing instructions |
| `--list` | `-l` | List available presets |
| `--session` | | Tmux session name |

**Built-in presets:**
- `DO_NOTHING` - Supervisor ignores this agent
- `STANDARD` - General-purpose safe automation
- `PERMISSIVE` - Trusted agent, minimal friction
- `CAUTIOUS` - Sensitive project, careful oversight
- `RESEARCH` - Information gathering, exploration
- `CODING` - Active development work
- `TESTING` - Running and fixing tests
- `REVIEW` - Code review, analysis only
- `DEPLOY` - Deployment and release tasks
- `AUTONOMOUS` - Fully autonomous operation
- `MINIMAL` - Just keep it from stalling

```bash
# Use a preset
overcode instruct my-agent CODING

# Custom instructions
overcode instruct my-agent "Focus on performance. Avoid changing the API."

# Clear instructions
overcode instruct my-agent --clear
```

---

## Monitoring Commands

### `overcode tmux`

Open the tmux split layout: dashboard on top, agent terminal on bottom. This is the recommended way to use overcode.

```bash
overcode tmux [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--ratio` | `-r` | Percentage of height for the dashboard pane (default: 25) |
| `--uninstall` | | Remove keybindings, kill split window and linked sessions |
| `--yes` | `-y` | Skip the first-run confirmation prompt |
| `--session` | | Tmux session name (default: `agents`) |

Running `overcode tmux` again re-attaches to the existing layout (restarts the dashboard with latest code).

**Split layout keybindings** (scoped to the split window only):

| Key | Action |
|-----|--------|
| Toggle key | Toggle focus between dashboard and terminal pane (default: `Tab`, configurable via `tmux.toggle_key` in config.yaml) |
| `Option+J/K` | Navigate agents from the terminal pane |
| `PageUp/Down` | Enter scrollback in the terminal pane |
| `WheelUp/Down` | Mouse scroll in the terminal pane |

For agents that draw inline (Claude Code) scrolling enters tmux copy mode on the agent's real scrollback. For full-screen agents (opencode) the same gestures are passed to the program, which scrolls its own transcript.

**Uninstall:**
```bash
overcode tmux --uninstall
```
Removes keybindings, kills the split window and linked sessions. Global tmux options (`focus-events`, `terminal-features`) are left in place (see output for manual removal commands).

### `overcode monitor`

Launch the standalone TUI dashboard (no tmux split).

```bash
overcode monitor [options]
```

| Option | Description |
|--------|-------------|
| `--diagnostics` | Disable auto-refresh timers (for debugging) |
| `--session` | Tmux session name |

### `overcode supervisor`

Launch the TUI with the embedded supervisor daemon.

```bash
overcode supervisor [options]
```

| Option | Description |
|--------|-------------|
| `--restart` | Restart if already running |
| `--session` | Tmux session name |

### `overcode web`

Start or stop the API server (non-blocking). It serves the sister API only;
there is no web UI.

```bash
overcode web [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--host` | `-h` | Host to bind (default: `127.0.0.1`) |
| `--port` | `-p` | Port (default: `8080`) |
| `--stop` | | Stop the running API server |
| `--session` | | Tmux session name |

Starts the server in the background and exits immediately. If already running, shows the current URL. Use `--stop` to stop a running server. Same toggle as the TUI `w` key.

Routes: `GET /api/status`, `GET /api/agents/{name}/status`,
`GET /api/timeline/raw`, `GET /health`, and the control routes under
`/api/agents/...`, `/api/daemon/...` and `POST /api/shutdown` (control needs
`web.allow_control: true`). See
[Advanced Features: API Server](advanced-features.md#api-server).

```bash
overcode web                          # Start on localhost:8080
overcode web --port 3000              # Custom port
overcode web --host 0.0.0.0           # LAN access (needs api_key)
overcode web --stop                   # Stop the server
```

### `overcode export`

Export session data to Parquet format for analysis.

```bash
overcode export <output-file.parquet> [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--archived` | `-a` | Include archived sessions (default: true) |
| `--timeline` | `-t` | Include timeline data (default: true) |
| `--presence` | `-p` | Include presence data (default: true) |

### `overcode history`

Show archived session history.

```bash
overcode history [agent-name]
```

Omit agent name to see all archived sessions.

---

## Daemon Commands

### Monitor Daemon

The monitor daemon tracks agent status, accumulates time metrics, and syncs Claude Code stats.

```bash
# Start the daemon
overcode monitor-daemon start [--interval <seconds>] [--session <session>]

# Stop the daemon
overcode monitor-daemon stop [--session <session>]

# Check status
overcode monitor-daemon status [--session <session>]

# Watch logs
overcode monitor-daemon watch [--session <session>]
```

Default polling interval is 10 seconds.

### Supervisor Daemon

The supervisor daemon provides Claude-powered orchestration. It launches a "daemon claude" when agents need attention.

```bash
# Start the daemon (requires monitor daemon running)
overcode supervisor-daemon start [--interval <seconds>] [--session <session>]

# Stop the daemon
overcode supervisor-daemon stop [--session <session>]

# Check status
overcode supervisor-daemon status [--session <session>]

# Watch logs
overcode supervisor-daemon watch [--session <session>]
```

---

## Skill Profile Commands

See [Skill Profiles](skill-profiles.md) for the whole picture.

| Command | What it does |
|---------|--------------|
| `overcode skills list` | The library, each CLI's always-on skills, profiles and pinned folders |
| `overcode skills profile set <name> <skill>...` | Create a profile or replace its skills |
| `overcode skills profile add\|remove <name> <skill>...` | Change a profile's skills |
| `overcode skills profile show <name>` | What a profile adds and hides for each CLI |
| `overcode skills profile list` / `delete <name>` | List or delete profiles |
| `overcode skills profile emoji <name> [emoji]` | The emoji the PRF column shows for a profile; no emoji goes back to the default 🎒 |
| `overcode skills pin <profile> [dir]` / `unpin [dir]` | New agents in a folder (and below) get this profile |
| `overcode skills library add\|remove <path>` | Read library skills from another folder too |
| `overcode skills adopt <skill>...` | Move always-on personal skills into the library |

```bash
overcode skills library add ~/Code/team-skills
overcode skills profile set research shirka dataviz
overcode skills pin research ~/Code/papers
overcode launch -n reader -d ~/Code/papers        # gets 'research'
```

## Wrapper Commands

### `overcode wrappers list`

List installed and available wrappers.

```bash
overcode wrappers list
```

Shows wrappers in `~/.overcode/wrappers/`, indicates whether each is bundled or custom, and flags modified bundled wrappers. Also lists bundled wrappers that haven't been installed yet (these auto-install on first use).

### `overcode wrappers install`

Install or update all bundled wrappers.

```bash
overcode wrappers install
```

Copies bundled wrapper scripts to `~/.overcode/wrappers/`. Reports which were installed, updated, or unchanged.

### `overcode wrappers reset`

Reset wrapper(s) to the bundled version, undoing any local modifications.

```bash
overcode wrappers reset              # Reset all bundled wrappers
overcode wrappers reset devcontainer # Reset only devcontainer
```

---

## Configuration Commands

### `overcode config init`

Create a config file with documented defaults.

```bash
overcode config init [--force]
```

Creates `~/.overcode/config.yaml`. Use `--force` to overwrite existing.

### `overcode config show`

Display current configuration.

```bash
overcode config show
```

### `overcode config path`

Show the config file path.

```bash
overcode config path
```

---

## Key Commands

### `overcode keys`

Show the TUI's effective keys, grouped like the command palette, with where each comes from: `default` (in code), the preset's name, or `override` (your config). See [Configuration → Keybindings](configuration.md#keybindings).

```bash
overcode keys                        # every scope
overcode keys --scope command_palette
overcode keys --preset vscode        # preview a preset (with your overrides)
overcode keys --conflicts            # warnings only: unknown actions, duplicate keys, swallowed keys, shadowed passthru keys
overcode keys --use vscode           # switch preset (writes keys.preset to config.yaml)
overcode keys --presets              # list presets
overcode keys --all --json           # include palette commands with no key; machine-readable
```

## Activity Commands

What the [usage log](configuration.md#usage-log) has recorded about how you
use overcode (#483).

### `overcode activity summary`

Most-used actions and how you reach them (key, palette, click), plus
experimental signals: keys you press that do nothing, palette picks of
actions that have a key, long j/k walks, toggles undone within 3 s, dialog
cancels, palette searches that found nothing.

```bash
overcode activity summary              # last 7 days
overcode activity summary --since all
overcode activity summary --json       # for scripts and agents
```

### `overcode activity keys`

The raw records as JSON lines, newest last.

```bash
overcode activity keys --since 30m --kinds key,action
```

### `overcode activity stream`

Follow the log live as JSON lines, for an agent's Monitor tool. `--detail
rollup` (default) prints one digest per `--rollup-interval` (15m) with a
one-sentence `description`, and nothing while idle. `significant` adds
actions, cancelled dialogs, tips and CLI calls as they happen. `verbose`
adds every key and click.

### `overcode activity path`

Where the usage log lives.

### `overcode journey`

Your learning journey: tracks, what's next, the hard way, and every
capability's level (the TUI's `u` panel as text). `--json` for scripts and
the overagent.

---

## View Commands

Change what the running TUI shows, from a script or an agent (#484). Each
command is queued for the TUI, applied through the same code as its own keys
and dialogs, and acknowledged: the result or the error is printed, and the
exit code is 0 (applied), 1 (refused) or 2 (no TUI running). `--json` prints
the raw acknowledgement.

```bash
overcode view state                          # focused agent, level, columns, sort, filters, dialog
overcode view columns list [--level med]     # every column, its description, shown or not
overcode view columns hide cost joules       # ids, names or headers; current level unless --level
overcode view columns show branch --level med
overcode view columns reset                  # back to the level's defaults
overcode view sort cost --desc               # any sortable column, or: tree
overcode view detail high                    # low, med, high, full
overcode view filter backend | --clear       # by tag
overcode view focus my-agent
overcode view actions                        # every palette action id, with keys
overcode view toggle toggle_timeline         # run a view palette action by id
overcode view point jump_to_attention        # show the user its key
overcode view notify "Restarted 3 agents"
```

`overcode docs path` prints where these docs are. They ship inside the
package, so they match the installed version and need no network.
`overcode docs code` prints where overcode's installed source is. The
overagent reads both.

An unknown name fails the whole command and says what would work (`unknown
column 'brnch' — did you mean branch?`). Commands from an agent overcode
launched are logged as the agent's, never as your own use.

---

## Model Metadata Commands

The catalog behind the `CTX%` and `$` columns — see
[Configuration → Model metadata](configuration.md#model-metadata-context-windows-and-pricing).

### `overcode models info`

Show which catalog lookups resolve against (local refreshed cache,
opencode's cache, or the bundled snapshot), its vintage and size.

```bash
overcode models info
```

### `overcode models lookup`

Show what overcode resolves for a model id — context window, list price,
short name — and which tier (curated table, catalog, or unknown) answered.

```bash
overcode models lookup zai/glm-4.6
overcode models lookup openai/gpt-5.6-sol
```

### `overcode models refresh`

Fetch models.dev and rewrite `~/.overcode/cache/model_metadata.json`. This is
the on-demand path; nothing is fetched unattended unless `config.yaml` sets
`model_metadata.auto_refresh: true`.

```bash
overcode models refresh          # fetch and rewrite
overcode models refresh --check  # report the active catalog's age only
```

---

## Global Options

Most commands accept `--session <name>` to specify a tmux session other than the default `agents`.

This allows managing multiple independent sets of agents:

```bash
# Team A agents
overcode launch -n task1 -d ~/project --session team-a
overcode monitor --session team-a

# Team B agents
overcode launch -n task1 -d ~/other --session team-b
overcode monitor --session team-b
```
