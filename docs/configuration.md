# Configuration

Overcode can be customized through a config file, environment variables, and per-agent presets.

## Config File

Create a config file with defaults:

```bash
overcode config init
```

This creates `~/.overcode/config.yaml`. View current settings with:

```bash
overcode config show
```

### Complete Configuration Reference

```yaml
# Default standing instructions for new agents
default_standing_instructions: "Be concise. Ask before making large changes."

# AI Summarizer settings
# Generates activity summaries shown in the TUI
summarizer:
  api_url: https://api.openai.com/v1/chat/completions
  model: gpt-4o-mini
  api_key_var: OPENAI_API_KEY  # Name of env var containing API key

# Display hostname (shown in Host column and API response)
# Defaults to system hostname if omitted
hostname: "mac-studio"

# Token pricing for cost calculations
# Defaults match Claude Sonnet 3.5
pricing:
  input: 3.0           # $/million tokens for input
  output: 15.0         # $/million tokens for output
  cache_write: 3.75    # $/million tokens for cache writes (5-minute TTL)
  cache_read: 0.30     # $/million tokens for cache reads
  # cache_write_1h: 6.0  # 1-hour TTL cache writes; default twice the input price
  table_max_age_days: 90  # doctor warns when the Claude price table in use is older

# Bedrock agents (see Pricing Configuration below)
bedrock:
  region: us-east-1    # AWS_REGION for Bedrock agents; us-* means us. profiles (1.1x list)
  # routing: global    # the profile prefix in use, when launches don't say
  discount: 0.0        # negotiated fraction off for Bedrock sessions (e.g. 0.30)
  # model_discount:    # optional per-model-family overrides
  #   opus: 0.25

# API server settings (`overcode web` — serves the sister API only)
web:
  # API key for API server authentication (sent as X-API-Key)
  # Required when binding to non-localhost (--host 0.0.0.0) and for allow_control
  api_key: "your-secret-key"
  # Allow sisters to send control actions (send, kill, launch, ...).
  # Needs api_key: without one the control routes answer 403.
  allow_control: false

# Tmux split layout settings
tmux:
  toggle_key: "Tab"  # Key to toggle pane focus: "Tab", "C-]", "C-Space"

# Timeline display settings
timeline:
  hours: 3.0  # Hours of history to show in timeline

# Rotation/retention for agent_status_history.csv and diagnostics/event_loop_timing.csv
# See "History Retention" below
history_retention:
  status_history_max_days: 540    # delete rotated archives older than this (~18 months)
  status_history_rotate_mb: 50    # rotate the active CSV past this size
  event_loop_timing_cap_mb: 100   # hard cap for diagnostics/event_loop_timing.csv
  event_loop_timing_enabled: true # false disables the heartbeat probe entirely

# When terminated agents leave sessions.json for the archive
# See "Session Archive" below
session_archive:
  terminated_grace_seconds: 3600  # 1 hour; negative disables automatic archiving
archive:
  retention_months: 0             # 0 keeps every month (the default)

# Archived agents' own files (day bundles); kept for ever by default
# See "Agent Files and Retention" below
agent_history:
  retention_days: 0               # 0 keeps every day (the default)
  max_bytes: 0                    # 0: no cap (e.g. 1GB)
  keep_hook_events: true

# Spend rows for running agents; see "Billing Codes" below
ledger:
  checkpoint_minutes: 60

# Billing codes the TUI cycles with Space; see "Billing Codes" below
billing:
  codes: []                       # e.g. [client-a, client-b, internal]

# Naming advice, the optional naming rules and auto-naming; see "Naming" below
naming:
  enabled: false                  # the engine names agents (auto-naming)
  advice: ""                      # free text: the naming policy
  rules: {}                       # optional checks on names parents give children

# Children presets for `overcode launch --preset`; see "Children Presets" below
children:
  preset: supervised              # default for children: supervised | balanced | autonomous | yours
  presets: {}                     # change a built-in or add your own

# Claude subscription quota: the Q% column's estimate
# See "Subscription Quota" below
quota:
  enabled: true                   # poll usage in the engine, show Q% for subscribers
  calibration: false              # opt in: statusline samples from launched agents
  # plan: max20x                  # detected from your Claude Code login when unset
  # weekly_usd: 1550              # the prior's API-equivalent $ per week

# Monitor daemon loop interval while nobody is watching
# See "Unattended Low-Power Mode" below
monitor_daemon:
  interval_unattended_seconds: 10

# Work an agent delegates inside its session (subagents, workflows, teammates)
# See "Delegated Work" below
observability:
  finished_row_seconds: 30        # a finished agent's own row stays this long

# Sister instances for cross-machine monitoring
# See docs/advanced-features.md for setup guide
sisters:
  - name: "macbook-pro"
    url: "http://localhost:15337"
  - name: "desktop"
    url: "http://localhost:25337"
    api_key: "secret"  # Only needed for direct LAN access

# Custom emoticons for skills
# Overrides built-in defaults shown in the "Available Skills" (ASK) column
skill_emoji:
  overcode: 🐙           # Default: 🐙 (built-in)
  claude-api: 🔌         # Default: 🔌 (built-in)
  simplify: ✨            # Default: ✨ (built-in)
  shirka: 🔬             # Custom skill example
  my-custom-skill: 🚀    # Add any custom skill with any emoji

# Energy estimate coefficients for the ⚡ column and the 🔥 watts rate
# See "Energy Estimates" below
energy:
  pue: 1.2
  models:
    opus: large
```

## Environment Variables

### Directory Overrides

| Variable | Description | Default |
|----------|-------------|---------|
| `OVERCODE_DIR` | Base data directory: `config.yaml`, `presets.json`, `jobs/`, wrappers, skills, caches | `~/.overcode` |
| `OVERCODE_STATE_DIR` | Sessions root: `sessions.json`, the archive, and one directory per tmux session | `$OVERCODE_DIR/sessions` |
| `OVERCODE_ACTIVITY` | `0` turns the [usage log](#usage-log) off | on |

Before 0.6.0, `OVERCODE_DIR` on its own did not move `sessions.json` or
`jobs.json`: they stayed in `~/.overcode/sessions` and `~/.overcode/jobs`.
With only `OVERCODE_DIR` set, overcode keeps reading them there until
`$OVERCODE_DIR` has its own.

### Claude Command

| Variable | Description | Default |
|----------|-------------|---------|
| `CLAUDE_COMMAND` | Custom claude command | `claude` |

Useful if you have a wrapper script or claude installed in a non-standard location.

### Summarizer

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | API key for OpenAI (default provider) |
| `OVERCODE_SUMMARIZER_API_URL` | Override API endpoint |
| `OVERCODE_SUMMARIZER_MODEL` | Override model name |
| `OVERCODE_SUMMARIZER_API_KEY_VAR` | Env var name containing API key |

The summarizer works with any OpenAI-compatible API. To use a different provider:

```bash
export OVERCODE_SUMMARIZER_API_URL="https://api.anthropic.com/v1/messages"
export OVERCODE_SUMMARIZER_MODEL="claude-3-haiku-20240307"
export OVERCODE_SUMMARIZER_API_KEY_VAR="ANTHROPIC_API_KEY"
```

## New Agent Defaults

Set defaults for new agents in `~/.overcode/config.yaml`:

```yaml
new_agent_defaults:
  bypass_permissions: false   # Use --dangerously-skip-permissions
  agent_teams: false          # Enable CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
  provider: web               # "web" (Claude.ai OAuth) or "bedrock" (AWS)
  wrapper: ""                 # Wrapper script name or path (e.g., "devcontainer")
  backend: claude-code        # claude-code | opencode | opencode2 | codex | grok — see docs/backends.md
  skill_profile: research     # Skill profile when neither parent nor folder sets one — see docs/skill-profiles.md
```

Skill profiles, the skill library and folder pins live under a `skills:` key; see [Skill Profiles](skill-profiles.md).

These apply to agents created via both the CLI (`overcode launch`) and the TUI (`n` key). CLI flags override config defaults, and for child agents the parent's settings take precedence over config defaults (#433): explicit flag > parent setting > config default > built-in default. Use `--no-inherit` to skip the parent.

Setting `wrapper: devcontainer` makes all new agents launch inside a Docker container by default. See the [Wrappers Guide](wrappers.md) for details.

Omitting `backend` (or setting it to `null`) leaves new agents on overcode's
built-in default (`claude-code`) — same effect as not having the key at all.
There are two surfaces for setting it: editing `config.yaml` directly (as
above), or the TUI's `G` new-agent-defaults modal, which has a **Backend**
row that cycles through every registered backend plus `(unset)` (space/enter
to cycle, `a` to apply). There is no `overcode config set` CLI subcommand —
`overcode config` only supports `init`/`show`/`path`/`tmux` — so config.yaml
and the `G` modal are the only two ways to change this default.

## Backend Telemetry

Non-Claude backends install a telemetry footprint at launch (a plugin file,
a hooks file, or per-launch argv) so overcode gets hook-grade status. Turn
one off per-backend in `~/.overcode/config.yaml`:

```yaml
backend_telemetry:
  opencode: off    # skip installing <project>/.opencode/plugins/overcode-telemetry.js
  opencode2: off   # skip installing <project>/.opencode/plugins/overcode-telemetry-v2/ (bundled plugin)
  codex: off       # skip the per-launch -c 'hooks.*=...' argv injection
  grok: off        # skip installing ~/.grok/hooks/overcode.json
```

Default is `on` for every backend, so nothing changes until you opt one out.
`claude-code` is exempt and always on — its hooks ride per-launch
`--settings` flags and leave no on-disk footprint to opt out of. With a
backend's telemetry off, `prepare_launch()` writes nothing and status falls
back to pane polling automatically; `overcode doctor` reports the agent as
`telemetry-disabled` (informational) rather than `missing-settings` (broken).
See [Backends](backends.md) ("Opting out of telemetry") for each backend's
exact footprint and the `overcode hooks uninstall-backend` cleanup commands.

## Energy Estimates

`$` cycles the cost display through tokens, dollars and energy. In energy mode
the ENRG column (⚡) shows each agent's estimated joules, and the burn rate (🔥)
shows average power in watts over the spin-baseline window.

Energy is estimated from token counts, not from dollars. Each model is treated
as one serving replica: some GPUs at some wattage, producing and ingesting
tokens at some rate across its whole batch.

```
joules per output token = gpus × gpu_watts × pue / decode_tps
joules per input token  = gpus × gpu_watts × pue / prefill_tps
cache read  = input × cache_read_factor   (default 0.1: the prefix isn't recomputed)
cache write = input × cache_write_factor  (default 1.0)
```

Providers don't publish replica sizes or throughput, so **every coefficient is
a guess**. The built-in size classes, with `gpu_watts` per GPU including its
share of the host:

| Class | gpus | gpu_watts | decode_tps | prefill_tps | ≈ J per output token |
|---|---|---|---|---|---|
| `large` (Opus, Fable, GPT-5, Grok) | 16 | 1000 | 1500 | 50,000 | 13 |
| `medium` (Sonnet; the default) | 8 | 1000 | 3000 | 100,000 | 3.2 |
| `small` (Haiku, `mini`, `nano`, `flash`) | 4 | 1000 | 6000 | 200,000 | 0.8 |

Everything can be overridden in `config.yaml`:

```yaml
energy:
  pue: 1.2                  # datacentre overhead multiplier
  cache_read_factor: 0.1
  cache_write_factor: 1.0
  default_class: medium     # for models no rule matches
  classes:                  # change a built-in class (unstated keys keep their defaults)
    large: {gpus: 32}       # ...or add your own
    local: {gpus: 1, gpu_watts: 450, decode_tps: 40, prefill_tps: 1500}
  models:                   # model-name substring -> class or profile, checked in
    llama: local            # file order before the built-in rules
    opus: large
    my-model: {joules_per_output_token: 2.0, joules_per_input_token: 0.05}
```

Before 0.5.9 overcode converted dollars to joules as if the whole API price
were electricity at $0.07/kWh. Electricity is a few percent of a token's
price, so that overstated energy by one to two orders of magnitude (#522).

## Passthru Keys

By default, a handful of TUI hotkeys are forwarded straight to the focused agent's Claude Code session rather than being handled by overcode: `Enter`, `Esc`, `1`–`5` (for numbered prompts), and `Ctrl+O`.

You can toggle these on and off from inside the TUI with `Ctrl+K` (`^` under the vscode [key preset](#keybindings)), which saves your choices to `config.yaml`. For more advanced customization — remapping a slot to send a different key, or adding fully new passthru keys — edit `~/.overcode/config.yaml` directly:

```yaml
passthru_keys:
  # Disable a default slot by setting it to null:
  "5": null

  # Remap a slot to forward a different key
  # (e.g. let Ctrl+O send Esc to the agent):
  ctrl+o: "escape"

  # Add a brand-new passthru slot beyond the defaults.
  # The key name must be something Textual recognises
  # (e.g. "f5", "alt+j", "ctrl+space"), and the value is
  # the key actually sent to the tmux pane.
  # Note: adding a new slot also requires a corresponding
  # BINDING to be registered — the modal will surface user-added
  # slots for on/off toggling, but new bindings themselves are a
  # developer-level change.
  # f5: "f5"
```

Only deltas from the defaults are written — a fresh config keeps the section empty. Disabled slots show `☐` in the modal; remapped slots show their target in yellow.

## Keybindings

Every TUI key can be changed under `keys:` in `~/.overcode/config.yaml` (#510):

```yaml
keys:
  preset: vscode                     # default | vscode
  overrides:                         # main-screen actions
    jump_to_agent: [ctrl+j, J]       # action -> one key or a list
    toggle_monochrome: null          # unbind
    reverse_sort: R                  # palette-only commands can be bound too
  scopes:                            # keys inside widgets
    command_palette:
      cursor_down: [down, ctrl+n]
    command_bar:
      toggle_expand: ctrl+l
```

- **Layers.** The default keys are the ones in code (`SupervisorTUI.BINDINGS` plus each widget's own list). A preset is a delta on top of them, and `overrides` / `scopes` are a delta on top of the preset. Each entry names an action and gives its *complete* key list, replacing what the layer below bound.
- **Action names** are the palette's: `overcode view actions` lists them, and `overcode keys` shows every scope's actions with their current keys.
- **Key names** are Textual's (`ctrl+p`, `question_mark`, `pagedown`, `J` for shift+j). Characters (`?`, `@`, `|`), caret (`^P`) and tmux (`C-p`, `M-j`) spellings are accepted too.
- **Scopes:** `app` (the main screen, which `overrides` sets), `command_bar`, `command_palette`, `summary_prompt_lab`.
- **Problems are warnings, never crashes:** an unknown action or scope (including ones removed with the standalone dashboard in 0.6.0: `toggle_preview`, `expand_preview`, `toggle_tmux_sync` and the `fullscreen_preview` scope), a key bound twice in one scope, a key the preset avoids, a typing key bound inside a text field, or a key that shadows a [passthru key](#passthru-keys). The TUI mentions them at startup; `overcode keys --conflicts` lists them.

```bash
overcode keys                  # effective keys, grouped, with source (default / preset / override)
overcode keys --preset vscode  # what a preset would give you (with your overrides)
overcode keys --conflicts      # only the warnings
overcode keys --use vscode     # switch preset (writes keys.preset)
overcode keys --presets        # list presets
```

The palette's **Key preset** command switches preset and rebinds the running TUI immediately. The help overlay (`h`), the palette and the footer always show the keys in effect.

### VSCode preset

VSCode's integrated terminal runs some keys as VSCode commands instead of passing them to the program (`terminal.integrated.commandsToSkipShell`). On Linux/Windows that takes `Ctrl+P` (Quick Open), `Ctrl+K` (chord prefix), `Ctrl+G` (recent directory), `Ctrl+E`, `Ctrl+J` and `Ctrl+F`; on every OS it takes `Ctrl+Space`, ``Ctrl+` ``, `Ctrl+Tab`, `Ctrl+Q` and `F1`. On macOS most workbench shortcuts use Cmd, so the Ctrl keys get through there, but they still clash with VSCode habits. The `vscode` preset moves off all of them:

| Action | Default | vscode |
|---|---|---|
| Jump to agent | `Ctrl+P` | `Space` |
| Rename agent | `Ctrl+N` | `N` |
| Send Ctrl+O to agent | `Ctrl+O` | `v` |
| Passthru keys… | `Ctrl+K` | `^` |
| Cycle focal repo | `Ctrl+R` | `@` |
| Enhanced context | `Ctrl+T` | `+` |
| Tmux toggle key… | `Ctrl+G` | `\|` |
| Command bar: multi-line | `Ctrl+E` | `Ctrl+L` |
| Palette: next / previous | `↓` `Ctrl+N` `Ctrl+J` / `↑` `Ctrl+P` `Ctrl+K` | `↓` / `↑` |

It also recommends a tmux pane toggle key other than `Ctrl+Space` (`Tab`, the default); your `tmux.toggle_key` is never changed for you. `overcode doctor` suggests the preset when it sees `TERM_PROGRAM=vscode`.

The alternative is to keep the default keys and let VSCode pass them through: add the command with a `-` prefix to `terminal.integrated.commandsToSkipShell` (e.g. `"-workbench.action.quickOpen"` for `Ctrl+P`), set `terminal.integrated.allowChords` to `false` for `Ctrl+K`, or set `terminal.integrated.sendKeybindingsToShell` to `true`.

## Standing Instruction Presets

Presets are saved in `~/.overcode/presets.json`. View available presets:

```bash
overcode instruct --list
```

### Built-in Presets

| Preset | Description |
|--------|-------------|
| `DO_NOTHING` | Supervisor ignores this agent completely |
| `STANDARD` | General-purpose safe automation |
| `PERMISSIVE` | Trusted agent, minimal friction |
| `CAUTIOUS` | Sensitive project, extra careful |
| `RESEARCH` | Information gathering and exploration |
| `CODING` | Active development work |
| `TESTING` | Running and fixing tests |
| `REVIEW` | Code review and analysis only |
| `DEPLOY` | Deployment and release tasks |
| `AUTONOMOUS` | Fully autonomous operation |
| `MINIMAL` | Just keep it from stalling |

### Custom Presets

Edit `~/.overcode/presets.json` to add your own:

```json
{
  "MY_PRESET": "Focus on security. Never commit directly to main. Always run tests.",
  "FRONTEND": "Use React patterns. Prefer functional components. Test with Jest."
}
```

Then use with:
```bash
overcode instruct my-agent MY_PRESET
```

## TUI Preferences

The TUI saves your display preferences per tmux session in:
```
~/.overcode/sessions/{session}/tui_preferences.json
```

Saved preferences include:
- Summary detail level (low/med/full)
- Timeline visibility
- Daemon panel visibility
- Sort mode
- Monochrome mode
- Cost display toggle

These persist across TUI restarts.

## Data Storage

### Session Data
```
~/.overcode/
├── archive/                             # The session archive (see Session Archive)
│   ├── 2026-10.jsonl                    # This month: one slim record per archived agent
│   ├── 2026-09.jsonl.gz                 # Closed months, gzipped once
│   └── cursor.json                      # How far the journey's counts have read
└── ledger/                              # Spend by billing code, kept for ever (see Billing Codes)
    ├── 2026-10.jsonl                    # This month's spend rows
    ├── 2026-09.jsonl.gz                 # Closed months, gzipped once
    └── totals.json                      # Running totals per billing code
~/.overcode/sessions/
└── sessions.json                        # Live sessions (all tmux sessions), plus terminated ones for the grace
~/.overcode/sessions/{session}/
├── agents/{session-id}/                 # One live agent's files (removed when it is archived)
│   ├── hook_state.json                  # Latest hook event (status detection)
│   ├── hook_events.jsonl                # Recent hook events (rotated at 100 KB)
│   ├── report.json                      # `overcode report` from a child
│   └── episodes.jsonl                   # Status episodes
├── bundles/
│   └── 2026-10-12.agents.gz             # The files of every agent archived that day, one gzip member each
├── .layout                              # "2" once the pre-0.6 files are migrated
├── agent_status_history.csv             # Status timeline (active window)
├── agent_status_history.<ts>.csv.gz     # Rotated archives (#468)
├── monitor_daemon_state.json            # Current metrics
├── monitor_daemon.pid                   # Monitor process ID
├── supervisor_daemon.pid                # Supervisor process ID
├── supervisor_stats.json                # Supervisor token tracking
├── tui_preferences.json                 # TUI settings
└── diagnostics/
    ├── event_loop_timing.csv            # TUI heartbeat probe, hard-capped (#465)
    └── status_changes.csv               # Per-agent status transition log
```

With a custom `OVERCODE_STATE_DIR`, `archive/` and `ledger/` sit beside the
session directories inside it (or beside it, when it is called `sessions`).

### Global Data
```
~/.overcode/
├── config.yaml      # User configuration
├── usage/           # Subscription quota: readings, token stream, fitted model
├── presets.json     # Standing instruction presets
└── presence_log.csv # User presence tracking (macOS)
```

## History Retention

`agent_status_history.csv` and `diagnostics/event_loop_timing.csv` are both
append-only and grow without bound if left unmanaged — on a long-lived host
they've been observed reaching multiple GB (#465, #468). They're handled
differently because they serve different purposes:

- **`agent_status_history.csv` is archival.** It backs the timeline views
  (TUI timeline, parquet export, sister timelines), so history is valuable and
  shouldn't be silently discarded. The monitor daemon checks it at most
  hourly and, once it exceeds `status_history_rotate_mb` or spans more than
  7 days, rotates it: rows older than 30 hours (comfortably above the
  24h widest windowed reader) are moved into a compressed
  `agent_status_history.<YYYYMMDD-HHMMSS>.csv.gz` archive alongside the
  active file (gzip typically shrinks this data ~15-20x), while the active
  file keeps everything the windowed TUI/export readers need (3h/24h).
  Archives older than `status_history_max_days` are deleted on the same
  hourly pass. overcode itself reads only the active file; the archives
  are there for your own analysis until they age out.
- **`diagnostics/event_loop_timing.csv` is diagnostic.** It's the TUI's
  event-loop responsiveness probe (records every ~100ms, flushed every 5s)
  and isn't read by any view — it's a debugging aid. Rather than
  archiving it, it's just hard-capped: once it exceeds
  `event_loop_timing_cap_mb`, the TUI truncates it down to its newest ~10%
  on the next flush. Set `event_loop_timing_enabled: false` to turn the
  probe off entirely if you don't need it.

```yaml
history_retention:
  status_history_max_days: 540    # delete rotated archives older than this (~18 months)
  status_history_rotate_mb: 50    # rotate the active CSV past this size
  event_loop_timing_cap_mb: 100   # hard cap for diagnostics/event_loop_timing.csv
  event_loop_timing_enabled: true # false disables the heartbeat probe entirely
```

`overcode doctor` flags either file (including archives, combined) once it
exceeds 5GB (sized above an intentional 18-month archive set), naming the path and the config knob to fix it.

## Session Archive

`sessions.json` is one file for every agent overcode has launched on the
machine, across all tmux sessions, and every TUI worker and daemon tick
parses it whole while every write rewrites it whole. Left alone it grows
with every agent ever launched: a killed agent stays in it as
`status: terminated` until `overcode cleanup` moves it to the archive.

The monitor daemon does that move itself. Once a session's status has
been `terminated` for `terminated_grace_seconds` (default one hour; the
check runs every housekeeping pass, so allow up to two minutes on top) the
daemon removes it from `sessions.json` and adds it to the archive with the
same record `overcode cleanup` writes (`end_time`, `status: archived`). A
child that stopped without reporting and whose window has gone goes the
same way. Any daemon does this for terminated entries in any tmux session,
so entries left behind by a tmux session that no longer has a daemon are
cleared too; at most 50 are archived per pass. During the grace the TUI's
"killed agents" ghost rows (Agent visibility dialog) still list the agent, and `overcode revive`
can still find it; after it, the agent is in the archive (`overcode
history`, and `overcode show` of a name no longer live).

```yaml
session_archive:
  terminated_grace_seconds: 3600  # 1 hour; negative disables automatic archiving
archive:
  retention_months: 0             # 0 (default) keeps every month; N deletes months older than N
```

The archive is a JSONL file per month, `~/.overcode/archive/YYYY-MM.jsonl`,
named by when agents were archived; the first archive of a new month gzips
the months before it, which never change again. A record is slim (about
1 KB): the agent's identity, parent, billing code, tags, backend and model,
directory and branch, times, report and the whole `stats`, plus a pointer
to its own files in the day bundle. It never holds the launch command, a
sister's API key or the standing instructions (only whether there were
any). `overcode history` lists the last 30 days by default (`--since 2w`,
`--since 2026-09`, `--all`); `overcode history <name>` finds the newest
agent of that name (or former name, or id), newest month first.

An `archive.json` or `archive.jsonl` from an earlier version is split into
months (by each record's end time), slimmed, and deleted the first time the
archive is touched: the old files held every launch command and any
sister's API key, so no copy is kept.

## Agent Files and Retention

While an agent is live its own files (hook state and event log, report,
episode log, and what the namer reads: `brief.txt`, the first 2 KB of its
launch prompt, and `prompts.jsonl`, its first three prompts) are in
`sessions/<tmux>/agents/<session-id>/`, keyed by its
session id so a new agent of a reused name never reads what an earlier one
left. When it is archived the directory is packed into one gzip member of
that day's bundle, `sessions/<tmux>/bundles/YYYY-MM-DD.agents.gz`, and
removed, in a crash-safe order (pack, then the archive record, then out of
`sessions.json`, then the directory). `agents/` therefore stays the size of
the live fleet. `overcode history <name> --files` says whether an agent's
files are kept and lists them; `zcat <day>.agents.gz` streams a whole day,
one JSON object per agent.

A directory whose record has gone (a crash part-way, a hook arriving after
the agent was archived) is an orphan: the daemon's hourly sweep, `overcode
cleanup` and `overcode kill` pack it (or remove it, if its archive record
exists) once it has been still for ten minutes.

Bundles, the archive and the ledger are kept for ever by default. Set a
limit to prune the bundles:

```yaml
agent_history:
  retention_days: 0        # 0 (default) keeps every day; N deletes bundle days older than N
  max_bytes: 0             # 0 (default) no cap; e.g. 1GB drops the oldest days past it
  keep_hook_events: true   # false packs only hook state, report and episodes
```

With `terminated_grace_seconds` negative nothing is archived, so neither
`sessions.json` nor `agents/` is bounded; `overcode doctor` says so, and
warns when `agents/` holds far more directories than there are live
agents. Its storage section lists the directories, bundles, archive and
ledger with their sizes.

Upgrading from 0.5: the monitor daemon moves each tmux session's old
name-keyed files (`hook_state_<name>.json` and the rest) into a bundle a
batch at a time and writes `sessions/<tmux>/.layout`. An agent launched
before the upgrade keeps writing its name-keyed files until it is
relaunched (restart, revive), and they are left in place until then.

## Delegated Work

A Claude agent hands work out inside its own session: subagents (in the
background unless the call says otherwise), forks, workflows and in-process
teammates. None of them is an overcode agent, but all of them report
through the parent's hooks, so the parent's row carries a 👥 cell (the
SubAgents column, `SUBA`):

```
👥 ▶15 🟠1 ✅26 ❌1 🧩1
```

how many are ▶ working, 🟠 waiting for approval in the parent's pane, ⚪
idle (teammates listening), ✅ finished, ❌ failed or stopped, and 🧩
workflow runs; counts that are zero are left out, and hovering the cell
gives the total and says what each stands for. An agent that never
delegated shows `👥  -`; one without hook counts (no hooks, or a sister on
an older overcode) shows the subagents its transcript files say are running
(`👥 ▶2`).
The monitor daemon computes the counts from the hook events; the TUI and
`overcode list` only show them. While delegated work is in flight the
parent is 🟡 ("Waiting on 2 background agents and 1 workflow"); it turns
🔴 only when nothing is left running. An idle teammate is listening, not
working, so it does not hold the parent yellow. Delegated agents never
ring the bell: a subagent's permission prompt shows on the parent (🟠),
and the parent's own rules decide.

```yaml
observability:
  finished_row_seconds: 30   # 0 folds a finished agent into the counts at once
```

`finished_row_seconds` is how long a finished agent keeps its own row
under its parent; after that it is only a ✅/❌ count in the parent's
SubAgents cell. The TUI's Agent visibility dialog (`d`) sets it too, and
can hide finished agents altogether, or all but the failed ones.

## Children Presets

A children preset bundles what happens to a child agent that stops without
reporting (its oversight policy and timeout), how soon a done child's
window is closed, and a default budget. `overcode launch --preset <name>`
picks one; a child launched without `--preset` gets `children.preset`.
The built-ins:

| Preset | `on_stuck` | `archive_after` | `budget` |
|--------|------------|-----------------|----------|
| `supervised` (the default) | `wait` (no timeout) | `1h` | none |
| `balanced` | `timeout:24h` | `1h` | none |
| `autonomous` | `timeout:1h` | `10m` | none |

`supervised` is overcode's behaviour before presets, so leaving the
section out changes nothing. Change a built-in field by field, or add
presets of your own:

```yaml
children:
  preset: balanced            # the default for children launched without --preset
  presets:
    autonomous:
      archive_after: 30m      # only this field changes
    overnight:
      on_stuck: timeout:8h    # wait | fail | timeout:DURATION
      archive_after: never    # DURATION, or never
      budget: 5.00            # USD; moved out of the parent's budget if it has one
      description: Long unattended runs
```

| Key | Value |
|-----|-------|
| `on_stuck` | `wait`, `fail` or `timeout:DURATION`, as `--on-stuck` |
| `oversight_timeout` | A DURATION: shorthand for `on_stuck: timeout:DURATION` (`0` means `wait`) |
| `archive_after` | How long a done child keeps its window before the monitor daemon closes it (`never` keeps it); the record then reaches the archive after `session_archive.terminated_grace_seconds` |
| `budget` | A default budget in USD for children launched with the preset |
| `description` | Shown by `overcode config presets` |

A DURATION is `30s`, `5m`, `1h`, `2d` or a number of seconds. A field a
preset leaves out takes the value of the built-in with the same name, or
`supervised`'s for a new preset. Explicit launch flags (`--on-stuck`,
`--oversight-timeout`, `--archive-after`, `--budget`) always win over the
preset. In config.yaml, an unknown preset name (as `children.preset`), an
unknown key or an unreadable value is reported as a warning at launch and
ignored; the launch goes ahead with the default. On the command line it is
different: `--preset` with a name that doesn't exist refuses to launch and
lists the valid names. `overcode config
presets` lists the presets in effect and the warnings. See
[Child Agents](children.md) for how oversight works.

## Billing Codes

Every agent's spend is filed under a billing code, kept in a ledger for
ever. The code is the one named after the agent's launch name unless set at
launch, so every run named `shirka` rolls up under one `shirka` code; a
child or a fork always takes its parent's code (also with `--no-inherit`; a
child's own `--billing-code` is ignored with a warning). The code is
sticky: renaming the agent keeps it.

A code has an id (`c_…`, fixed) and a name (yours to change). Renaming a
code changes how every report shows it, past spend included, without
touching the ledger; its old name still works wherever a code is typed
(launch, rebill, the TUI), unless another code takes it. Two codes that
should have been one can be merged: the first one's spend, past and
future, reports under the second.
In the TUI, the "Billing codes…" dialog (`/` palette) lists every code
with its spend (total, this month, last month) and renames or merges them
in place ([TUI guide](tui-guide.md#billing-codes)).

```bash
overcode launch -n research --billing-code client-x   # set it at launch
overcode rebill research client-y                     # from now on; the spend so far stays on client-x
overcode billing                                      # spend per code, all time
overcode billing --since 30d                          # or --month 2026-09, --json, --ids
overcode billing rename client-y acme                 # every report says acme from now on
overcode billing merge acme-v2 acme                   # fold acme-v2's spend into acme
overcode billing codes                                # name, id, source, agents, spend, old names
```

The codes live in `~/.overcode/ledger/codes.json`. A code's `source` is
`default` (created for a launch under its name) or `human` (given with
`--billing-code`, a rebill, a rename). Ledgers written before codes had ids
(0.6.0) are not rewritten: the first start gives each code string a code
of its own, and reports map the old rows to it, so a renamed code keeps its
history and a new code later given its old name starts from nothing.
`overcode doctor` warns about an agent whose code is missing from the
registry and about ledger code strings no code maps.

`overcode rebill` is forward only: the ledger closes the old code with the
agent's spend so far and starts a new segment under the new one. Children
launched afterwards take the new code; children already running keep
theirs.

A Claude background session shown under an agent (a 🌙 row, see
[TUI guide](tui-guide.md#claude-background-sessions-)) is billed to that
agent's code, as a child would be. It is its own conversation, so its spend
is never added to the agent's: it gets ledger rows of its own (a checkpoint
as soon as it spends, then at most every `ledger.checkpoint_minutes`, and a
final row when it finishes or its row goes), and `overcode billing` counts
it among the code's agents and in a `background` column. A session resumed
after it finished opens a new segment from where the last one closed, and a
rebill of its agent moves only its later spend, so neither is counted twice;
a daemon restart picks up where the ledger stands (`ledger/claude_bg.json`).

An agent's spend can go *down* once, when a fix to a transcript reader
restates it (0.6.1: Claude spend was over-counted by up to 2x, see
[Claude Session Files](claude-session-files.md)). The ledger then gets a
`correction` row for each of the agent's billing segments, scaling each
segment's spend by the same ratio, so the per-code totals drop by the
over-count and a rebilled agent's spend stays split as before. Archived
agents are restated too, once, while their transcripts are still on disk;
agents whose transcripts are gone keep their figures.

In the TUI, the new-agent dialog's Billing row and the "Billing code…"
palette command (which rebills, exactly as `overcode rebill` does) cycle
with `Space` through the agent's own name and a shortlist from config.yaml;
any other code can be typed. See [TUI guide → Billing codes](tui-guide.md#billing-codes).

```yaml
billing:
  codes: [client-a, client-b, internal]   # empty by default
```

A number is taken as its text; an entry that is not a code, or a `billing`
or `codes` of the wrong shape, is skipped with a warning in the dialog.

The ledger, `~/.overcode/ledger/YYYY-MM.jsonl`, has one row per spend event,
each carrying the agent's cumulative spend so far: a final row when the
agent is archived, and a checkpoint for a running agent whose spend moved,
at most every `checkpoint_minutes`, so a long-running agent's spend shows
before it ends. `ledger/totals.json` folds the rows incrementally, keeping
the last row of each agent, so a repeated row is never counted twice.
Agents archived before the ledger existed are added once, under their own
name, when the daemon first migrates.

```yaml
ledger:
  checkpoint_minutes: 60   # a running agent's spend row, at most this often
```

There are no per-code limits: the per-agent budget (`overcode budget`) and
the child-spend rollup are unchanged.

## Naming

How agents are named (#542). Every agent records where its name came from
(`name_source` in sessions.json, shown by `overcode list --json`):
`default` (the new-agent dialog's directory name or a fork's `-fork`,
untouched), `parent` (a parent agent's `-n`), `auto` (overcode named it) or
`human` (you typed it). `parent` and `human` names are never renamed by
overcode. Records from before this field read as `default` only when the
name is exactly a default overcode would have made up (the record's
directory name, with the dialog's `2`-`99` or `_new` suffix, or a name
ending in `-fork`); everything else reads as `human`.

```yaml
naming:
  advice: |                 # the naming policy, in your words
    kebab-case, 1-3 words, what the work is about, not how.
    Put an issue number last when there is one: fix-jwt-542.
  max_length: 18            # the length the namer aims for (not a check)
  rules:                    # optional checks; every one is off by default
    pattern: null           # a regex the name must match, e.g. '^[a-z0-9]+(-[a-z0-9]+)*$'
    max_length: null        # a hard limit on length
    forbid: []              # words a name may not contain, e.g. [test, agent, child, tmp]
    require: null           # a regex the name must contain, e.g. '-[0-9]+$'
    no_repo_prefix: false   # refuse a name starting with its repo's while the repo column shows
    recent_archived_hours: 0  # refuse a name an agent archived this recently had (0: off)
    llm_check: false        # also ask the summarizer model whether the name follows the advice
    warn_humans: false      # warn (never refuse) when a name you type breaks the rules
  enabled: false            # auto-naming: the engine names agents (off by default)
  first_after: {turns: 2, green_minutes: 3, output_tokens: 15000}
  max_renames: 2            # auto renames in an agent's life (the first name + one refinement)
  cost_cap_per_day: 0.50    # USD a day for the namer's model calls (per tmux session)
  # provider:               # the namer's model; default: the summarizer: block
  #   api_type: anthropic
  #   api_url: https://api.anthropic.com/v1/messages
  #   model: claude-haiku-4-5
  #   api_key_var: ANTHROPIC_API_KEY
  cli_title: true           # keep Claude Code's own session name in step (below)
```

**Auto-naming** (`enabled: true`, or a children preset that sets it). The
engine names an agent whose name is `default` or `auto` once its task is
clear: at the first turn end (Stop) after `first_after` work (at least
`turns` completed turns, and `green_minutes` of green time or
`output_tokens` output tokens). One refinement comes at 10 times that
work, and renames only if the model says the task has changed. An auto
name changes at most `max_renames` times, at least 10 minutes apart. The
namer never renames a `human` or `parent` name, never while the agent
waits on a permission dialog, and never within 30 s of a key pressed in
the TUI (it waits for the next Stop). A rename is overcode's ordinary
restartless rename: the agent keeps running, the old name stays an alias,
the agent and its parent are told with their next prompt, and the TUI
shows a toast (`old → new (auto)`) and the `~` marker. The first auto name
also renames the agent's billing code when that code was made just for it
(a root agent's default code, not one you chose).

The model sees the agent's brief (the first 2 KB of its launch prompt),
its first three prompts, its repo, branch and PR, the files it edits most,
its name and parent, and the fleet (live agents and the last day's
archived ones, siblings together), with the advice and `max_length` as a
target. An agent with no hooks gives its last 60 terminal lines instead.
It answers `{"candidates": [...], "keep": bool, "why": "..."}`; overcode
picks the first candidate that passes the rules and is not taken (a
numbered series stays legal), else adds the shortest number. Each call is
a ledger row under the billing code `overcode`; at about two calls per
agent with a mini model, 100 agents cost about $0.05. With no model
configured (no `summarizer:` key) the daemon log says so once.
`overcode name suggest <agent>` asks for one agent's name without
renaming it.

**Claude Code's own name.** Claude Code has a session name of its own (its
prompt box, `/resume`, the terminal title). overcode launches it with
`--name <agent name>`; after a rename it types `/rename <new name>` into the
agent, only when the agent is idle with an empty input line (otherwise at a
later turn end); and a `/rename` you type in Claude renames the agent too,
as a name you chose (`human`, locked). This runs whether or not
auto-naming is on; `cli_title: false` turns it off.

**The advice is the policy.** It is free text, read by a model: the
auto-namer, and `llm_check` when it is on. Say in it what you want names to
look like. A repo can add its own conventions in `<repo>/.overcode/naming.md`,
appended after yours. `overcode name policy` prints both.

**The rules are an optional supplement**, checked with no model and no delay
when an agent launches a child (`overcode launch -n` / `fork -n` with
`OVERCODE_SESSION_ID` set). Their defaults reject nothing: a numbered series
such as `wiki1`, `wiki2`, `wiki3` is always fine. Two checks always apply,
rules or not: overcode's name pattern (letters, digits, `-` and `_`, at most
64) and a name another live agent has, or had before a rename. A refused
name exits 3 with the rule, the advice and a suggested name;
`--force-name` launches anyway, except past those two. `llm_check` costs one
summarizer call per child launch, waits at most 5 s, and lets the name
through if the call fails.

**The namer's prompt** is `~/.overcode/prompts/naming.md` once saved (the
naming lab will edit it); until then a built-in default. Its placeholders are
`{advice}`, `{brief}`, `{prompts}`, `{repo}`, `{branch}`, `{files}`,
`{current_name}`, `{name_source}`, `{parent}`, `{fleet}`, `{siblings}` and
`{max_length}`.

An unknown key or a bad value under `naming:` is ignored with a warning
(`overcode name policy` prints it).

## Usage Log

The TUI records how you use overcode, for the learning journey (#483): every
key, the action it ran and how you reached it (key, palette or click),
dialogs opened and closed, palette searches, and the `overcode` commands you
run. `overcode activity summary` shows what it has seen.

Records go to `~/.overcode/activity/YYYY-MM-DD.jsonl`, one JSON object per
line. Nothing leaves the machine.

Full records are kept for 14 days. After that each day is folded into a
day summary (counts per action, key, dialog and so on) in
`activity/rollup/YYYY-MM.json`, and the day's records are deleted. That keeps
the log to about two weeks of records plus a few KB per day, so the journey
stays quick to open. `overcode activity keys` shows only the last 14 days;
`overcode activity summary --since` and the journey read both. Only files
under `activity/` are touched: the status history and other logs keep their
own retention (see [History Retention](#history-retention)).

What is never recorded: anything you type to an agent. In the command bar's
send, standing-orders and heartbeat-instruction modes, keys are logged as
`<c>` with no character, and pastes anywhere are logged by length only.
Overcode's own metadata is recorded in full: tag and agent names,
annotations, palette searches. CLI records keep the command and flag names,
never their values. Commands run by agents overcode launched are only
counted, per command per day, in `activity/agent-cli/`, and never count as
yours.

```yaml
activity:
  record: true   # false turns it off; no record is created at all
```

`OVERCODE_ACTIVITY=0` turns it off too (the test suite sets this), and the
palette command **Record activity** pauses it for the current TUI run.

The [journey](tui-guide.md#your-journey) (`u`) is worked out from this log.
How fast unused capabilities slip back is a setting:

```yaml
journey:
  decay_grace_days: 30   # unused this long: one level down
  decay_step_days: 45    # then one more level each this many days
  mentor: off            # off | occasional | coach — see the TUI guide
```

## Unattended Low-Power Mode

Nothing used to be gated on anyone watching: the TUI captured panes four
times a second and the monitor daemon looped every 2 s whether or not a
tmux client was attached, the screen was locked or the laptop lid was
shut. Both now notice when nobody is looking, and both come straight back
when someone is.

**The TUI** watches whether a tmux client is attached to the pane it runs
in (one `tmux display-message` a second) and tells the engine (the monitor
daemon) over `engine.sock`. While none is, the focused-pane capture, the
timeline read, the AI summaries, the sister polls, the jobs and sessions
refreshes and the resize sweep are paused, and nothing is repainted. The
engine keeps recording, and its bells still reach the TUI, so macOS
notifications keep working while you are away. The moment a client
attaches (or a key is pressed) every timer resumes and one full refresh
runs. A TUI run outside tmux can't tell, so it always behaves as attended.

**The monitor daemon** stretches its loop from `interval_fast` (2 s) to
`interval_unattended_seconds` (default 10 s) when nothing says anyone is
watching: no view on `engine.sock` reports itself visible, no client is
attached to the agents tmux session, no TUI keypress heartbeat is fresh
(60 s), and the API server has not touched the `tui_attended` file in the
last 15 s (it does whenever it serves a status request, so a fleet watched
through a sister TUI stays on the fast loop too). It returns to the fast
interval as soon as a view says it is visible again (the sleep ends at its
next 1 s chunk), within one loop of a client attaching, and within about a
second of a key being pressed. The published state carries
`interval_mode` (`attended` / `unattended`) and the TUI's daemon status
bar shows `(unattended)` next to the interval when the daemon is
still in that mode — normally only for the loop after you return.

What the coarser loop does *not* change: `agent_status_history.csv` is
written on change (plus a 60 s keepalive), so the timeline you come back
to has no holes at 10 s resolution; heartbeats, oversight timeouts and the
every-two-minutes housekeeping (done-agent auto-archive, untracked window
count, terminated-session archive) are all wall-clock and keep their
cadence.

```yaml
monitor_daemon:
  interval_unattended_seconds: 10  # loop while nobody is watching; must be >= 2 (interval_fast)
```

Read when the daemon starts: after changing it, restart the daemon with
`overcode monitor-daemon stop` then `overcode monitor-daemon start`, or
press `\` (Restart monitor) in the TUI.

## Pricing Configuration

Cost estimates are **model-aware** when you launch agents with `--model`. Built-in pricing is included for Claude Opus, Sonnet, and Haiku. Agents launched without `--model` use the global `pricing:` section as a fallback.

```yaml
# Global fallback pricing (used when no model is set) — defaults to Sonnet
pricing:
  input: 3.0
  output: 15.0
  cache_write: 3.75
  cache_read: 0.30
```

To override or add pricing for a specific model keyword:

```yaml
model_pricing:
  my-custom-model:
    input: 10.0
    output: 50.0
    cache_write: 12.5
    cache_read: 1.0
```

Claude Code writes its prompt cache with the 1-hour TTL, which Anthropic
bills at twice the input price (5-minute writes are 1.25x). The transcript
splits each message's cache writes by TTL, and the 1-hour part is priced at
`cache_write_1h`: twice `input` unless set, in `pricing:` or per model in
`model_pricing:`. The Bedrock premium and discount scale it too.

**Where a model's price comes from**, first match wins:

1. Your `model_pricing:` rows, matched as substrings of the model id,
   longest key first.
2. Your **local price table** (`~/.overcode/cache/claude_prices.json`),
   if you built one with `overcode models refresh --prices` and it is dated
   after the shipped table (see "Prices on an air-gapped host" below).
3. The **price table** shipped with overcode
   (`src/overcode/data/claude_prices.json`): one row per Claude model id,
   each with its source and as-of date, generated from the AWS Price List
   (whose `global` column is Anthropic's list price) when the release is
   made. Bedrock, Vertex and opencode spellings of an id
   (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`,
   `claude-opus-4-5@20251101`, `anthropic/claude-opus-4.1`) find the same row.
4. The built-in family rows (`pricing.MODEL_PRICING`: `opus`, `sonnet`,
   `haiku`, ...), matched as substrings: only for ids the table does not
   name, such as an alias or a model released after it. `overcode doctor`
   warns when an agent's Claude model is priced this way.
5. The models.dev catalog (below), then the global `pricing:` rates.

`overcode models lookup <model>` shows which row answered; `overcode
models info` shows which price table is in use, its date, source and age.
The shipped table's rows as of 2026-10-12, per million tokens:

| Model | Input | Output | Cache write (5 min / 1 h) | Cache read | Long context |
|---|---|---|---|---|---|
| Fable 5.1 | $10 | $50 | $12.50 / $20 | $0.25 | — |
| Fable 5 | $10 | $50 | $12.50 / $20 | $1.00 | — |
| Opus 5.5 | $4 | $20 | $5 / $8 | $0.20 | — |
| Opus 4.5 – 5 | $5 | $25 | $6.25 / $10 | $0.50 | — |
| Opus 4, 4.1 | $15 | $75 | $18.75 / $30 | $1.50 | — |
| Sonnet 5.5 | $2 | $10 | $2.50 / $4 | $0.10 | — |
| Sonnet 5 | $2 | $10 | $2.50 / $4 | $0.20 | — |
| Sonnet 4 – 4.6, 3.5, 3.7 | $3 | $15 | $3.75 / $6 | $0.30 | Sonnet 4 over 200K: $6 / $22.50 |
| Haiku 5.5 | $0.10 | $0.50 | $0.125 / $0.20 | $0.01 | over 100K: $0.50 / $2.50 |
| Haiku 4.5 | $1 | $5 | $1.25 / $2 | $0.10 | — |
| Haiku 3.5 | $0.80 | $4 | $1 / $1.60 | $0.08 | — |

**Long context** is priced per request: a request whose prompt (input +
cache read + cache write tokens) is over the threshold is billed whole at
the long-context rates, and every other request at the base rates, however
long the session has grown.

A price change applies to tokens read from then on: spend already recorded
keeps the rate it was priced at.

### Prices on an air-gapped host

Pricing never needs the network. The Claude price table ships inside each
overcode release, so upgrading overcode is the usual way to get new
prices. `overcode doctor` warns when the table in use is more than 90 days
old (`pricing.table_max_age_days`).

When you cannot upgrade, rebuild the table from the AWS Price List file
overcode's releases are made from. On a machine with internet access,
download the Bedrock foundation-model offer file (public, no AWS account
needed):

```bash
curl -o AmazonBedrockFoundationModels.json \
  https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrockFoundationModels/current/us-east-1/index.json
```

The URL pattern is
`https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrockFoundationModels/current/<region>/index.json`.
Use `us-east-1`, as the releases do: its `global` column is Anthropic's
list price. The index of all offers is
`https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/index.json`.

Carry the file in by whatever route your site allows (it is plain JSON;
gzip it if you like, overcode reads `.gz` too), then:

```bash
overcode models refresh --prices AmazonBedrockFoundationModels.json --check  # show what changes
overcode models refresh --prices AmazonBedrockFoundationModels.json          # write it
overcode models info                                                         # confirm it is in use
```

This writes `~/.overcode/cache/claude_prices.json`; the installed package
is never modified. It is used ahead of the shipped table for as long as it
is dated after it: its rows replace the shipped ones, and a model it does
not list keeps its shipped row. Upgrading to an overcode whose table is as
new or newer makes it unused (delete it, or leave it). A file older than
the shipped table is refused, since it would be ignored. Running daemons
pick up the new table on their next price lookup. The same thresholds and
notes the release adds (long-context tiers the AWS list does not state)
are applied. Your `model_pricing:` rows still win over both tables.

`overcode models refresh` without `--prices` fetches the models.dev
catalog; offline it fails within about 10 seconds and leaves everything as
it was.

### Bedrock

Amazon Bedrock bills a `global.` cross-region inference profile at the
list price above, and a **geographic** (`us.`, `eu.`, `apac.`, ...) or
**in-region** one at **1.1x** list, on every model from the 4.5 generation
on (older models cost the same everywhere). overcode records the routing
when it launches a Bedrock agent, from (first match) the model id it
passes (`--model us.anthropic.claude-...`), else `ANTHROPIC_MODEL`; then
`bedrock.routing` in config, else `ANTHROPIC_BEDROCK_REGION_PREFIX`; else
the prefix Claude Code derives from `bedrock.region` (`us-*` → `us.`,
`eu-*` → `eu.`, `ap-*` → `apac.`, other regions → `global.`). With the
default `us-east-1`, that is `us.`: 1.1x. An application inference profile
ARN is priced as regional. Agents launched before overcode 0.6.1, or
detected as Bedrock without being launched as such, use `bedrock.routing`
(else the region's):

```yaml
bedrock:
  region: us-east-1       # what overcode sets as AWS_REGION for Bedrock agents
  routing: global         # optional: the profile prefix your agents use
```

Enterprise customers usually pay a **negotiated discount** off that (an
AWS Marketplace private offer / committed-use agreement). Apply it so
estimates match your invoice — it only affects sessions detected as
Bedrock (`msg_bdrk_` message IDs):

```yaml
bedrock:
  discount: 0.30          # flat fraction off for all Bedrock sessions (30% off)
  model_discount:         # optional per-model-family overrides (win over the flat rate)
    opus: 0.25
    sonnet: 0.35
```

Discounts are clamped to `[0, 1)` and scale every rate (input, output, cache write, cache read, both tiers) uniformly, after the regional premium. Web / Claude-Max / direct-API sessions are unaffected.

> **What this can't express:** a flat percentage can't model **provisioned throughput** (a fixed `$/hour` reserved-capacity commitment, not per-token billing) or **input/output-asymmetric** net rates. For those, pin exact net per-MTok rates via `model_pricing:` and mark them `net: true`: a net row is taken as it is, with no premium and no discount. A `model_pricing:` row without `net: true` is a list price and gets both (it takes the built-in row's premium unless it sets `regional:`). `overcode doctor` warns when a discount is set and a row does not say which it is. Ask your AWS/Anthropic account manager for your effective per-model rate. Note also that Claude **Max** is a flat subscription with no per-token billing, so its `$` figure is an *API-equivalent* estimate, not an amount you are billed.
>
> ```yaml
> model_pricing:
>   opus-4-6: {input: 3.85, output: 19.25, cache_write: 4.81, cache_read: 0.385, net: true}
> ```

### Checking the estimate

When a Claude Code session ends it records what it counted and charged
(the transcript's `cost-state` line). `overcode doctor` prices the same
conversation at overcode's list prices and warns, naming the model, when
the two differ by more than 2% (and $0.05): a sign the model's price row
is wrong. Conversations begun before the agent started, or resumed after
the record, are not compared.

The TUI shows costs based on these rates. Press `$` to toggle between token counts and dollar amounts.

## Subscription Quota

On a Claude subscription (Pro, Max, Team or Enterprise seats) the dollar
figure is notional: what you use is a share of a 5-hour and a weekly
window. Claude reports those only as whole percents, so overcode
**estimates** each agent's share of the weekly window in percentage points
with two decimals (`1.37%`): the optional `Q%` column (press `$` until
"weekly quota"), a `quota wk` column in `overcode billing`, and the Σ in the
status bar in that mode. Hover a `Q%` cell for the agent's share of the
current 5-hour window, the account's own percentages and how sure the
model is. API-key, Bedrock and Vertex agents have no quota: the column
stays hidden when no agent is on the subscription.

```yaml
quota:
  enabled: true        # false: no polling, no Q% column
  calibration: false   # true: statusline samples (below)
  plan: max20x         # pro | max5x | max20x | team | team_premium | enterprise
  weekly_usd: 1550     # the prior's weekly budget in API-equivalent dollars
  weeks: 4             # history the fit uses (2–8)
  poll_minutes: 5      # usage API poll, at least 5
  breaks: [2026-05-06] # known limit changes: the fit never spans one
```

**How it works.** The monitor daemon polls Claude's usage endpoint once per
account every 5 minutes (backing off when rate limited; the TUI no longer
polls it itself) and keeps each reading in `~/.overcode/usage/<account>/`
together with the tokens overcode's agents had spent since the weekly reset.
It also writes every subscription agent's tokens (deduplicated per message,
as the cost figures are) to `~/.overcode/usage/tokens/`, billed to the
agent's code. An agent's estimate is its tokens since the reset, priced at
Anthropic list prices per token type (input, output, 5-minute and 1-hour
cache writes, cache reads), times a weight per type. Until fitted the
weights are the price ratios scaled to your plan's weekly budget, with cache
reads free (public measurements found them close to free); the plan comes
from your Claude Code login, else `quota.plan`. Once readings accumulate the
daemon fits the weights hourly to the observed weekly percentages, treating
each whole-percent reading as an interval, and keeps them near the price
ratios while data is thin. `overcode usage model` shows the result; the
column is dim while unfitted. Usage the model cannot see (claude.ai chat,
Cowork, Claude Code run outside overcode) is never spread over agents: it
is the `unattributed` row of `overcode billing`. `overcode doctor` warns
when the model predicts more than the account shows, or fits poorly.

**Calibration (opt-in).** Polls every 5 minutes say only that the weekly
figure was, say, 37% at that moment. With `quota.calibration: true`
(`overcode usage calibration on`), the Claude agents overcode launches from
then on run `overcode statusline` as their statusline: after each response
it appends the rate-limit figures, the session's cost and its prompt-cache
statistics to `~/.overcode/usage/statusline/<agent>.jsonl`, so the daemon
sees the exact moment the figure ticks from 37% to 38%, which sharpens the
fit. It then runs your own statusline command, if your project or user
settings configure one, and prints its output, so your statusline looks the
same. Nothing is added to an agent's settings while calibration is off.

## Model metadata (context windows and pricing)

Two tiers feed the `CTX` (context window) and `$` (cost) columns for any
model id a backend reports (#473):

1. **Curated tables** — `history_reader.MODEL_CONTEXT_WINDOWS`, the Claude
   price table (`data/claude_prices.json`) and `pricing.MODEL_PRICING`.
   Every row cites a primary source or a live CLI figure. These always win
   (see "Where a model's price comes from" above for their order).
2. **The models.dev catalog** — [models.dev](https://models.dev)'s open,
   community-maintained catalog (`https://models.dev/api.json`), transcoded
   to ~1,000 text models from first-party vendors and the major gateways
   (OpenAI, Anthropic, xAI, Z.AI/GLM, Moonshot/Kimi, Google, DeepSeek,
   Mistral, Alibaba/Qwen, MiniMax, OpenRouter, Bedrock, Vertex, GitHub
   Copilot, Groq, Together, Fireworks, …). Consulted for any id the curated
   tables don't name. This is also the catalog opencode ships with, so an
   opencode agent's `CTX%` agrees with opencode's own console
   (docs/backends.md, "CTX%: what the context column divides by").

The catalog is whichever of these is **freshest on this machine**:

| Source | Path | Refreshed by |
|---|---|---|
| local refreshed cache | `~/.overcode/cache/model_metadata.json` | `overcode models refresh`, or the daemon when `model_metadata.auto_refresh: true` |
| opencode's own cache | `~/.cache/opencode/models.json` | opencode itself, whenever it runs |
| bundled snapshot | `src/overcode/data/model_metadata.json` (in the wheel) | each overcode release |

Newest of the first two by file time wins; the bundled snapshot is the
offline fallback. Lookups never touch the network, so an air-gapped host
simply runs off whatever copy it has. Nothing is fetched unless you run the
refresh command or opt the daemon in — and the daemon's fetch runs on a
background thread with a 15-second timeout and backs off for six hours
after a failure, so a locked-down network costs one log line, not a stall:

```yaml
model_metadata:
  auto_refresh: false   # daemon fetches models.dev once the local cache is older than max_age_days
  max_age_days: 7
```

`overcode models info` shows which one is active and how old it is;
`overcode models lookup <id>` shows what a given id resolves to and which
tier answered; `overcode doctor` warns when the active catalog is more than
90 days old. Set `OVERCODE_MODEL_METADATA_BUNDLED_ONLY=1` to force the
bundled snapshot (the unit tests do).

Anything in neither renders a dash for `CTX` and falls back to your
configured default per-token rates for `$` (see "Pricing Configuration"
above). A `$0/$0` catalog listing (a coding-plan or promo tier) is treated
as *no price*, not a free model.

Resolution details:

- Ids are normalised before lookup: an opencode `provider/model` qualifier
  and a trailing `[1m]`-style capacity suffix are stripped, and the snapshot
  is matched case-insensitively.
- Where two providers list the same model, the first-party vendor's entry
  wins (`model_metadata.PROVIDER_PRIORITY`), so resellers only ever *add*
  ids.
- Your `model_pricing:` overrides beat both tiers.

Refreshing the *bundled* snapshot (contributors, before a release):

```bash
python scripts/refresh_model_metadata.py           # fetch models.dev, rewrite the file
python scripts/refresh_model_metadata.py --check   # exit 1 if it would change
python scripts/refresh_model_metadata.py --from api.json   # offline
```

The file is one model per line, so a refresh diffs cleanly; commit it with
the version bump it ships in. A proposal for mapping *unrecognised* ids
(internal model names, gateway aliases) onto this catalog is in
`docs/design/model-alias-resolution.md`.

## Skill Emoticons

Overcode displays emoticons for available skills in the TUI's "Available Skills" (ASK) column. You can customize these emoticons in your config file.

### Built-in Skill Emoticons

| Skill | Default Emoji | Purpose |
|-------|:-------------:|---------|
| `overcode` | 🐙 | Running and managing agents through overcode |
| `claude-api` | 🔌 | Claude API/SDK development |
| `simplify` | ✨ | Code quality review |
| `commit` | 📦 | Git commit creation |
| `review-pr` | 🔍 | Pull request review |
| `reset` | 🔄 | Branch reset |
| `loop` | 🔁 | Recurring task execution |
| `schedule` | 📅 | Scheduled tasks |
| *(other skills)* | 🧩 | Default fallback |

### Custom Skill Emoticons

Override defaults or add emoticons for custom skills:

```yaml
skill_emoji:
  shirka: 🔬        # Research project organization
  data-analysis: 📊  # Data science work
  security: 🔒       # Security reviews
```

Emoticons appear in:
- TUI "Available Skills" (ASK) column when viewing agents with `--full` detail. Hover an agent's emoji cell (skills, PRF, tools, wrapper, status detail) to see what each emoji stands for.
- Skill selection dialogs
- Agent summary outputs

**Note:** Emoticons are purely cosmetic and don't affect skill functionality.

## Corporate API Gateway

If your organization uses a corporate gateway for API access, configure the summarizer to use it:

```yaml
summarizer:
  api_url: https://internal-gateway.corp.com/openai/v1/chat/completions
  model: gpt-4o-mini
  api_key_var: CORP_API_KEY
```

## Multiple Sessions

Run separate agent pools with different tmux sessions:

```bash
# Production monitoring
overcode launch -n prod-watcher -d ~/prod --session production
overcode tmux --session production

# Development work
overcode launch -n feature-dev -d ~/dev --session development
overcode tmux --session development
```

Each session has independent:
- Session state and history
- TUI preferences
- Daemon instances
