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
```

These apply to agents created via both the CLI (`overcode launch`) and the TUI (`n` key). CLI flags override config defaults, and for child agents the parent's settings take precedence over config defaults (#433): explicit flag > parent setting > config default > built-in default. Use `--no-inherit` to skip the parent.

Setting `wrapper: devcontainer` makes all new agents launch inside a Docker container by default. See the [Wrappers Guide](wrappers.md) for details.

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
~/.overcode/sessions/{session}/
├── sessions.json              # Active sessions list
├── {agent-id}.json           # Individual agent state
├── agent_status_history.csv  # Status timeline
├── monitor_daemon_state.json # Current metrics
├── monitor_daemon.pid        # Monitor process ID
├── supervisor_daemon.pid     # Supervisor process ID
├── supervisor_stats.json     # Supervisor token tracking
└── tui_preferences.json      # TUI settings
```

### Global Data
```
~/.overcode/
├── config.yaml      # User configuration
├── presets.json     # Standing instruction presets
└── presence_log.csv # User presence tracking (macOS)
```

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
