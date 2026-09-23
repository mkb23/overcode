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

# Cloud relay for pushing status to a remote endpoint
relay:
  enabled: false
  url: https://your-worker.workers.dev/update
  api_key: your-secret-key
  interval: 30  # Seconds between status pushes

# Display hostname (shown in Host column and API response)
# Defaults to system hostname if omitted
hostname: "mac-studio"

# Token pricing for cost calculations
# Defaults match Claude Sonnet 3.5
pricing:
  input: 3.0           # $/million tokens for input
  output: 15.0         # $/million tokens for output
  cache_write: 3.75    # $/million tokens for cache writes
  cache_read: 0.30     # $/million tokens for cache reads

# Negotiated Bedrock discount off list prices (see Pricing Configuration below)
bedrock:
  discount: 0.0        # flat fraction off list for Bedrock sessions (e.g. 0.30)
  # model_discount:    # optional per-model-family overrides
  #   opus: 0.25

# Web server settings
web:
  # API key for web server authentication
  # Required when binding to non-localhost (--host 0.0.0.0)
  api_key: "your-secret-key"
  # Analytics dashboard presets
  time_presets:
    - name: "Morning"
      start: "09:00"
      end: "12:00"
    - name: "Afternoon"
      start: "13:00"
      end: "17:00"
    - name: "Full Day"
      start: "09:00"
      end: "17:00"

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

# When terminated agents leave sessions.json for archive.jsonl
# See "Session Archive" below
session_archive:
  terminated_grace_seconds: 3600  # 1 hour; negative disables automatic archiving

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
  delegating-to-agents: 👥  # Default: 👥 (built-in)
  claude-api: 🔌         # Default: 🔌 (built-in)
  simplify: ✨            # Default: ✨ (built-in)
  shirka: 🔬             # Custom skill example
  my-custom-skill: 🚀    # Add any custom skill with any emoji
```

## Environment Variables

### Directory Overrides

| Variable | Description | Default |
|----------|-------------|---------|
| `OVERCODE_DIR` | Base data directory | `~/.overcode` |
| `OVERCODE_STATE_DIR` | Session state directory | `~/.overcode/sessions` |

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
```

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

## Passthru Keys

By default, a handful of TUI hotkeys are forwarded straight to the focused agent's Claude Code session rather than being handled by overcode: `Enter`, `Esc`, `1`–`5` (for numbered prompts), and `Ctrl+O`.

You can toggle these on and off from inside the TUI with `Ctrl+K`, which saves your choices to `config.yaml`. For more advanced customization — remapping a slot to send a different key, or adding fully new passthru keys — edit `~/.overcode/config.yaml` directly:

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
- Detail lines (5/10/20/50)
- View mode (tree/list_preview)
- Timeline visibility
- Daemon panel visibility
- Sort mode
- Monochrome mode
- Cost display toggle

These persist across TUI restarts.

## Data Storage

### Session Data
```
~/.overcode/sessions/
├── sessions.json                        # Live sessions (all tmux sessions), plus terminated ones for the grace
├── archive.jsonl                        # Archived sessions, one JSON record per line, append-only
├── archive.json.migrated                # The pre-JSONL archive, kept after its one-time migration
~/.overcode/sessions/{session}/
├── {agent-id}.json                      # Individual agent state
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

### Global Data
```
~/.overcode/
├── config.yaml      # User configuration
├── presets.json     # Standing instruction presets
└── presence_log.csv # User presence tracking (macOS)
```

## History Retention

`agent_status_history.csv` and `diagnostics/event_loop_timing.csv` are both
append-only and grow without bound if left unmanaged — on a long-lived host
they've been observed reaching multiple GB (#465, #468). They're handled
differently because they serve different purposes:

- **`agent_status_history.csv` is archival.** It backs the timeline views
  (TUI timeline, parquet export, web analytics), so history is valuable and
  shouldn't be silently discarded. The monitor daemon checks it at most
  hourly and, once it exceeds `status_history_rotate_mb` or spans more than
  7 days, rotates it: rows older than 30 hours (comfortably above the
  24h widest windowed reader) are moved into a compressed
  `agent_status_history.<YYYYMMDD-HHMMSS>.csv.gz` archive alongside the
  active file (gzip typically shrinks this data ~15-20x), while the active
  file keeps everything the windowed TUI/export readers need (3h/24h).
  Archives older than `status_history_max_days` are deleted on the same
  hourly pass. The web dashboard's custom date-range analytics endpoint
  transparently reads both the active file and any archives it needs — deep
  history queries keep working after rotation.
- **`diagnostics/event_loop_timing.csv` is diagnostic.** It's the TUI's
  event-loop responsiveness probe (records every ~100ms, flushed every 5s)
  and isn't read by any dashboard — it's a debugging aid. Rather than
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

The monitor daemon now does that move itself. Once a session's status has
been `terminated` for `terminated_grace_seconds` (default one hour; the
check runs every 60 daemon loops, so allow up to two minutes on top) the
daemon removes it from `sessions.json` and appends it to `archive.jsonl`
with the same record `overcode cleanup` writes (`end_time`, `status:
archived`). Any daemon does this for terminated entries in any tmux
session, so entries left behind by a tmux session that no longer has a
daemon are cleared too. During the grace the TUI's "show killed" ghost
rows (`g`) still list the agent, and `overcode revive` can still find it;
after it, the agent is in the archive (`overcode history`).

```yaml
session_archive:
  terminated_grace_seconds: 3600  # 1 hour; negative disables automatic archiving
```

`archive.jsonl` holds one JSON record per line and is append-only, so
archiving an agent costs one line regardless of how many are archived
already (the previous `archive.json` was rewritten whole on every
archive). An existing `archive.json` is migrated into `archive.jsonl` the
first time the archive is touched and renamed to `archive.json.migrated`.
Everything that reads the archive (`overcode history`, the web analytics
endpoints, `overcode export`) reads the JSONL.

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

Model names are matched as substrings, **longest key first**, so `"sonnet"` matches `"claude-sonnet-4-6"` and a more specific key like `"opus-4-1"` wins over `"opus"`. User overrides in `model_pricing:` take precedence over the built-in table.

Built-in rates use **list prices** (verified June 2026): current Opus 4.5–4.8 at `$5/$25`, Sonnet 4.x at `$3/$15`, Haiku 4.5 at `$1/$5`; legacy Opus 4.0/4.1 keep `$15/$75` and legacy Haiku 3.5 keeps `$0.80/$4`.

### Bedrock discount

Amazon Bedrock on-demand list prices equal the Anthropic API list prices in the built-in table. Enterprise customers usually pay a **negotiated discount** off that list (an AWS Marketplace private offer / committed-use agreement). Apply it so estimates match your invoice — it only affects sessions detected as Bedrock (`msg_bdrk_` message IDs):

```yaml
bedrock:
  discount: 0.30          # flat fraction off list for all Bedrock sessions (30% off)
  model_discount:         # optional per-model-family overrides (win over the flat rate)
    opus: 0.25
    sonnet: 0.35
```

Discounts are clamped to `[0, 1)` and scale every rate (input, output, cache write, cache read) uniformly. Web / Claude-Max / direct-API sessions are unaffected.

> **What this can't express:** a flat percentage can't model **provisioned throughput** (a fixed `$/hour` reserved-capacity commitment, not per-token billing) or **input/output-asymmetric** net rates. For those, pin exact net per-MTok rates directly via `model_pricing:` instead. Ask your AWS/Anthropic account manager for your effective per-model rate. Note also that Claude **Max** is a flat subscription with no per-token billing, so its `$` figure is an *API-equivalent* estimate, not an amount you are billed.

The TUI shows costs based on these rates. Press `$` to toggle between token counts and dollar amounts.

## Model metadata (context windows and pricing)

Two tiers feed the `CTX` (context window) and `$` (cost) columns for any
model id a backend reports (#473):

1. **Curated tables** — `history_reader.MODEL_CONTEXT_WINDOWS` and
   `pricing.MODEL_PRICING`. Every row cites a primary source or a live CLI
   figure. These always win.
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
| `overcode` | 🐙 | Overcode CLI commands reference |
| `delegating-to-agents` | 👥 | Parallel agent delegation |
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
- TUI "Available Skills" (ASK) column when viewing agents with `--full` detail
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
overcode monitor --session production

# Development work
overcode launch -n feature-dev -d ~/dev --session development
overcode monitor --session development
```

Each session has independent:
- Session state and history
- TUI preferences
- Daemon instances
