# Agent Backends

Overcode was built around Claude Code, but the pieces that know *which* CLI
is running now live behind one seam — an `AgentBackend` adapter that owns
that CLI's argv grammar, key gestures, pane chrome, and telemetry. Two
backends ship today:

| Backend name | CLI | Binary override |
|---|---|---|
| `claude-code` (default) | [Claude Code](https://claude.ai/claude-code) | `CLAUDE_COMMAND` |
| `opencode` | [opencode](https://opencode.ai) | `OPENCODE_COMMAND` |

Everything below opencode's row is honest about maturity. opencode support
now covers the dashboard, hook-grade live status, previews, AI summaries,
send-instruction, restart, kill, resume, fork, and token/cost/context
columns. What it does not cover is the Claude-only subsystems — skills, the
sandbox badge, the subscription-usage widget, and agent teams — which stay
capability-gated and render as dashes or hidden controls.

## Feature support at a glance

The user-facing view: which overcode features work on which backend, with
the TUI key where one exists. Unsupported actions are grayed out or answer
with a clean "backend X does not support …" — never a crash.

| Feature | TUI key | claude-code | opencode | Notes |
|---|---|---|---|---|
| Launch / new-agent modal | `n` | ✅ | ✅ | Backend toggle in the modal; `-B opencode` from the CLI |
| Kill | `x` | ✅ | ✅ | |
| Restart (same conversation) | `R` | ✅ | ✅ | opencode resumes via `--session <id>` |
| Revive a terminated agent | — | ✅ | ✅ | |
| Fork (branch conversation) | `F` | ✅ | ✅ | Verified on opencode: `--session <id> --fork` creates a `(fork #1)` session |
| Send instruction | `i` / `:` | ✅ | ✅ | |
| Approve / reject gestures | `Enter` / `Escape` | ✅ | ✅ | Key gestures are backend-resolved |
| Live hook-grade status | — | ✅ | ✅ | opencode: bundled telemetry plugin; pane polling as fallback |
| Detection-mode toggle | `K` | ✅ | ✅ | |
| Token / cost / context columns | — | ✅ | ✅ | opencode: read from its SQLite session store |
| AI summaries | `A` | ✅ | ✅ | |
| Preview pane | `m` | ✅ | ✅ | |
| Sleep mode / heartbeat | `z` / `H` | ✅ | ✅ | |
| Remote agents via sisters | `N` | ✅ | ✅ | Capabilities travel with the agent, so remote gating matches local |
| Devcontainer wrapper | — | ✅ | ✅ | `--wrapper devcontainer` installs the right CLI |
| Permission modes | — | ✅ full | ⚠️ approximate | permissive and bypass both map to `--auto` (see below) |
| `--allowed-tools` allowlist | — | ✅ | ❌ | No opencode flag exists; silently ignored |
| Skills | — | ✅ | ❌ | |
| Sandbox badge | — | ✅ | ❌ | Claude-only loopback probe |
| Subscription-usage widget | — | ✅ | ❌ | Anthropic-only usage API |
| Agent teams | — | ✅ | ❌ | Claude Code experimental feature |

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

## Support matrix

Capabilities are the `BackendCapability` flags each adapter declares;
overcode gates UI actions and telemetry off them.

| Capability | claude-code | opencode | Notes |
|---|---|---|---|
| `RESUME` | ✅ | ✅ | opencode: `--session <id>` |
| `FORK` | ✅ | ✅ | opencode: `--session <id> --fork` — **verified**, creates a `(fork #1)` session |
| `SESSION_ID_PRESCRIPTION` | ✅ | ❌ | opencode mints its own `ses_…` ids; overcode must discover, not prescribe |
| `HOOK_EVENTS` | ✅ | ✅ | opencode: bundled telemetry plugin (below); pane polling remains the fallback |
| `TRANSCRIPT_STATS` | ✅ | ✅ | opencode: SQLite `session` table (below) |
| `PERMISSION_INJECTION` | ✅ | ❌ | opencode v1.18.19 has no per-launch tool allowlist flag |
| `SKILLS` | ✅ | ❌ | opencode *does* have a `/skills` command, but no overcode integration |
| `SANDBOX_PROBE` | ✅ | ❌ | Claude-only loopback heuristic |
| `SUBSCRIPTION_USAGE` | ✅ | ❌ | Anthropic-only usage API |
| `AGENT_TEAMS` | ✅ | ❌ | Claude Code experimental feature |

## Flag mapping

| overcode concept | claude-code | opencode (v1.18.19) |
|---|---|---|
| Binary | `claude` | `opencode` |
| Bypass permissions | `--dangerously-skip-permissions` | `--auto` |
| Permissive | `--permission-mode dontAsk` | `--auto` (approximate — see below) |
| Allowed tools | `--allowedTools a,b` | ✗ no flag exists |
| Model | `--model sonnet` | `--model provider/model` |
| Persona | `--agent name` | `--agent name` |
| Prescribe session id | `--session-id <uuid>` | ✗ |
| Resume | `--resume <id>` | `--session <id>` |
| Fork | `--resume <id> --fork-session` | `--session <id> --fork` |
| Telemetry injection | `--settings '<json>'` hooks | `.opencode/plugins/overcode-telemetry.js` |
| Stats source | `~/.claude/projects/**.jsonl` | SQLite `~/.local/share/opencode/opencode.db` |
| Graceful exit | `C-c`, then `/exit` | `Escape` ×2, then `/exit` |
| Clear conversation | `/clear` | `/new` |
| Approve | `Enter` | `Enter` (confirms the preselected *Allow once*) |
| Reject | `Escape` | `Escape` |
| Trust-folder dialog | "I trust this folder" | none |

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

---

## Supervising an opencode agent

The supervisor's own meta-agent stays Claude Code, but its gestures are
backend-resolved:

```bash
overcode send <name> approve   # confirms the preselected "Allow once"
overcode send <name> reject    # Escape — dismisses, abandoning the tool call
```

These are *gestures*, not keys: overcode asks the agent's backend which keys
its permission dialog wants. Prefer them over the raw `overcode send <name>
enter` / `escape`, which still exist and still send literal keys. Supervisor
context lines name a non-default backend (`Backend: opencode`) so the
supervisor knows not to send Claude slash commands at it.

---

## Containers: the devcontainer wrapper

`--wrapper devcontainer` works for either backend. The launcher exports
`OVERCODE_BACKEND` into the wrapper's environment for any non-default
backend (Claude Code leaves it unset, so the wrapper's behaviour there is
byte-for-byte what it was before), and the wrapper keys its install step
off it:

| `OVERCODE_BACKEND` | installs | `overcode hooks install` |
|---|---|---|
| unset / `claude-code` | `npm i -g @anthropic-ai/claude-code` | yes |
| `opencode` | `npm i -g opencode-ai@latest` | skipped — no settings.json hook protocol |

opencode's telemetry still reaches the host: the plugin is staged into the
project directory, which is bind-mounted as `/workspace`, and the hook-state
exchange directory is already mounted at `/overcode-state`. Provider
credentials present in your shell (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`OPENROUTER_API_KEY`, `GEMINI_API_KEY`) are forwarded into the container.

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

## Adding a third backend

Implement the `AgentBackend` protocol in `src/overcode/backends/base.py`,
register it in `src/overcode/backends/__init__.py`, and supply a
`StatusPatterns` instance built from a captured pane corpus. Declare only
the capabilities you actually support — every gated feature degrades to a
dash or a clean "backend X does not support fork" rather than a crash.
