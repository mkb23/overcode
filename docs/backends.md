# Agent Backends

Overcode was built around Claude Code, but the pieces that know *which* CLI
is running now live behind one seam — an `AgentBackend` adapter that owns
that CLI's argv grammar, key gestures, pane chrome, and telemetry. Three
backends ship today:

| Backend name | CLI | Binary override |
|---|---|---|
| `claude-code` (default) | [Claude Code](https://claude.ai/claude-code) | `CLAUDE_COMMAND` |
| `opencode` | [opencode](https://opencode.ai) | `OPENCODE_COMMAND` |
| `codex` | [Codex CLI](https://github.com/openai/codex) | `CODEX_COMMAND` |

Everything below opencode's row is honest about maturity. opencode support
now covers the dashboard, hook-grade live status, previews, AI summaries,
send-instruction, restart, kill, resume, fork, and token/cost/context
columns. What it does not cover is the Claude-only subsystems — skills, the
sandbox badge, the subscription-usage widget, and agent teams — which stay
capability-gated and render as dashes or hidden controls.

**codex is Phase 2: launch, hook-grade live status (including
`waiting_approval`), resume, fork, and token/context columns.** Every
launch injects `overcode hook-handler` via per-launch `-c
'hooks.<Event>=[...]'` config overrides plus
`--dangerously-bypass-hook-trust` — no files written, nothing to install.
Cost is honest **only about its ceiling, not its number**: codex is
subscription/API billed with no local per-turn charge recorded, and there is
no codex-specific entry in `pricing.py`'s `MODEL_PRICING` table yet (no
reliable current price to put there) — but the cost column does not fall
back to a dash for an unpriced model anywhere in overcode, codex included.
`monitor_daemon.py`'s cost estimator (`settings.get_model_pricing`) falls
back to your configured *default* per-token price for any model it doesn't
recognize, so a codex agent's cost column shows a real, non-zero dollar
figure priced as if it were your default model — live-verified during Phase
2 smoke testing, and worth knowing rather than trusting at face value until
a real codex price lands in `MODEL_PRICING`. Devcontainer support is still
Phase 5 — that is the one thing left this document says "not yet" about for
codex.

## Feature support at a glance

The user-facing view: which overcode features work on which backend, with
the TUI key where one exists. Unsupported actions are grayed out or answer
with a clean "backend X does not support …" — never a crash.

| Feature | TUI key | claude-code | opencode | codex | Notes |
|---|---|---|---|---|---|
| Launch / new-agent modal | `n` | ✅ | ✅ | ✅ | Backend toggle in the modal; `-B opencode` / `-B codex` from the CLI |
| Kill | `x` | ✅ | ✅ | ✅ | |
| Restart (same conversation) | `R` | ✅ | ✅ | ✅ | opencode resumes via `--session <id>`; codex via `codex resume <id>` |
| Revive a terminated agent | — | ✅ | ✅ | ✅ | |
| Fork (branch conversation) | `F` | ✅ | ✅ | ✅ | opencode: `--session <id> --fork` creates a `(fork #1)` session; codex: `codex fork <id>` (subcommand, verified live) |
| Send instruction | `i` / `:` | ✅ | ✅ | ✅ | |
| Approve / reject gestures | `Enter` / `Escape` | ✅ | ✅ | ✅ | Key gestures are backend-resolved |
| Live hook-grade status | — | ✅ | ✅ | ✅ | codex: `-c 'hooks.<Event>=[...]'` + `--dangerously-bypass-hook-trust` injection, incl. `waiting_approval` |
| Detection-mode toggle | `K` | ✅ | ✅ | ✅ | Per-session dispatch picks hooks mode automatically when state files are fresh |
| Token / cost / context columns | — | ✅ | ✅ | ⚠️ tokens/context ✅, cost ⚠️ estimate only | codex: rollout-JSONL reader; cost has no local figure and no codex-specific `pricing.py` entry, so it shows your configured *default* model's rate applied to codex's real token counts — not a dash, but not to be trusted as accurate |
| AI summaries | `A` | ✅ | ✅ | ✅ | |
| Preview pane | `m` | ✅ | ✅ | ✅ | |
| Sleep mode / heartbeat | `z` / `H` | ✅ | ✅ | ✅ | |
| Remote agents via sisters | `N` | ✅ | ✅ | ✅ | Capabilities travel with the agent, so remote gating matches local |
| Devcontainer wrapper | — | ✅ | ✅ | ❌ Phase 5 | codex devcontainer install step not wired yet |
| Permission modes | — | ✅ full | ⚠️ approximate | ✅ full | codex: distinct flags for bypass/permissive/normal (see below) |
| `--allowed-tools` allowlist | — | ✅ | ❌ | ❌ | No opencode or codex flag exists; silently ignored |
| Skills | — | ✅ | ❌ | ❌ | |
| Sandbox badge | — | ✅ | ❌ | ❌ | Claude-only loopback probe |
| Subscription-usage widget | — | ✅ | ❌ | ❌ | Anthropic-only usage API |
| Agent teams | — | ✅ | ❌ | ❌ | Claude Code experimental feature |

---

## Launching an opencode agent

```bash
overcode launch -n my-agent --backend opencode -d ~/code/myproject
overcode launch -n my-agent -B opencode --model openai/gpt-4o-mini
```

`-B` is the short form (`-b` was already `--budget`).

In the TUI, press `n` for the new-agent modal — there is a **Backend**
toggle alongside Provider. From the CLI, `overcode show <name>` prints the
backend, and a **BKD** column appears in the dashboard **only** when the
fleet actually spans more than one backend, so a Claude-only user sees no
change.

To make opencode the default for new agents:

```yaml
# ~/.overcode/config.yaml
new_agent_defaults:
  backend: opencode
```

Children inherit their parent's backend unless you pass `--backend` or
`--no-inherit`.

### Models

opencode expects the fully-qualified `provider/model` form and overcode
passes `--model` through verbatim:

```bash
overcode launch -n oc -B opencode --model openai/gpt-4o-mini
overcode launch -n oc -B opencode --model anthropic/claude-sonnet-4-5
```

A bare model name (`sonnet`) will not resolve — that is Claude Code's
grammar, not opencode's.

---

## Launching a codex agent

```bash
overcode launch -n my-agent --backend codex -d ~/code/myproject
overcode launch -n my-agent -B codex --model gpt-5.6-sol
```

Same `-B` short form, same new-agent-modal toggle, same `overcode show`
backend line as opencode. codex is Phase 2: launch, resume, fork, kill,
restart, hooks-grade status (including `waiting_approval`), and
token/context columns all work; cost is a rough estimate, not dashes — see
the honesty note at the top of this document.

### Models

codex expects a bare model id and overcode passes `-m` through verbatim:

```bash
overcode launch -n cx -B codex --model gpt-5.6-sol
```

No provider prefix — that is opencode's grammar, not codex's.

---

## Support matrix

Capabilities are the `BackendCapability` flags each adapter declares;
overcode gates UI actions and telemetry off them.

| Capability | claude-code | opencode | codex | Notes |
|---|---|---|---|---|
| `RESUME` | ✅ | ✅ | ✅ | opencode: `--session <id>`; codex: `codex resume <id>` (subcommand) |
| `FORK` | ✅ | ✅ | ✅ | opencode: `--session <id> --fork` — **verified**, creates a `(fork #1)` session; codex: `codex fork <id>` — **verified live** |
| `SESSION_ID_PRESCRIPTION` | ✅ | ❌ | ❌ | opencode mints its own `ses_…` ids; codex has no `--session-id`-shaped flag for fresh launches. Both require discovery, not prescription |
| `HOOK_EVENTS` | ✅ | ✅ | ✅ | opencode: bundled telemetry plugin (below); codex: `-c 'hooks.<Event>=[...]'` + `--dangerously-bypass-hook-trust` injected on every launch (below) |
| `TRANSCRIPT_STATS` | ✅ | ✅ | ✅ | opencode: SQLite `session` table (below); codex: rollout-JSONL reader (below) — tokens/context populate accurately, cost is a rough estimate priced at your configured default model's rate (no local figure, no codex-specific `pricing.py` entry) |
| `PERMISSION_INJECTION` | ✅ | ❌ | ❌ | opencode v1.18.19 has no per-launch tool allowlist flag; codex's nearest concept is sandbox modes + `-c` config, not a tool allowlist |
| `SKILLS` | ✅ | ❌ | ❌ | opencode *does* have a `/skills` command, codex too — neither has overcode integration |
| `SANDBOX_PROBE` | ✅ | ❌ | ❌ | Claude-only loopback heuristic; codex has its own (unrelated) sandbox |
| `SUBSCRIPTION_USAGE` | ✅ | ❌ | ❌ | Anthropic-only usage API |
| `AGENT_TEAMS` | ✅ | ❌ | ❌ | Claude Code experimental feature |

## Flag mapping

| overcode concept | claude-code | opencode (v1.18.19) | codex (v0.150.1) |
|---|---|---|---|
| Binary | `claude` | `opencode` | `codex` |
| Bypass permissions | `--dangerously-skip-permissions` | `--auto` | `--dangerously-bypass-approvals-and-sandbox` |
| Permissive | `--permission-mode dontAsk` | `--auto` (approximate — see below) | `-a never --sandbox workspace-write` |
| Normal | (default) | opencode's own `permission` config | (default: `on-request` approval) |
| Allowed tools | `--allowedTools a,b` | ✗ no flag exists | ✗ no flag exists |
| Model | `--model sonnet` | `--model provider/model` | `-m <model>` (bare id, e.g. `gpt-5.6-sol`) |
| Persona | `--agent name` | `--agent name` | ✗ (`-p/--profile` is a config-layer override, not a persona flag) |
| Prescribe session id | `--session-id <uuid>` | ✗ | ✗ |
| Resume | `--resume <id>` | `--session <id>` | `codex resume <id>` (subcommand, options after) |
| Fork | `--resume <id> --fork-session` | `--session <id> --fork` | `codex fork <id>` (subcommand, options after) |
| Telemetry injection | `--settings '<json>'` hooks | `.opencode/plugins/overcode-telemetry.js` | `-c 'hooks.<Event>=[...]'` × 8 + `--dangerously-bypass-hook-trust` |
| Stats source | `~/.claude/projects/**.jsonl` | SQLite `~/.local/share/opencode/opencode.db` | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` |
| Graceful exit | `C-c`, then `/exit` | `Escape` ×2, then `/exit` | `Escape`, then `/quit` |
| Bare `C-c` | safe | kills the process | **kills the process instantly, no confirmation** |
| Clear conversation | `/clear` | `/new` | `/new` |
| Approve | `Enter` | `Enter` (confirms the preselected *Allow once*) | `Enter` (confirms the preselected *Yes, proceed*) |
| Reject | `Escape` | `Escape` | `Escape` (no literal reject key) |
| Trust-folder dialog | "I trust this folder" | none | "Do you trust the contents of this directory?" — `Enter` accepts |

### Permission modes are approximate

overcode has three modes; opencode has one flag.

- **normal** → no flag. opencode asks according to its own `permission`
  config in `opencode.json` / `~/.config/opencode/opencode.jsonc`.
- **permissive** → `--auto`.
- **bypass** → `--auto`.

`--auto` auto-approves anything not explicitly *denied*, so opencode's
`"deny"` rules still win — it is closer to Claude's `dontAsk` than to
`--dangerously-skip-permissions`. Both overcode modes collapse onto it,
which means **permissive and bypass behave identically on opencode**. If
you need finer control, put it in the project's `opencode.json`:

```json
{ "permission": { "bash": "ask", "edit": "allow" } }
```

`--allowed-tools` is silently ignored for opencode: the `--permissions`
flag the design research expected does not exist in v1.18.19, and emitting
it would fail the launch outright.

### codex's permission modes are exact, not approximate

Unlike opencode, codex has a distinct flag for each of overcode's three
modes:

- **normal** → no flag. codex asks per its default `on-request` approval
  policy (only commands that reach outside the sandboxed workspace prompt).
- **permissive** → `-a never --sandbox workspace-write`. No approval dialog,
  but the sandbox itself still silently blocks out-of-workspace writes
  (reported back to the model, not to you).
- **bypass** → `--dangerously-bypass-approvals-and-sandbox`. Zero prompts,
  full filesystem access — codex's own banner shows `sandbox:
  danger-full-access` under this flag.

`--allowed-tools` is silently ignored for codex too: there is no
`--allowedTools`-shaped flag, and the nearest concepts (sandbox modes, `-c
key=value` config overrides) are not a tool allowlist.

---

## Telemetry: the bundled opencode plugin

Claude Code lets overcode inject hooks on the command line (`--settings`).
opencode has no such flag, but it does have a plugin system, so overcode
ships one:

```
src/overcode/opencode_plugin/overcode-telemetry.js
```

At launch (and on every restart/revive/fork) the opencode backend copies
that file to:

```
<start_directory>/.opencode/plugins/overcode-telemetry.js
```

The plugin subscribes to opencode's event bus and writes the *same*
`hook_state_<agent>.json` / `hook_events_<agent>.jsonl` files Claude Code's
hooks produce, so `HookStatusDetector` — obligation badges, foreground
classification, the status-detail column — works unchanged.

| opencode signal | overcode hook event | Status |
|---|---|---|
| `chat.message` (user) / `message.updated` role=user | `UserPromptSubmit` | running |
| `tool.execute.before` | `PreToolUse` | running |
| `permission.asked` | `PermissionRequest` | **waiting_approval** |
| `permission.replied` (allow) | `PreToolUse` | running |
| `permission.replied` (reject) | `PostToolUse` | running |
| `tool.execute.after` | `PostToolUse` | running |
| `session.idle` | `Stop` | waiting_user |
| `session.error` | `StopFailure` | error |
| `session.deleted` | `SessionEnd` | terminated |

opencode's lowercase tool names (`bash`, `read`, `webfetch`) are mapped onto
Claude's taxonomy (`Bash`, `Read`, `WebFetch`) so the detector's Bash-command
activity strings and sleep detection keep working.

### Things worth knowing about the footprint

- **The plugin file is visible to git.** It lands in your project as an
  untracked `.opencode/plugins/overcode-telemetry.js`. Overcode does **not**
  edit your `.gitignore` or `.git/info/exclude` — that is your repository's
  business. Add `.opencode/plugins/overcode-telemetry.js` to either one if
  you want it hidden. Committing it is also fine.
- **It is inert outside overcode.** The plugin registers *no hooks at all*
  unless `OVERCODE_SESSION_NAME` and `OVERCODE_TMUX_SESSION` are both in the
  environment. Your own `opencode` runs in that directory are unaffected.
- **Your own file is never clobbered.** Overcode only rewrites the file if it
  still carries the `OVERCODE-PLUGIN-MARKER` line. Replace the contents and
  overcode leaves it alone permanently.
- **It is not removed when the agent dies.** Re-ensuring on the next launch is
  cheaper and safer than a teardown race. Delete it whenever you like;
  overcode recreates it.
- **Project-local, never global.** Registering the plugin in
  `~/.config/opencode/` would load overcode's telemetry into every opencode
  session you ever run. A project copy is scoped to the directory overcode
  launched in.
- **A plugin module may export only its factory.** opencode calls *every*
  export as a plugin factory; an exported helper crashes the whole opencode
  process at load time. If you fork the plugin, keep the single-export shape.

`overcode doctor` reports `missing-settings` for an opencode agent whose
project directory has no plugin — that agent is running on pane polling.

---

## Telemetry: codex hook injection

Codex CLI ships a stable, enabled-by-default hooks system with 12 events,
resolved by openai/codex source + a live-verified Phase 0 probe
(`docs/design/agent-backends-codex-grok.md` Appendix A). Unlike opencode,
codex needs no plugin file and no project-directory write at all: every
launch passes one `-c` config override per event plus a trust-bypass flag:

```
-c 'hooks.UserPromptSubmit=[{hooks=[{type="command",command="<overcode-bin> hook-handler"}]}]'
-c 'hooks.PreToolUse=[...]'
-c 'hooks.PostToolUse=[...]'
-c 'hooks.PermissionRequest=[...]'
-c 'hooks.Stop=[...]'
-c 'hooks.Interrupt=[...]'
-c 'hooks.SessionStart=[...]'
-c 'hooks.SessionEnd=[...]'
--dangerously-bypass-hook-trust
```

`<overcode-bin>` is resolved the same way Claude Code's `--settings`
injection resolves it (`shutil.which("overcode")`, falling back to `python -m
overcode.cli`), so the hook subprocess finds overcode regardless of how it
was installed. `command` is a **bare shell string**, not an array — codex's
`HookHandlerConfig::Command` type differs from Claude's `command: [str,
...]` here, and the array form fails to parse.

**Why `--dangerously-bypass-hook-trust` is safe here.** Without it, an
identical `-c 'hooks...'` override fires zero hooks, silently — codex's hook
trust model normally requires a one-time interactive review (`t` to trust in
the TUI), which durably writes a `[hooks.state...]` entry into your *global*
`~/.codex/config.toml`. The bypass flag skips that review for the process
overcode itself launches, with **zero file writes anywhere** — cleaner than
the interactive-trust alternative, not just faster. The command it registers
is `overcode hook-handler` (or the venv-qualified equivalent) — a command
overcode wrote and vets itself, on a process overcode itself started; the
flag never touches a codex session you launch by hand outside overcode.

`overcode doctor` reports `missing-settings` for a codex agent whose argv
lacks `--dangerously-bypass-hook-trust` — see the doctor section below.

### Event mapping and the Interrupt/SessionStart additions

Codex's hook stdin is snake_case and Claude-shaped already
(`hook_event_name`, `session_id`, `tool_name`, `permission_mode`, ...), so
`overcode hook-handler` needs no dialect translation for it — see
`hook_handler._normalize_hook_payload()`, the same call site Grok's
camelCase dialect will extend later.

| codex event | overcode hook event | Status |
|---|---|---|
| `UserPromptSubmit` | `UserPromptSubmit` | running |
| `PreToolUse` / `PostToolUse` | `PreToolUse` / `PostToolUse` | running |
| `PermissionRequest` | `PermissionRequest` | **waiting_approval** |
| `Stop` | `Stop` | waiting_user |
| `Interrupt` | `Interrupt` | waiting_user |
| `SessionStart` | `SessionStart` (+ records `session_id`) | waiting_user |
| `SessionEnd` | `SessionEnd` | terminated |

Two events here have no Claude Code analogue and are registered only for
codex (`hook_handler.CODEX_HOOK_EVENTS`, never added to `OVERCODE_HOOKS`,
which is what Claude's `--settings` injection reads — Claude never sends
either one):

- **`Interrupt`** fires when the user hits Escape mid-turn. Claude Code
  prints no Stop/SessionEnd hook on interrupt at all, so overcode has to
  scrape the pane for an "interrupted" marker to downgrade a stuck
  `running`; codex's hook stdin says so directly, so the event→status map
  alone does the downgrade — no pane read needed.
- **`SessionStart`** is how overcode learns the codex session id. Codex has
  no `--session-id`-shaped flag, so — unlike Claude, which prescribes the id
  up front — this hook event is the *only* channel; its `session_id` field
  is folded into `hook_state_<agent>.json`'s `agent_session_ids` /
  `agent_session_id`, which is what makes restart-resume and fork target the
  right conversation and is also `CodexStatsReader`'s primary lookup key
  (below).

---

## Stats: the SQLite session store

`OpencodeStatsReader` (`src/overcode/backends/opencode_stats.py`) reads
opencode's store read-only (`file:…?mode=ro`, short busy timeout — it never
writes, and never blocks the daemon tick). The path comes from `OPENCODE_DB`,
then `OPENCODE_DATA_DIR`, then `$XDG_DATA_HOME/opencode/opencode.db`, then
`~/.local/share/opencode/opencode.db`.

| overcode column | opencode source |
|---|---|
| input tokens | `session.tokens_input` |
| output tokens | `session.tokens_output` + `session.tokens_reasoning` |
| cache write / read | `session.tokens_cache_write` / `tokens_cache_read` |
| cost | `session.cost` (what the provider actually charged); recomputed from `pricing.py` when it is 0, which is the subscription-auth case |
| model | `session.model` JSON, rendered back as `provider/model` |
| context | newest assistant `message.data.tokens.total` |
| interactions | count of `message.data.role == "user"` |

Rows are located by the opencode session ids the plugin recorded into the
hook state (`agent_session_id` / `agent_session_ids`) — the exact analogue of
Claude's prescribed `--session-id`. Without the plugin it falls back to
matching `session.directory` against the agent's working directory within its
launch-time window, ignoring child (`task`) sessions.

Any failure — no database, a lock, a renamed column — returns "unknown", so
the columns render dashes rather than misleading zeros. Schema drift also
raises an `overcode doctor` warning naming the missing columns.

---

## Stats: the rollout JSONL (codex)

`CodexStatsReader` (`src/overcode/backends/codex_stats.py`) reads codex's
own transcript format — one append-only JSONL file per conversation at
`~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-ts>-<uuid>.jsonl` (`CODEX_HOME`
honoured, defaulting to `~/.codex`) — read-only, one pass per lookup, never
writing.

| overcode column | codex source |
|---|---|
| input tokens | latest `event_msg` (`payload.type=="token_count"`) `info.total_token_usage.input_tokens` |
| output tokens | same event's `output_tokens` + `reasoning_output_tokens` |
| cache write / read | same event's `cache_write_input_tokens` / `cached_input_tokens` |
| context | same event's `total_tokens` (a running total — latest event wins, not summed) |
| model | latest `turn_context.payload.model` (one line per turn) |
| interactions | `response_item` messages where `role=="user"` **and** `internal_chat_message_metadata_passthrough.content_item_kinds` contains `"user.text"` — excludes injected `<environment_context>`/skills/permissions scaffolding, which carries its own kind tags instead |
| cost | **not a dash — a rough, possibly-wrong estimate.** codex is subscription/API billed with no local per-turn charge (matches Claude's transcript, which also carries none), and `pricing.py`'s `MODEL_PRICING` table has no codex-specific entry (no current price overcode trusts). Recomputation is still the normal fallback for *every* backend when a model isn't in `MODEL_PRICING`, and — live-verified during Phase 2 smoke testing — that fallback is your configured *default* per-token price (`settings.get_model_pricing`), not zero/None. A codex agent's cost column therefore shows a real dollar figure, priced as if its tokens cost what your default model costs; treat it as a placeholder until a real codex price is added, not as billing-accurate |

Rows are located by the codex session id `SessionStart`'s hook recorded into
`hook_state_<agent>.json` (`agent_session_id` / `agent_session_ids` — the
exact field names and mechanism opencode's plugin also writes, since both
ride the same `hook_handler.write_hook_state()` code path). Without a
recorded id — hooks never fired, or the state file predates this phase — it
falls back to matching `session_meta.cwd` against the agent's working
directory within a few calendar days of its launch time, the same
directory+time fallback shape `OpencodeStatsReader` uses, bounded so it never
scans the user's entire session history.

Any failure — missing directory, a corrupt line, an unreadable file —
returns "unknown", so the columns render dashes rather than misleading
zeros. A `token_count` event missing expected keys raises an `overcode
doctor` warning naming them, checked against the most recently modified
rollout file rather than every session ever recorded.

---

## Status detection (polling fallback)

When the plugin is absent, status comes from **pane polling** — overcode
reads the rendered TUI. The pattern set lives in
`src/overcode/backends/opencode.py` (`OPENCODE_PATTERNS`) and is grounded in
a committed corpus of real captures at
`tests/fixtures_opencode_panes/`, replayed by
`tests/unit/test_status_detector_opencode.py`.

The signals that matter:

| overcode status | opencode chrome |
|---|---|
| `running` | `esc interrupt` (or `esc again to interrupt`) in the bottom bar, plus an animating `⬝ / ■` spinner |
| `waiting_user` (permission) | `△ Permission required` box with `Allow once / Allow always / Reject` |
| `waiting_user` (idle) | input box `┃` gutter with no interrupt hint; hint bar reads `tab agents  ctrl+p commands` |
| `terminated` | shell prompt, none of the above |

Known rough edges, honestly:

- **Errors read as `waiting_user`, not `error`.** opencode renders provider
  failures as plain prose in a red-bordered box using the same `┃` gutter as
  everything else, and overcode strips ANSI before matching — the colour is
  the only structural signal and it is gone by then. A short list of known
  message texts (`Incorrect API key provided`, `ECONNREFUSED`, …) is matched,
  but the general case degrades to "stopped, needs you".
- **The pristine banner screen reports a vague activity string.** On a fresh
  launch opencode centres its input box with blank filler beneath, so the
  bottom-of-pane slice the detector reads contains only the tip line and the
  info bar. The *status* is right (`waiting_user`); the one-line activity
  text is just less useful until the first turn.
- **Finished tool calls are not treated as work.** `→ Read README.md` and
  `✱ Glob "*"` stay on screen after a turn ends, so matching them would
  report a settled agent as running. Tool-execution detection is therefore
  disabled for opencode; `esc interrupt` covers the in-flight case.
- **UNVERIFIED: thinking/reasoning chrome.** No reasoning-capable model was
  driven during corpus capture, so the thinking markers are empty rather
  than guessed.
- **Interrupts are detected from a status-pill suffix.** Pressing Escape
  twice mid-generation rewrites the assistant turn's footer from
  `▣  Build · GPT-4o mini` to `▣  Build · GPT-4o mini · interrupted`, and
  leaves it there. `· interrupted` is what the hook detector matches to
  downgrade a stuck `running` to `waiting_user`; the busy hint
  (`esc again to interrupt`) never contains it. Captured live in Phase 6 —
  see `tests/fixtures_opencode_panes/interrupted.txt`.

---

## codex: hooks-grade status, pane polling as fallback

As of Phase 2, a codex agent whose hook injection fired gets the same
hooks-grade status Claude Code and opencode get — including the
`waiting_approval` distinction pane polling cannot make. Per-session
dispatch (`status_detector_factory.py`) picks hooks mode automatically once
`hook_state_<agent>.json` exists and is fresh; nothing to configure. **Pane
polling is still the fallback** for the gap between process start and the
first hook firing, and for a codex agent whose hook injection was stripped
out of its argv some other way (a manual relaunch outside overcode, for
instance). The pattern set lives in `src/overcode/backends/codex.py`
(`CODEX_PATTERNS`), grounded in a committed corpus of real Codex CLI
v0.150.1 captures at `tests/fixtures_codex_panes/`, replayed by
`tests/unit/test_status_detector_codex.py`.

The polling-mode signals that matter:

| overcode status | codex chrome |
|---|---|
| `running` | `esc to interrupt` in the `• Working (Ns • esc to interrupt)` spinner line |
| `waiting_user` (permission) | `Would you like to run the following command?` / `Yes, proceed` / `Press enter to confirm or esc to cancel` |
| `waiting_user` (idle) | `› Ask Codex to do anything` placeholder — codex never draws a bare prompt glyph, so idle detection matches this literal placeholder text rather than an empty gutter |
| `terminated` | shell prompt, none of the above |

Known rough edges of the **polling fallback**, honestly (hooks mode does not
have these — permission dialogs distinguish `waiting_approval` there):

- **No `waiting_approval` under polling.** Permission dialogs read as
  `waiting_user`, identically to an idle prompt, when a codex agent is
  running on the polling fallback rather than hooks. The `overcode send
  <name> approve` gesture still works either way.
- **Bad-model errors read as `waiting_user`, not `error`.** codex recovers a
  turn-level failure (e.g. an unsupported model id) on its own, settling
  right back at the ready prompt in the same frame it shows the error JSON —
  see `tests/fixtures_codex_panes/error_bad_model.txt`. Matching the error
  text would misreport a settled agent as stuck.
- **Finished tool/status lines are not treated as work.** `• Ran curl ...`
  and `■ Conversation interrupted ...` stay on screen after they happen, so
  tool-execution detection is disabled the same way it is for opencode; the
  spinner's `esc to interrupt` covers the in-flight case.
- **UNVERIFIED: thinking/reasoning chrome and the slash-command menu.**
  Neither was captured in the Phase 0 corpus, so their pattern fields are
  left empty rather than guessed.
- **`C-c` kills codex instantly, no confirmation** — the same lesson opencode
  taught, repeating on a second backend. overcode never sends it; the safe
  interrupt is a single `Escape`.

---

## Doctor checks

`overcode doctor` adds three opencode-specific checks, and only when the
fleet actually contains an opencode agent:

1. **Version range.** opencode ships every 2-3 days and overcode reads its
   on-screen chrome, so a version outside the tested range earns a warning.
   The range lives in `TESTED_OPENCODE_RANGE` in
   `src/overcode/backends/opencode.py` (currently `>=1.18.0, <2.0.0`).
2. **Autoupdate.** If `~/.config/opencode/opencode.json[c]` has
   `"autoupdate": true`, overcode warns — an unattended upgrade can move the
   TUI out from under the pattern set. Silent when there is no config or it
   doesn't mention the setting.
3. **Schema drift.** A renamed column in opencode's SQLite store blanks the
   token/cost columns; doctor names the missing columns rather than leaving
   you to guess.

Per-agent, the health verdict for an opencode session is "is there a live
`opencode` process under the pane, and is the telemetry plugin in its project
directory?". A missing plugin reports `missing-settings`, the same verdict
Claude Code gets when it is running without injected hooks.

codex gets two checks of its own, gated on the fleet containing a codex
agent:

1. **Version range.** codex ships even faster than opencode — multiple
   releases a week during Phase 0 verification. The range lives in
   `TESTED_CODEX_RANGE` in `src/overcode/backends/codex.py` (currently
   `>=0.148.0, <1.0.0`).
2. **Auto-update, unconditionally.** `codex features list` reports
   `in_app_updates stable true` (enabled by default) with no config toggle
   found to disable it during Phase 0 verification, so this warning is
   always surfaced rather than read from a config file that doesn't exist.
3. **Rollout JSONL schema drift.** A codex upgrade that renames a
   `token_count` usage field blanks the token/cost/context columns; doctor
   names the missing keys (checked against the most recently modified
   rollout file) rather than leaving you to guess.

Per-agent, the health verdict for a codex session checks argv for
`--dangerously-bypass-hook-trust` — present means hooks are injected and
reads `ok`; absent reports `missing-settings`, the same verdict Claude Code
gets when it is running without `--settings` and opencode gets when its
telemetry plugin is missing. `overcode restart` re-injects it.

---

## Supervising an opencode or codex agent

The supervisor's own meta-agent stays Claude Code, but its gestures are
backend-resolved:

```bash
overcode send <name> approve   # opencode: confirms "Allow once"; codex: confirms "Yes, proceed"
overcode send <name> reject    # Escape — dismisses, abandoning the tool call
```

These are *gestures*, not keys: overcode asks the agent's backend which keys
its permission dialog wants. Prefer them over the raw `overcode send <name>
enter` / `escape`, which still exist and still send literal keys. Supervisor
context lines name a non-default backend (`Backend: opencode` /
`Backend: codex`) so the supervisor knows not to send Claude slash commands
at it — codex's clear-conversation gesture is `/new`, not `/clear`, and its
graceful exit is `/quit`, not `/exit`.

---

## Containers: the devcontainer wrapper

`--wrapper devcontainer` works for claude-code and opencode today. The
launcher exports `OVERCODE_BACKEND` into the wrapper's environment for any
non-default backend (Claude Code leaves it unset, so the wrapper's behaviour
there is byte-for-byte what it was before), and the wrapper keys its install
step off it:

| `OVERCODE_BACKEND` | installs | `overcode hooks install` |
|---|---|---|
| unset / `claude-code` | `npm i -g @anthropic-ai/claude-code` | yes |
| `opencode` | `npm i -g opencode-ai@latest` | skipped — no settings.json hook protocol |
| `codex` | ❌ not wired yet (Phase 5) | — |

opencode's telemetry still reaches the host: the plugin is staged into the
project directory, which is bind-mounted as `/workspace`, and the hook-state
exchange directory is already mounted at `/overcode-state`. Provider
credentials present in your shell (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`OPENROUTER_API_KEY`, `GEMINI_API_KEY`) are forwarded into the container.
codex's devcontainer install case (`npm i -g @openai/codex`) and its
`~/.codex/auth.json` mounting story land in Phase 5.

---

## Mixed fleets across machines

Sisters publish each agent's backend **and** its serialized capability list
in `SessionDaemonState` (`backend`, `backend_capabilities`), which rides the
existing raw daemon-state passthrough. A TUI therefore gates remote actions
on what the *remote* backend can do — including backends the local build has
never heard of — and grays out, for example, fork on a backend without it.

A sister running a version older than this reports neither field. Those
agents are read as `claude-code` with the full capability set, which is what
they effectively were.

---

## Naming

The launch flag for passing raw CLI arguments through is `--backend-arg`.
`--claude-arg` is still accepted as a hidden deprecated alias.

Internally the Claude-flavoured names were renamed in Phase 6 with
backward-compatible aliases on every public surface — `ClaudeLauncher` →
`AgentLauncher`, `Session.claude_session_ids` → `agent_session_ids`,
`Session.active_claude_session_id` → `active_agent_session_id`,
`Session.extra_claude_args` → `extra_cli_args`, `Session.claude_agent` →
`agent_persona`, `ClaudeNotFoundError` → `AgentCliNotFoundError`,
`ClaudeSessionStats` → `AgentSessionStats`. Persisted `sessions.json` is read
under both key sets and written under both for one release, so downgrading is
safe. Deliberately *not* renamed: hook-state file keys, `OVERCODE_*` env vars,
`CLAUDE_COMMAND` (the mock-harness contract), supervisor-daemon internals, and
web API response keys.

---

## Adding another backend

Implement the `AgentBackend` protocol in `src/overcode/backends/base.py`,
register it in `src/overcode/backends/__init__.py`, and supply a
`StatusPatterns` instance built from a captured pane corpus. Declare only
the capabilities you actually support — every gated feature degrades to a
dash or a clean "backend X does not support fork" rather than a crash. The
codex backend (`src/overcode/backends/codex.py`) is worth reading alongside
opencode's for a second telemetry-injection shape: it started with only
`RESUME` and `FORK` declared (Phase 1) and added `HOOK_EVENTS` +
`TRANSCRIPT_STATS` once its hook injection and `codex_stats.py` landed
(Phase 2) — a capability set is a launch-time floor, not a permanent one.
