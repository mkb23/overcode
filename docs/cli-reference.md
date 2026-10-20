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
| `--normal-permissions` | | Run in normal permission mode even under a bypass or permissive parent; every other setting is still inherited. Can't be combined with `--bypass-permissions` or `--skip-permissions` |
| `--parent` | | Name of parent agent (auto-detected if launched from within an agent) |
| `--no-parent` | | Launch top-level instead of as the calling agent's child. Only the overagent may use it from inside an agent, for agents you will drive yourself (a guard against accidents, not a sandbox) |
| `--follow` | `-f` | Stream child output, block until report or timeout |
| `--on-stuck` | | Policy when child stops without reporting: `wait` (default), `fail` (a follower marks it done and exits 1 at once), `timeout:DURATION` (the daemon marks it done with ⌛ after DURATION) |
| `--oversight-timeout` | | Shorthand for `--on-stuck timeout:DURATION` (e.g., `5m`, `1h`, `30s`) |
| `--preset` | | Children preset: `supervised` (the default for children), `balanced`, `autonomous`, or one of yours from config.yaml. Sets the oversight policy, archive delay and a default budget; explicit flags override it. An unknown name refuses to launch (exit 1) and lists the valid names. See [Child Agents: Presets](children.md#presets) |
| `--archive-after` | | How long a done child keeps its window before the daemon closes it: a duration (`10m`, `24h`) or `never`. Default `1h` (or the preset's) |
| `--allowed-tools` | | Comma-separated tools to allow (e.g., `Read,Glob,Grep,Edit`). Claude Code only — maps to `--allowedTools`; ignored for opencode, which has no such flag |
| `--backend-arg` | | Extra agent-CLI flag (repeatable). Each value is a space-separated flag+value string. `--claude-arg` is a deprecated alias |
| `--backend` | `-B` | Agent CLI backend: `claude-code` (default), `opencode`, `opencode2`, `codex`, or `grok`. See [Backends](backends.md) |
| `--budget` | `-b` | Cost budget in USD (deducted from parent if parent has budget) |
| `--wrapper` | `-w` | Wrapper script: path or name from `~/.overcode/wrappers/` (e.g., `devcontainer`) |
| `--no-inherit` | | Don't inherit settings from the parent agent (see below) |
| `--skills` | | Skill profile to switch on; `none` for none. Default: the parent's, then the folder's pinned profile. See [Skill Profiles](skill-profiles.md) |
| `--billing-code` | | Bill this agent's spend to this code (default: its name). A child always takes its parent's. See [Billing Codes](configuration.md#billing-codes) |
| `--force-name` | | From inside an agent: launch with a name the naming policy refuses. Recorded on the agent (`name_forced`). Never gets past a taken name or overcode's name pattern |
| `--json` | | Print the launched agent (`id`, `name`, `name_source`, `parent_session_id`) as JSON, or a refused name as a structured error; everything else goes to stderr |
| `--session` | | Tmux session name (default: `agents`) |

**Naming policy (#542):** run from inside an agent (`OVERCODE_SESSION_ID`
set), `launch` and `fork` check `-n` against the naming policy before
launching: overcode's name pattern and names already taken (a live agent's
name or old name) always, plus any `naming.rules` you set (see
[Naming](configuration.md#naming)). A refused name exits **3**, with the
rule it broke, the naming advice and a suggested name on stderr, for
example `name 'child-1' breaks the naming policy (forbidden word 'child');
try '...', or pass --force-name`. A name from a human shell is never
refused (a warning only, with `naming.rules.warn_humans`). A name given by a
parent is never auto-renamed, and neither is one a human chose.

**Parent settings inheritance (#433):** when launched from within an agent (or with `--parent`), the child inherits the parent's `provider`, `model`, `wrapper`, agent-teams setting, and permission mode for any of those not given explicitly. Resolution order is: explicit flag > parent setting > `new_agent_defaults` in `~/.overcode/config.yaml` > built-in default. So a parent pinned to Bedrock spawns Bedrock children unless told otherwise. Use `--no-inherit` to skip the parent and resolve from config defaults only. Permission mode is inherited too: a child of a bypass parent launched without any permission flag runs in bypass. Use `--normal-permissions` for a normal-mode child that keeps the rest of the parent's settings (`--no-inherit` also gives normal mode, but drops everything else too). A child's billing code always comes from its parent, even with `--no-inherit`.

**Children presets:** a child launched without `--preset` gets the default preset (`supervised` unless `children.preset` in config.yaml says otherwise), which is overcode's behaviour before presets. A top-level agent gets one only when named. `overcode config presets` lists them.

| Preset | Stops without a report | Done child's window closes |
|--------|------------------------|----------------------------|
| `supervised` | waits for ever | 1 h after done |
| `balanced` | marked ⌛ after 24 h | 1 h after done |
| `autonomous` | marked ⌛ after 1 h | 10 min after done |

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

# Unattended child: 1 h oversight timeout, window closed 10 min after done
overcode launch -n nightly-deps --preset autonomous -p "Bump the deps. When done: overcode report --status success"

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
overcode list [name | --self] [--show-done] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `name` | Optional. Show only this agent and its descendants |
| `--self` | From inside an agent: show it and its descendants, found by `OVERCODE_SESSION_ID` (so it still works after the agent is renamed) |
| `--show-done` | Include "done" child agents |
| `--json` | One JSON object per agent: `id`, `name`, `name_source` (`default`, `parent`, `auto` or `human`), `name_forced`, `previous_names`, `status`, `activity`, `parent_session_id`, `repo`, `branch`, `backend`, `model`, `is_remote` |

Output shows: agent name, uptime, green/idle time, interactions, tokens, and current activity. In tree mode, children are indented under their parent. A name overcode chose (auto-naming) is shown as `~name`.

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
`web.allow_control: true` and a `web.api_key` also takes `POST /api/shutdown`. Its default body
stops only the supervisor, the monitor and the API server, and agents keep
running. `{"scope": "all"}` does the full shutdown, and `"dry_run": true`
returns the plan instead.

### `overcode rename`

Rename an agent, keeping its conversation, status history and costs.

```bash
overcode rename <agent-name> <new-name> [--force] [--session <session>]
overcode rename <agent-name> --auto | --lock
```

| Option | Description |
|--------|-------------|
| `--force` | Agents launched before 0.6 only: rename even if busy (its turn is cancelled). Ignored otherwise |
| `--auto` | Hand the name to overcode's auto-naming; the name stays as it is until the namer changes it |
| `--lock` | Keep the current name: overcode never renames it |

A rename locks the name (its source becomes `human`): auto-naming never
changes a name a person chose. `--auto` undoes that.

The agent keeps running, even mid-turn: its tmux window and its record take
the new name, and with its next prompt it is told it was renamed. Its
environment keeps the name it was launched under (`OVERCODE_SESSION_NAME`);
overcode finds a running agent by `OVERCODE_SESSION_ID`, so nothing reads
that name.

The old name keeps working as an alias. `send`, `follow`, `kill`, `show` and
the other name-taking commands still reach the agent and print
`note: agent '<old>' was renamed to '<new>'` on stderr. A parent following a
child through a rename keeps following it. An alias is dropped when a new
agent is launched with that name.

A terminated agent just has its record renamed, and `restart`/`revive`
bring it back under the new name.

An agent launched before 0.6 finds itself by its launch name, so it is
stopped and resumed under the new name instead, in the same window with the
same conversation. While it is busy (working, or showing a permission
dialog) that would cancel its turn, so it is refused unless `--force`.

### `overcode name`

The naming policy (#542; see [Naming](configuration.md#naming)).

```bash
overcode name policy [--directory <dir>] [--json]
overcode name check <name> [--directory <dir>] [--json]
overcode name suggest <agent> [--advice-file <file>] [--json]
overcode name lab [--archived <n>] [--advice-file <a.md>] [--vs <b.md>] [--csv <out.csv>] [--json]
```

`name policy` prints the naming advice first (the policy proper: free text
a model reads, from `naming.advice` and the repo's `.overcode/naming.md`),
then the checks that always apply and the optional `naming.rules` that are
set. A parent agent reads it before naming its children.

`name check` runs the check `launch -n` runs from inside an agent, without
launching: exit 0 when the name passes, 3 with the failed rules, the advice
and a suggested name when it does not. `--json` prints
`{name, ok, hard, failures: [{rule, message, hard}], suggestion, advice}`;
`hard` failures (`base_pattern`, `reserved`, `taken`) are the ones
`--force-name` cannot get past. `--directory` picks the repo (for its
advice file and `no_repo_prefix`), default the current directory.

`name suggest` asks the auto-namer's model for one agent's name, with the
inputs and prompt the engine would use, and renames nothing: it prints the
candidates, overcode's pick (the first that passes the rules and is not
taken) and the model's reason. `--advice-file` tries other advice in place
of the configured one. `--json` prints `{agent, id, name_source, advice,
candidates, keep, why, pick, error, model, tokens_in, tokens_out,
cost_usd}`. It works with `naming.enabled` off; the call is billed to the
`overcode` billing code. Exit 1 when there is no model or no usable reply.

`name lab` is the naming lab without the TUI: it runs advice over every
live local agent, or the last `--archived` N archived ones (their brief,
prompts and edited files come from the day bundles; they are checked
against each other, as a fleet), and renames nothing. `--advice-file`
is the advice to try (default: the saved advice); `--vs` runs a second
file as B. A repo's `.overcode/naming.md` is still added after the advice,
as the namer does. It prints one row per agent (name, source, pick A, pick
B, the candidates passed over and why; a pick two agents share is marked
`collides`), then per draft the names changed, collisions and mean length.
`--csv` also writes `agent,name_source,pick_a,pick_b,rejected`; `--json`
prints `{rows, summary, calls, cost_usd}`. One call per agent and advice,
in parallel, billed to `overcode` and counted against
`naming.cost_cap_per_day` (a warning when the cap stops calls). Exit 1
with no model.

### `overcode cleanup`

Remove terminated sessions from tracking. Sessions whose tmux windows no longer exist are marked terminated; this command removes them from the session list.

```bash
overcode cleanup [--done] [--session <session>]
```

| Option | Description |
|--------|-------------|
| `--done` | Also archive "done" child agents now (kill tmux window, remove from tracking). Without it the monitor daemon closes a done child's window after its archive delay (`--archive-after`, 1 h by default) |

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

Without a report, child agents that stop enter `waiting_oversight` status instead of `done`. The parent's `--follow` blocks until a report arrives (or the oversight policy triggers). See [Child Agents](children.md).

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

### `overcode rebill`

Bill an agent's spend to another code from now on. Forward only: what it
spent so far stays on its old code. The code is a name (a code's current
or previous name); an unknown name creates a code.

```bash
overcode rebill <name> <code>
```

### `overcode billing`

Spend per billing code, from the ledger (all time by default). Grouped by
code id and shown by each code's current name.

```bash
overcode billing                 # all time
overcode billing --since 30d     # or 2w, 12h, 2026-09, 2026-09-15
overcode billing --month 2026-09
overcode billing --json          # codes keyed by id, each with its "name"
overcode billing --ids           # add each code's id
```

| Subcommand | Does |
|------------|------|
| `overcode billing rename OLD NEW` | Renames a code (by name, previous name or id). Every report, past spend included, shows the new name; the ledger is not touched. Refuses a name another code has. |
| `overcode billing merge A B` | Folds code A into B: A's rows report under B, A's names resolve to B, A's agents bill to B. Recorded in `ledger/codes.json`, no row rewritten. |
| `overcode billing codes` | Lists codes: name, id, source (`default`/`human`), live agents, agents and spend, previous names (`--json`). |

Claude background sessions billed to a code (🌙 rows) are counted among its
agents and, when there are any, in a `background` column (`"background"` in
`--json`). See [Billing Codes](configuration.md#billing-codes).

On a Claude subscription (Pro, Max, Team seats) a `quota wk` column shows
each code's estimated share of the **weekly quota this week**, in percentage
points (`1.37%`), and an `unattributed` row holds the rest of the weekly
figure the account shows (claude.ai chat, Cowork, Claude Code outside
overcode). With `--since` / `--month` the column is `quota`: the period's
points. It is an estimate from tokens (`overcode usage model`); `--json`
carries `quota_pp` per code and a `quota` block. API-key and Bedrock users
see no quota column.

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

Open the tmux split layout: dashboard on top, agent terminal on bottom. This is the dashboard — `overcode` with no command and `overcode monitor` open the same layout. Inside tmux it switches your client to it; from a plain terminal it attaches.

```bash
overcode tmux [options]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--ratio` | `-r` | Percentage of height for the dashboard pane (default: 25) |
| `--restart` | | Restart the monitor daemon and API server on launch |
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

The same as `overcode tmux`: opens the split layout. Its flags are passed to the dashboard pane. (Before 0.6.0 this ran a standalone dashboard without the split; that mode is gone.)

```bash
overcode monitor [options]
```

| Option | Description |
|--------|-------------|
| `--jobs` | Start the dashboard in jobs view |
| `--restart` | Restart the monitor daemon and API server first |
| `--diagnostics` | Disable auto-refresh timers (for debugging) |
| `--session` | Tmux session name |

`overcode supervisor`, which opened a pre-0.6.0 layout with the standalone dashboard and a Claude pane, now prints that it was removed and opens `overcode tmux`. Press `e` in the dashboard for an agent that drives overcode (the overagent); the supervisor daemon is `overcode supervisor-daemon`.

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
`web.allow_control: true` and `web.api_key`). See
[Advanced Features: API Server](advanced-features.md#api-server).

```bash
overcode web                          # Start on localhost:8080
overcode web --port 3000              # Custom port
overcode web --host 0.0.0.0           # LAN access (refused without web.api_key)
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
overcode history                     # agents archived in the last 30 days
overcode history --since 2026-09     # or 2w, 12h, 2026-09-15; --all for every one
overcode history <name-or-id>        # the newest archived agent of that name (or former name, or id)
overcode history <name> --files      # and whether its own files are kept (in a day bundle)
```

`overcode show <name>` falls back to the archive for an agent no longer live.

### `overcode usage`

Claude subscription usage: the 5-hour and weekly windows, straight from the
usage API.

```bash
overcode usage                      # 5h and 7d percentages, reset times
overcode usage model                # the quota model behind the Q% column
overcode usage model --json
overcode usage model --refit        # fit now on the kept history (the engine refits hourly)
overcode usage calibration          # show whether statusline calibration is on
overcode usage calibration on       # opt in (agents launched from now on)
overcode usage calibration off
```

Every source reports the weekly window in whole percents, so the per-agent
figure (`Q%`, `1.37%`) is predicted from tokens: each agent's tokens since
the weekly reset, priced at Anthropic list prices per token type, times a
weight per type fitted to the observed weekly percentages. `usage model`
prints the fitted weights against the price-ratio prior (cache reads start
at zero), their uncertainty, the implied weekly budget, the fit's quality
(observations, statusline ticks, windows, RMSE, how many polls the model
put in the right whole-percent cell), the 5-hour ratio, and this week's
observed figure against what overcode's agents are predicted to have used.

`calibration on` adds `overcode statusline` to the `--settings` of Claude
agents overcode launches: it records each response's rate-limit figures
(the exact moment the weekly figure ticks over) for the fit, then prints
your own statusline if you configured one. Off by default; off, nothing is
added. See [Subscription quota](configuration.md#subscription-quota).

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

### `overcode config presets`

List the children presets in effect (built-in and from config.yaml), mark the default, and print any warnings about the `children` section.

```bash
overcode config presets
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
opencode's cache, or the bundled snapshot), its vintage and size; and which
Claude price table prices come from (the one shipped with overcode, or a
local override from `models refresh --prices`), its as-of date, source and
age.

```bash
overcode models info
```

### `overcode models lookup`

Show what overcode resolves for a model id — context window, list price,
short name — and which tier (the price table's row with its date, a curated
family row, the catalog, or unknown) answered. A Claude model also shows
its long-context tier and Bedrock's regional premium.

```bash
overcode models lookup claude-haiku-5-5
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

Offline it fails within about 10 seconds and changes nothing.

With `--prices FILE` it instead rebuilds the Claude price table from an AWS
Price List file (the `AmazonBedrockFoundationModels` offer, JSON or `.gz`)
and touches no network: for hosts that cannot upgrade overcode to get new
prices, such as air-gapped ones. It writes
`~/.overcode/cache/claude_prices.json`, used ahead of the shipped table
while it is dated after it; a file older than the shipped table is refused.
Where to get the file and how lookups order:
[Configuration → Prices on an air-gapped host](configuration.md#prices-on-an-air-gapped-host).

```bash
overcode models refresh --prices AmazonBedrockFoundationModels.json --check  # show the diff only
overcode models refresh --prices AmazonBedrockFoundationModels.json
```

---

## Global Options

Most commands accept `--session <name>` to specify a tmux session other than the default `agents`.

This allows managing multiple independent sets of agents:

```bash
# Team A agents
overcode launch -n task1 -d ~/project --session team-a
overcode tmux --session team-a

# Team B agents
overcode launch -n task1 -d ~/other --session team-b
overcode tmux --session team-b
```
