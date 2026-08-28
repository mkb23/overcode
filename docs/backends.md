# Agent Backends

Overcode was built around Claude Code, but the pieces that know *which* CLI
is running now live behind one seam — an `AgentBackend` adapter that owns
that CLI's argv grammar, key gestures, pane chrome, and telemetry. Four
backends ship today:

| Backend name | CLI | Binary override |
|---|---|---|
| `claude-code` (default) | [Claude Code](https://claude.ai/claude-code) | `CLAUDE_COMMAND` |
| `opencode` | [opencode](https://opencode.ai) | `OPENCODE_COMMAND` |
| `codex` | [Codex CLI](https://github.com/openai/codex) | `CODEX_COMMAND` |
| `grok` | [Grok Build](https://x.ai) | `GROK_COMMAND` |

Everything below opencode's row is honest about maturity. opencode support
now covers the dashboard, hook-grade live status, previews, AI summaries,
send-instruction, restart, kill, resume, fork, and token/cost/context
columns. What it does not cover is the Claude-only subsystems — skills, the
sandbox badge, the subscription-usage widget, and agent teams — which stay
capability-gated and render as dashes or hidden controls.

**codex: launch, hook-grade live status (including `waiting_approval`),
resume, fork, devcontainer support, and token/context columns.** Every
launch injects `overcode hook-handler` via per-launch `-c
'hooks.<Event>=[...]'` config overrides plus
`--dangerously-bypass-hook-trust` — no files written, nothing to install.
Cost is honest **about both its ceiling and, now, a real list-price
estimate**: codex is subscription/API billed with no local per-turn charge
recorded (matching Claude's own transcript, which also carries none), but
`pricing.py`'s `MODEL_PRICING` table now has a `gpt-5.6-sol` entry (codex's
account-default model — see the pricing section below for sourcing and
caveats), so the cost column prices real codex token counts at that model's
actual published rate rather than falling back to your configured *default*
model's rate the way it did before that entry existed. It is still an
estimate, not a billed figure — codex has no local per-turn charge to
compare against, unlike grok.

**grok: launch, hook-grade live status (including `waiting_approval`),
resume, fork, session-id prescription, a permission allowlist,
devcontainer support (install only — see the containers section for the
auth caveat), and token/cost/context columns.** A global,
inert-outside-overcode hooks file (`~/.grok/hooks/overcode.json`) delivers
hook-grade status, and `GrokStatsReader` reads a genuinely billing-accurate
token/cost/context split from `updates.jsonl` — unlike codex's cost column,
grok's is not an estimate; `pricing.py` now also carries `grok-4.6`/
`grok-4.5` entries as the *fallback* path for when that local figure is
unavailable (see the pricing section below). grok also requires a SuperGrok
or X Premium+ subscription, which `overcode doctor` checks for (below)
rather than assuming.

## Feature support at a glance

The user-facing view: which overcode features work on which backend, with
the TUI key where one exists. Unsupported actions are grayed out or answer
with a clean "backend X does not support …" — never a crash.

| Feature | TUI key | claude-code | opencode | codex | grok | Notes |
|---|---|---|---|---|---|---|
| Launch / new-agent modal | `n` | ✅ | ✅ | ✅ | ✅ | Backend toggle in the modal; `-B opencode` / `-B codex` / `-B grok` from the CLI |
| Kill | `x` | ✅ | ✅ | ✅ | ✅ | |
| Restart (same conversation) | `R` | ✅ | ✅ | ✅ | ✅ | opencode resumes via `--session <id>`; codex via `codex resume <id>`; grok via `--resume <id>` |
| Revive a terminated agent | — | ✅ | ✅ | ✅ | ✅ | |
| Fork (branch conversation) | `F` | ✅ | ✅ | ✅ | ✅ | opencode: `--session <id> --fork` creates a `(fork #1)` session; codex: `codex fork <id>` (subcommand, verified live); grok: `--resume <id> --fork-session --session-id <new-uuid>` — grok prescribes the fork's id too, verified live |
| Send instruction | `i` / `:` | ✅ | ✅ | ✅ | ✅ | |
| Approve / reject gestures | `Enter` / `Escape` | ✅ | ✅ | ✅ | ✅ | Key gestures are backend-resolved; grok uses digit keys (`2`/`3`), no Enter |
| Live hook-grade status | — | ✅ | ✅ | ✅ | ✅ | codex: `-c 'hooks.<Event>=[...]'` + `--dangerously-bypass-hook-trust` injection, incl. `waiting_approval`; grok: global `~/.grok/hooks/overcode.json`, camelCase dialect, incl. `waiting_approval` |
| Detection-mode toggle | `K` | ✅ | ✅ | ✅ | ✅ | Per-session dispatch picks hooks mode automatically when state files are fresh |
| Token / cost / context columns | — | ✅ | ✅ | ⚠️ tokens/context ✅, cost ⚠️ estimate | ✅ | codex: rollout-JSONL reader; cost has no local figure, but `pricing.py` now carries a `gpt-5.6-sol` entry (codex's account-default model), so it shows that model's real published rate applied to codex's real token counts — a list-price estimate, not a billed figure. grok: `GrokStatsReader` reads a full local token/cost split from `updates.jsonl` (summed per-turn `turn_completed.usage`, `costUsdTicks` converted from nano-dollars) — genuinely billing-accurate, not an estimate |
| AI summaries | `A` | ✅ | ✅ | ✅ | ✅ | |
| Preview pane | `m` | ✅ | ✅ | ✅ | ✅ | |
| Sleep mode / heartbeat | `z` / `H` | ✅ | ✅ | ✅ | ✅ | |
| Remote agents via sisters | `N` | ✅ | ✅ | ✅ | ✅ | Capabilities travel with the agent, so remote gating matches local |
| Devcontainer wrapper | — | ✅ | ✅ | ✅ | ⚠️ installs, auth unverified | codex installs via npm like Claude; grok has no npm package (curl installer) and its container auth story is untested (see below) |
| Permission modes | — | ✅ full | ⚠️ bypass full, permissive approximate | ✅ full | ✅ full | opencode: bypass genuinely overrides deny rules via `OPENCODE_PERMISSION`, permissive (`--auto` alone) still lets deny rules win (see below); codex: distinct flags for bypass/permissive/normal (see below); grok: same, plus flag-vs-config precedence confirmed live (see below) |
| `--allowed-tools` allowlist | — | ✅ | ❌ | ❌ | ✅ | No opencode or codex flag exists, silently ignored; grok: repeated `--allow <rule>` per tool, confirmed to actually suppress the dialog live |
| Skills | — | ✅ | ❌ | ❌ | ❌ | grok has skills + a marketplace, unintegrated |
| Sandbox badge | — | ✅ | ❌ | ❌ | ❌ | Claude-only loopback probe |
| Subscription-usage widget | — | ✅ | ❌ | ❌ | ❌ | Anthropic-only usage API |
| Agent teams | — | ✅ | ❌ | ❌ | ❌ | Claude Code experimental feature |

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
backend line as opencode. Launch, resume, fork, kill, restart, hooks-grade
status (including `waiting_approval`), devcontainer support, and
token/context columns all work; cost is a list-price estimate, not a billed
figure — see the honesty note at the top of this document.

### Models

codex expects a bare model id and overcode passes `-m` through verbatim:

```bash
overcode launch -n cx -B codex --model gpt-5.6-sol
```

No provider prefix — that is opencode's grammar, not codex's.

---

## Launching a grok agent

```bash
overcode launch -n my-agent --backend grok -d ~/code/myproject
overcode launch -n my-agent -B grok --model grok-4.6
```

Same `-B` short form, same new-agent-modal toggle, same `overcode show`
backend line as opencode/codex. Launch, resume, fork, kill, restart, the
permission allowlist, hook-grade live status (including `waiting_approval`),
devcontainer support (install only — see the containers section for the
auth caveat), and the token/cost/context columns all work — see the honesty
note at the top of this document.

grok requires a SuperGrok or X Premium+ subscription and a `grok login` run
once outside overcode. If `~/.grok/auth.json` is missing or empty,
`overcode doctor` names `grok login` explicitly rather than letting the
launch fail with no explanation.

### Models

grok expects a bare model id and overcode passes `-m` through verbatim:

```bash
overcode launch -n gk -B grok --model grok-4.6
```

No provider prefix — same grammar as codex's, unlike opencode's
`provider/model` form. Phase 0 found `-m`/`--model` fails asymmetrically: an
unknown id is silently ignored by the interactive TUI (it just falls back to
the account default with no visible error) but rejected loudly by headless
`-p` mode. overcode does not currently pre-validate against `grok models`.

### Session id prescription and the permission allowlist

grok is the first non-Claude backend to use both of these overcode
capabilities:

- **`SESSION_ID_PRESCRIPTION`**: overcode mints a uuid and passes
  `-s/--session-id <uuid>` on every fresh launch (and on every fork — see
  below), so `overcode show <name>` displays the session id immediately
  instead of waiting for discovery. The session lands at
  `~/.grok/sessions/<percent-encoded-abs-cwd>/<uuid>/` (`/` → `%2F`,
  including the leading slash) — confirmed live, round-trip verified.
- **`PERMISSION_INJECTION`**: `--allowed-tools Bash,Read` becomes
  `--allow Bash --allow Read` — one repeated flag per tool, using the same
  `Tool(glob)` rule grammar Claude's `--allowedTools` speaks. A bare tool
  name means "allow every invocation of that tool," confirmed to actually
  suppress the approval dialog live for a matching command.
- **Fork prescribes a new id too**: `overcode launch-fork` mints a *second*
  fresh uuid for the forked agent (`--resume <source-id> --fork-session
  --session-id <new-uuid>`) — grok is the only backend where fork produces
  a distinct, overcode-chosen id rather than one the CLI generates on its
  own or one discovery has to find later.

---

## Support matrix

Capabilities are the `BackendCapability` flags each adapter declares;
overcode gates UI actions and telemetry off them.

| Capability | claude-code | opencode | codex | grok | Notes |
|---|---|---|---|---|---|
| `RESUME` | ✅ | ✅ | ✅ | ✅ | opencode: `--session <id>`; codex: `codex resume <id>` (subcommand); grok: `--resume <id>` |
| `FORK` | ✅ | ✅ | ✅ | ✅ | opencode: `--session <id> --fork` — **verified**, creates a `(fork #1)` session; codex: `codex fork <id>` — **verified live**; grok: `--resume <id> --fork-session --session-id <new-uuid>` — **verified live**, and unlike the others grok prescribes the fork's own id too |
| `SESSION_ID_PRESCRIPTION` | ✅ | ❌ | ❌ | ✅ | opencode mints its own `ses_…` ids; codex has no `--session-id`-shaped flag for fresh launches; grok's `-s/--session-id` requires a *new* conversation and round-tripped live to `~/.grok/sessions/<enc-cwd>/<uuid>/` |
| `HOOK_EVENTS` | ✅ | ✅ | ✅ | ✅ | opencode: bundled telemetry plugin (below); codex: `-c 'hooks.<Event>=[...]'` + `--dangerously-bypass-hook-trust` injected on every launch (below); grok: global `~/.grok/hooks/overcode.json`, a Claude-compatible, camelCase-dialect hooks system (below) |
| `TRANSCRIPT_STATS` | ✅ | ✅ | ✅ | ✅ | opencode: SQLite `session` table (below); codex: rollout-JSONL reader (below) — tokens/context populate accurately, cost is a list-price estimate from `pricing.py`'s `gpt-5.6-sol` entry (no local figure to bill against). grok's `GrokStatsReader` (below) reads a *full* local input/output/cost split from `updates.jsonl` — genuinely billing-accurate, unlike codex's estimate |
| `PERMISSION_INJECTION` | ✅ | ❌ | ❌ | ✅ | opencode v1.18.19 has no per-launch tool allowlist flag; codex's nearest concept is sandbox modes + `-c` config, not a tool allowlist; grok's `--allow <rule>` (repeatable) confirmed live to actually suppress the dialog, not just parse cleanly |
| `SKILLS` | ✅ | ❌ | ❌ | ❌ | opencode *does* have a `/skills` command, codex and grok too — none has overcode integration |
| `SANDBOX_PROBE` | ✅ | ❌ | ❌ | ❌ | Claude-only loopback heuristic; codex has its own (unrelated) sandbox |
| `SUBSCRIPTION_USAGE` | ✅ | ❌ | ❌ | ❌ | Anthropic-only usage API |
| `AGENT_TEAMS` | ✅ | ❌ | ❌ | ❌ | Claude Code experimental feature |

## Flag mapping

| overcode concept | claude-code | opencode (v1.18.19) | codex (v0.150.1) | grok (v1.0.5) |
|---|---|---|---|---|
| Binary | `claude` | `opencode` | `codex` | `grok` |
| Bypass permissions | `--dangerously-skip-permissions` | `--auto` + `OPENCODE_PERMISSION=<allow-everything>` (genuinely overrides deny rules — see below) | `--dangerously-bypass-approvals-and-sandbox` | `--permission-mode bypassPermissions` |
| Permissive | `--permission-mode dontAsk` | `--auto` alone (approximate — deny rules still win, see below) | `-a never --sandbox workspace-write` | `--permission-mode auto` (**not** `dontAsk` — see below) |
| Normal | (default) | opencode's own `permission` config | (default: `on-request` approval) | `--permission-mode default`, passed **explicitly on every launch** (see below) |
| Allowed tools | `--allowedTools a,b` | ✗ no flag exists | ✗ no flag exists | `--allow <rule>` repeated once per tool |
| Model | `--model sonnet` | `--model provider/model` | `-m <model>` (bare id, e.g. `gpt-5.6-sol`) | `-m <model>` (bare id, e.g. `grok-4.6`) |
| Persona | `--agent name` | `--agent name` | ✗ (`-p/--profile` is a config-layer override, not a persona flag) | `--agent name` |
| Prescribe session id | `--session-id <uuid>` | ✗ | ✗ | `--session-id <uuid>` (new conversations only) |
| Resume | `--resume <id>` | `--session <id>` | `codex resume <id>` (subcommand, options after) | `--resume <id>` |
| Fork | `--resume <id> --fork-session` | `--session <id> --fork` | `codex fork <id>` (subcommand, options after) | `--resume <id> --fork-session --session-id <new-uuid>` |
| Telemetry injection | `--settings '<json>'` hooks | `.opencode/plugins/overcode-telemetry.js` | `-c 'hooks.<Event>=[...]'` × 8 + `--dangerously-bypass-hook-trust` | global `~/.grok/hooks/overcode.json` (marker + inertness-guarded) |
| Stats source | `~/.claude/projects/**.jsonl` | SQLite `~/.local/share/opencode/opencode.db` | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `~/.grok/sessions/<enc-cwd>/<uuid>/updates.jsonl` + `summary.json` + `prompt_history.jsonl` |
| Graceful exit | `C-c`, then `/exit` | `Escape` ×2, then `/exit` | `Escape`, then `/quit` | `Escape`, then `/quit` |
| Bare `C-c` | safe | kills the process | **kills the process instantly, no confirmation** | **safe** — interrupts only, same as Escape (opposite of codex/opencode) |
| Clear conversation | `/clear` | `/new` | `/new` | `/new` |
| Approve | `Enter` | `Enter` (confirms the preselected *Allow once*) | `Enter` (confirms the preselected *Yes, proceed*) | `2` (no Enter — digit alone executes; option `1` is *always-approve*, not a one-time approve) |
| Reject | `Escape` | `Escape` | `Escape` (no literal reject key) | `3` (no Enter) |
| Trust-folder dialog | "I trust this folder" | none | "Do you trust the contents of this directory?" — `Enter` accepts | none — confirmed absent even in a never-before-visited directory |

### Permission modes: permissive and bypass no longer collapse

overcode has three modes; opencode has one command-line flag (`--auto`) plus,
as of the Ancillary item shipped alongside 0.5.1, one env var
(`OPENCODE_PERMISSION`) that a genuinely stronger mode can use.

- **normal** → no flag. opencode asks according to its own `permission`
  config in `opencode.json` / `~/.config/opencode/opencode.jsonc`.
- **permissive** → `--auto`, and nothing else. `--auto` auto-approves
  anything not explicitly *denied*, so opencode's own `"deny"` rules still
  win — this is honestly closer to Claude's `dontAsk` than to
  `--dangerously-skip-permissions`, and stays that way for permissive.
- **bypass** → `--auto` **plus** `OPENCODE_PERMISSION` set to an
  allow-everything JSON blob (`OpencodeBackend.env_prefix()`, every key from
  opencode's own published config schema forced to `"allow"`). Unlike
  `--auto` alone, `OPENCODE_PERMISSION` is merged into opencode's resolved
  config *after* project config — live-verified via `opencode debug config`
  (Ancillary section of `docs/design/agent-backends-codex-grok.md`) that it
  genuinely **overrides** a project's `"deny"` rules, not just the ones
  `--auto` leaves alone. No file is written and nothing needs cleanup: the
  var is process-scoped, set only for the launched opencode process, gone
  the moment it exits.

If you need finer control than these three modes offer, put it in the
project's `opencode.json`:

```json
{ "permission": { "bash": "ask", "edit": "allow" } }
```

— note that in **bypass** mode, `OPENCODE_PERMISSION` overrides this file's
`deny`/`ask` rules too; use **permissive** if you want the project's own
rules to still apply.

`--allowed-tools` is silently ignored for opencode: the `--permissions`
flag the design research expected does not exist, and emitting it would
fail the launch outright.

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

### grok's permission modes are exact, and explicit on every launch

Like codex, grok has a distinct flag for each of overcode's three modes —
but grok adds a wrinkle codex and opencode don't have: **the user's own
config can silently override what overcode asks for**, so overcode never
omits the flag, even for "normal" mode.

- **normal** → `--permission-mode default`. Approval dialog for every tool
  call, same look as `permission_required.txt`.
- **permissive** → `--permission-mode auto`. **Not** `dontAsk` — Phase 0
  found `dontAsk` shows the *identical* approval dialog as `default`; only
  `auto` actually skips it (confirmed live: same test command, `auto`
  produced no dialog at all, `dontAsk` produced the full dialog).
- **bypass** → `--permission-mode bypassPermissions`.

**Why every launch passes `--permission-mode` explicitly, never relying on
the default:** grok's own `~/.grok/config.toml` can set
`[ui] permission_mode = "always-approve"` — a real setting on the machine
this was verified against. Phase 0 confirmed live that the launch flag
overrides it (`--permission-mode default` produced a real dialog despite the
config's always-approve; omitting the flag reproduced silent auto-approve).
If overcode ever omitted the flag for "normal" mode, a user with that config
line would get silent auto-approval regardless of what overcode's UI showed
them the mode as — so `build_command()` always emits `--permission-mode`,
never treats "normal" as "say nothing."

`--allowed-tools` **is** honored for grok, unlike opencode/codex: each
comma-separated tool name becomes its own `--allow <name>` flag, confirmed
live to both parse cleanly and actually suppress the dialog for a matching
command (`--allow 'Bash(echo *)'` was the exact rule-syntax probe; a bare
tool name is the parent case of that grammar — allow every invocation).

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
| context | same event's `last_token_usage.total_tokens` — the latest request's input (which already carries the whole conversation) plus its output. The cumulative `total_token_usage` re-counts the resent context every turn, so it overstates occupancy (a two-turn session read ~2x codex's own `/status` figure); it feeds only the Σ token columns |
| model | latest `turn_context.payload.model` (one line per turn) |
| interactions | `response_item` messages where `role=="user"` **and** `internal_chat_message_metadata_passthrough.content_item_kinds` contains `"user.text"` — excludes injected `<environment_context>`/skills/permissions scaffolding, which carries its own kind tags instead |
| cost | **not a dash — a list-price estimate, not a billed figure.** codex is subscription/API billed with no local per-turn charge (matches Claude's transcript, which also carries none), so there is nothing to compare an estimate against. `pricing.py`'s `MODEL_PRICING` table carries a `gpt-5.6-sol` entry (codex's account-default model — Appendix A), sourced from OpenAI's own pricing docs and standard-tier/short-context only (batch/flex/fast-mode and long-context tiers aren't modelled, since overcode has no signal for which tier a turn ran under). A codex turn on a different model than `gpt-5.6-sol` still falls back to your configured *default* per-token price (`settings.get_model_pricing`), the same behaviour every backend gets for an unrecognized model — live-verified during Phase 2 smoke testing. Either way the column always shows a real dollar figure, never a dash; treat it as informative, not as billing-accurate |

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

## Telemetry: grok's global hooks file

grok ships a hooks system that is *explicitly* Claude Code compatible
(grok's own bundled docs describe it that way) — same event names
(`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `StopFailure`,
`SessionEnd`), same exit-code-2 semantics, plus a grok-only `StopCancelled`
event and `Notification` events with `idle_prompt`/`permission_prompt`
matchers. Unlike Claude Code (`--settings` on the command line) or codex
(`-c` config overrides), grok has no per-launch injection flag, so overcode
ships a small JSON file instead:

```
~/.grok/hooks/overcode.json
```

written by `GrokBackend.prepare_launch()` (`ensure_hooks_installed()` in
`src/overcode/backends/grok.py`) on every launch/restart/revive/fork. It
registers:

```
UserPromptSubmit, PreToolUse, PostToolUse,
Stop, StopFailure, StopCancelled,
SessionStart, SessionEnd,
Notification (matcher: permission_prompt),
Notification (matcher: idle_prompt)
```

— the five registrations grok's own hooks doc says a "complete busy and idle
indicator" needs (`UserPromptSubmit` + `Stop` + `StopFailure` +
`StopCancelled` + the `idle_prompt` backstop), plus `SessionStart`/
`SessionEnd` for parity with the other backends and `PreToolUse`/
`PostToolUse` for the running-state detail. Every registration shares one
command, with a 5-second timeout (grok defaults `Stop`-family gates to 600s;
overcode never needs that long since none of these ever return a block
decision):

```
sh -c '[ -n "$OVERCODE_SESSION_NAME" ] || exit 0; exec <overcode-bin> hook-handler'
```

### Why global, not project-scoped

grok's project-local hooks (`.grok/hooks/*.json`) require folder trust
(`--trust`/`/hooks-trust`), which grants MCP+LSP+hook trust to the *whole
directory* — too big a side effect for overcode to grant silently on your
behalf. `~/.grok/hooks/*.json` is **always trusted**, no matter which
project you launch grok in, so overcode writes there instead — inertness
(below) is what keeps that safe.

### Things worth knowing about the footprint

- **The file is visible outside your project.** It lives in your home
  directory, not any repository, so there's nothing to `.gitignore`.
- **It is inert outside overcode.** The command guards on
  `OVERCODE_SESSION_NAME` being set — hook subprocesses inherit the grok
  process's environment, so any `grok` session you launch by hand, in any
  directory, on any machine, runs the exact same registered hooks and exits
  immediately without touching your filesystem.
- **Your own file is never clobbered.** Overcode only rewrites the file if
  its `description` field still carries the `OVERCODE-HOOKS-MARKER` string.
  Replace the contents (or the marker) and overcode leaves it alone
  permanently.
- **It is not removed when the last agent dies.** It's global, not
  per-project — every future grok launch anywhere needs it. Delete it
  whenever you like; overcode recreates it on the next launch.

`overcode doctor` reports `missing-settings` for a grok agent whose global
hooks file is missing or de-markered — see the doctor section below.

### Dialect and event mapping

grok's hook stdin is **camelCase** (`hookEventName`, `sessionId`,
`promptId`, `toolName`, `toolInput`, `toolResult`, …) where Claude's/codex's
is snake_case — confirmed live, exact JSON captured during Phase 0.
`hook_handler._normalize_hook_payload()` translates keys (grok's `toolResult`
maps to Claude's `tool_response` — a genuine rename, not just casing); a
second pass, `_apply_grok_semantics()`, handles the parts that aren't just
casing:

| grok event | overcode hook event | Status |
|---|---|---|
| `UserPromptSubmit` | `UserPromptSubmit` | running |
| `PreToolUse` / `PostToolUse` | `PreToolUse` / `PostToolUse` | running |
| `Notification` (matcher `permission_prompt`) | `PermissionRequest` | **waiting_approval** |
| `Stop` (`reason == "end_turn"`) | `Stop` | waiting_user |
| `StopCancelled` | `Stop` | waiting_user |
| `StopFailure` | `StopFailure` | error |
| `Notification` (matcher `idle_prompt`) | `Stop` | waiting_user (idle backstop) |
| `SessionEnd` | `SessionEnd` | terminated |
| `SessionStart` | (records `session_id`) | — |

grok's own lowercase tool names are mapped onto Claude's taxonomy so the
detector's Bash-command activity strings and obligation tracking keep
working: `run_terminal_command`→`Bash`, `read_file`→`Read`,
`search_replace`→`Edit`, `grep`→`Grep`, `list_dir`→`Glob`,
`web_search`→`WebSearch`, `spawn_subagent`→`Task`. An unaliased tool name
passes through unchanged.

Three filtering rules, all load-bearing (verified live during Phase 0 and
Phase 4 smoke testing):

- **Session-teardown `Stop` is dropped.** grok fires a *second* `Stop` at
  session end with `reason` set to `"shutdown"`/`"channel_closed"` instead of
  `"end_turn"` — a handler that doesn't filter on `reason` double-settles on
  every exit. `SessionEnd` is the event that owns teardown.
- **Subagent events never touch the session's own status.** Any event
  carrying `subagentType` is dropped entirely — "a subagent's stop is not
  the session's" (grok's own hooks doc).
- **Stale turn-end reports are ignored.** A `StopCancelled`/`Stop`/
  `StopFailure` report can be dispatched after the *next* turn's
  `UserPromptSubmit` already started (grok's own command loop, not
  overcode's). `hook_handler` tracks the newest `promptId` seen on
  `UserPromptSubmit` (`active_prompt_id` in `hook_state_<agent>.json`) and
  drops a turn-end report whose `promptId` doesn't match it. Events with no
  `promptId` at all (the `idle_prompt` backstop, the session-end `Stop`)
  always settle unconditionally — that's grok reporting on the *session*,
  not a turn.

---

## Stats: grok's updates.jsonl / summary.json / prompt_history.jsonl

`GrokStatsReader` (`src/overcode/backends/grok_stats.py`) needs no discovery
at all: `SESSION_ID_PRESCRIPTION` means overcode always minted the session
id itself, so it keys straight into
`~/.grok/sessions/<percent-encoded-abs-cwd>/<uuid>/` (`GROK_HOME` honoured,
defaulting to `~/.grok`; the cwd encoding is a full absolute-path
percent-encode, `/`→`%2F`, including the leading slash).

| overcode column | grok source |
|---|---|
| input tokens | every `turn_completed` update's `usage.inputTokens`, **summed** across the session |
| output tokens | same, `usage.outputTokens` + `usage.reasoningTokens`, summed |
| cache read / write | same, `usage.cachedReadTokens` / `usage.cacheCreationTokens`, summed |
| cost | same, `usage.costUsdTicks` summed, divided by 1e9 (nano-dollars — see below) |
| context | latest `params._meta.totalTokens` seen across `updates.jsonl`, in file order |
| model | `summary.json`'s `current_model_id` |
| interactions | count of `prompt_history.jsonl` lines (one per project, not per session) whose `session_id` matches |

Two things the original design research got wrong, corrected empirically
against a real 413-message session before this reader was written (see
`grok_stats.py`'s module docstring for the full account):

- **`turn_completed.usage` is per-turn, not cumulative.** A real session's
  consecutive `usage` objects are *not* monotonically increasing (observed
  `inputTokens` sequence: 4,130,868 → 89,480 → 452,829 → …) — each one
  covers only the turns since the previous report. `GrokStatsReader`
  therefore **sums** every `turn_completed.usage` object rather than taking
  "the latest one" the way codex's genuinely-cumulative `token_count` events
  are read.
- **`costUsdTicks` is nano-dollars** (1e9 ticks per USD), not the millionths
  the design doc's single early sample hadn't ruled out. Cross-checked
  against the same real session: a small turn (~13.6k input tokens, heavy
  reasoning) priced at 113,440,000 ticks → $0.11, and a much larger batch
  (4.1M input, 3.3M of it cached, 137k output/reasoning) priced at
  7,295,125,400 ticks → $7.30 — both land in a plausible dollar-per-token
  range; the millionths reading would have put the second figure at $7,295,
  which is not plausible for the token counts involved.

Any failure — missing directory, a corrupt line, an unreadable file —
returns "unknown", so the columns render dashes rather than misleading
zeros. A `turn_completed.usage` object missing expected keys raises an
`overcode doctor` warning naming them, checked against the most recently
modified session directory rather than every session ever recorded.

`pricing.py`'s `grok-4.6`/`grok-4.5` entries (standard-tier, short-context,
sourced from xAI's own docs) exist only as the generic cost-estimate
fallback every backend gets when the real per-turn figure above is
unavailable — `GrokStatsReader`'s `costUsdTicks` read is the number that
actually reaches the cost column in the normal case. The `grok-4.6` entry
was cross-checked against the real session above: pricing its largest batch
(790,868 non-cached + 3.34M cached input tokens, 137k output/reasoning) at
the long-context tier (≥200k tokens, the whole request billed at the higher
rate) gives ≈$8.15, within ~12% of the real billed $7.30 — consistent with
a session whose per-call context had grown past the long-context threshold.

---

## Opting out of telemetry

Every non-Claude backend's telemetry has an on-disk footprint of some kind
(codex is the one exception — its hooks are per-launch argv, not a file).
If you'd rather not have overcode write anything until you're sure about a
backend, turn it off per-backend in `~/.overcode/config.yaml`:

```yaml
backend_telemetry:
  opencode: off
  codex: off
  grok: off
```

Default is `on` for every backend, so nothing changes until you opt one out.
Each backend's actual footprint, for reference:

| Backend | Footprint | Scope |
|---|---|---|
| `claude-code` | none — hooks ride per-launch `--settings` flags | n/a, and **exempt from this knob** (see below) |
| `codex` | none — hooks ride per-launch `-c 'hooks.<Event>=...'` + `--dangerously-bypass-hook-trust` flags | n/a |
| `opencode` | `<project>/.opencode/plugins/overcode-telemetry.js`, written by `prepare_launch()` | per-project |
| `grok` | `~/.grok/hooks/overcode.json`, written by `prepare_launch()` | global (inert outside overcode — see above) |

**Claude Code is exempt from `backend_telemetry`** — its hooks are core to
how overcode supervises Claude agents at all, and (like codex) they leave no
on-disk footprint to opt out of, so the config knob is silently ignored for
it (`config.get_backend_telemetry_enabled` always returns `True` for
`claude-code`).

With a backend's telemetry off, `prepare_launch()` writes nothing and
`build_command()` omits that backend's hook/telemetry argv entirely — codex
skips all eight `-c 'hooks.<Event>=...'` overrides and
`--dangerously-bypass-hook-trust`; opencode skips the plugin install; grok
skips the hooks-file install. Nothing else changes: per-session status
detection (`status_detector_factory.py`) already falls back to pane polling
automatically whenever an agent has no fresh hook state, which is exactly
the condition telemetry-off produces — the same path an opencode agent
already takes if its plugin install failed for some other reason.

`overcode doctor` knows the difference between "broken" and "opted out": an
agent on a backend whose telemetry is configured off reads as
`telemetry-disabled` (dim, informational) rather than `missing-settings`
(red, implies a broken launch), and `overcode doctor --fix` does not try to
"fix" it by restarting.

Already have a footprint installed and want it gone? Turning the config knob
off does **not** retroactively remove an existing file — pair it with:

```bash
overcode hooks uninstall-backend opencode --dir ~/code/myproject
overcode hooks uninstall-backend grok
overcode hooks uninstall-backend codex        # nothing installed on disk for this backend
overcode hooks uninstall-backend claude-code  # nothing installed on disk for this backend
```

`--dir` is required for opencode (its plugin is project-scoped); grok's
hooks file is global, so no `--dir` is needed. Both removals check for
overcode's own marker first (`OVERCODE-PLUGIN-MARKER` / `OVERCODE-HOOKS-MARKER`)
and refuse to touch a file you've since edited yourself.

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

## grok: hooks-grade status, pane polling as fallback

As of Phase 4, a grok agent whose global hooks file is installed and firing
gets the same hooks-grade status Claude Code, opencode and codex get —
including the `waiting_approval` distinction pane polling cannot make.
Per-session dispatch (`status_detector_factory.py`) picks hooks mode
automatically once `hook_state_<agent>.json` exists and is fresh; nothing to
configure. **Pane polling is still the fallback** for the gap between
process start and the first hook firing, and for a grok agent whose global
hooks file was deleted or de-markered some other way. The pattern set lives
in `src/overcode/backends/grok.py` (`GROK_PATTERNS`), grounded in a
committed corpus of real Grok Build v1.0.5 captures at
`tests/fixtures_grok_panes/`, replayed by
`tests/unit/test_status_detector_grok.py`.

The polling-mode signals that matter:

| overcode status | grok chrome |
|---|---|
| `running` | `Esc:cancel` in the footer hint bar (`Shift+Tab:mode │ Esc:cancel │ Ctrl+x:shortcuts`) — the spinner line itself (`⠼ Waiting for response…`) is a secondary signal, since it can scroll out of the detector's trailing window on a longer turn, but the footer hint is fixed UI chrome and never does |
| `waiting_user` (permission) | `Yes, and don't ask again for anything` / `No, reject` / `1/3:select` — the dialog replaces the input box entirely |
| `waiting_user` (idle) | the input box's empty-input shape (`│ ❯` ... `│`) — matched as a shape, not a fixed string, since grok's box border width depends on the terminal and there's no bare prompt glyph the way Claude Code draws one |
| `terminated` | shell prompt, none of the above |

Known rough edges of the **polling fallback**, honestly (hooks mode does not
have these — permission dialogs distinguish `waiting_approval` there):

- **No `waiting_approval` under polling.** Same as codex: permission
  dialogs read as `waiting_user`. `overcode send <name> approve` still
  works — it sends grok's digit-2 gesture regardless of which status label
  got it there.
- **The input box has no fixed-width idle marker.** Unlike codex's single
  literal placeholder string ("Ask Codex to do anything"), grok's box
  border is drawn at the terminal's width, so `GrokStatusPatterns` matches
  the *shape* of an empty input line (`│ ❯` with only whitespace between the
  glyph and the closing border) rather than one fixed string. The same
  constraint means `prompt_ready_chars()` — used once, right after launch,
  to know when it's safe to send the first prompt — can't use the box
  either; it uses the one width-invariant string actually captured live: the
  right-aligned `[stable]` release-channel tag that appears alone on a
  fresh, pre-interaction launch. If a different channel ever renders a
  different tag there, this degrades to the launcher's existing
  30-second-timeout-then-send-anyway fallback, not a crash.
- **Headless bad-model errors read as `waiting_user`, with the raw error
  text as the activity string — not `terminated`.** `error_bad_model.txt`
  was captured from a *headless* (`-p`) run, not the interactive TUI —
  Phase 0 found the interactive TUI silently swallows a bad `--model` id
  instead of erroring, so there is no live TUI error chrome to key a
  pattern on. The headless error text itself doesn't match any grok-
  specific or shell-prompt pattern, so the detector's default phase reports
  `waiting_user` with the cleaned error line as the activity — a documented
  correction to this corpus's own README, which had annotated the fixture
  "terminated" before the detector's actual phase ordering was traced
  through by hand (see `tests/unit/test_status_detector_grok.py`'s
  `test_error_bad_model_reads_as_waiting_user_not_terminated`).
- **Finished tool/status lines are not treated as work.** `◆ Thought for
  Ns` and `◆ Run ...` stay on screen after they happen, so tool-execution
  detection is disabled the same way it is for opencode/codex; the footer
  hint's `Esc:cancel` covers the in-flight case.
- **UNVERIFIED: thinking/reasoning chrome.** No reasoning-capable model
  rendered visible "still thinking" chrome during Phase 0 corpus capture —
  the `◆ Thought for Ns` line is a settled summary, not a live spinner.
- **Bare `C-c` is safe on grok** — the opposite of codex/opencode. overcode
  still prefers `/quit` (after a settling `Escape`) over sending `C-c`
  directly, per Appendix B's recommendation, for consistency with the other
  three backends' graceful-exit shape.

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

grok gets two checks of its own, gated on the fleet containing a grok agent:

1. **Version range.** No fast release cadence was found in Phase 0 (unlike
   codex/opencode, `grok update --help` and `~/.grok/config.toml` show no
   auto-update toggle, and no background update chatter was observed during
   probing), but the guardrail still applies as the corpus ages. The range
   lives in `TESTED_GROK_RANGE` in `src/overcode/backends/grok.py`
   (currently `>=1.0.5, <2.0.0`).
2. **Missing or empty `~/.grok/auth.json`.** grok needs a SuperGrok or X
   Premium+ subscription and a `grok login` run once outside overcode — a
   binary that's installed but never logged in fails every launch with no
   overcode-side explanation otherwise. This check only fires once the
   version check itself succeeds (proof the binary runs at all), and names
   `grok login` explicitly.

3. **`updates.jsonl` schema drift.** A grok upgrade that renames a
   `turn_completed.usage` field blanks the token/cost/context columns;
   doctor names the missing keys (checked against the most recently
   modified session directory across the whole sessions root) rather than
   leaving you to guess.

Per-agent, the health verdict for a grok session is a two-pass check, the
same shape opencode's plugin check takes: a live `grok` process under the
pane reads `ok` on the first pass (grok's own argv carries no telemetry
trace to inspect, unlike codex's `--dangerously-bypass-hook-trust`), then
`refine_health_verdict` looks for the global hooks file
(`~/.grok/hooks/overcode.json`) still carrying the `OVERCODE-HOOKS-MARKER`.
Missing or de-markered reports `missing-settings`, the same verdict Claude
Code gets when it is running without `--settings` and opencode gets when its
telemetry plugin is missing. `overcode restart` re-installs it.

---

## Supervising an opencode, codex, or grok agent

The supervisor's own meta-agent stays Claude Code, but its gestures are
backend-resolved:

```bash
overcode send <name> approve   # opencode: confirms "Allow once"; codex: confirms "Yes, proceed"; grok: digit "2" (Yes, proceed — not the default-selected always-approve option)
overcode send <name> reject    # Escape — dismisses, abandoning the tool call (grok: digit "3", no Escape needed)
```

These are *gestures*, not keys: overcode asks the agent's backend which keys
its permission dialog wants. Prefer them over the raw `overcode send <name>
enter` / `escape`, which still exist and still send literal keys. Supervisor
context lines name a non-default backend (`Backend: opencode` /
`Backend: codex` / `Backend: grok`) so the supervisor knows not to send
Claude slash commands or Enter-based approval gestures at it — codex's
clear-conversation gesture is `/new`, not `/clear`, its graceful exit is
`/quit`, not `/exit`; grok's approve/reject gestures are bare digit keys
(`2`/`3`) with no Enter at all, and its default-selected dialog option is
*always-approve*, not a one-time approval — sending a bare Enter at a grok
permission dialog would silently switch the session into always-approve
mode rather than approving just the one tool call.

---

## Containers: the devcontainer wrapper

`--wrapper devcontainer` works for all four backends. The launcher exports
`OVERCODE_BACKEND` into the wrapper's environment for any non-default
backend (Claude Code leaves it unset, so the wrapper's behaviour there is
byte-for-byte what it was before), and the wrapper keys its install step off
it:

| `OVERCODE_BACKEND` | installs | `overcode hooks install` |
|---|---|---|
| unset / `claude-code` | `npm i -g @anthropic-ai/claude-code` | yes |
| `opencode` | `npm i -g opencode-ai@latest` | skipped — no settings.json hook protocol |
| `codex` | `npm i -g @openai/codex` | skipped — hooks are per-launch `-c hooks...` argv, not a settings file |
| `grok` | `curl -fsSL https://x.ai/cli/install.sh \| bash` (no npm package) | skipped — hooks are the global `~/.grok/hooks/overcode.json` file |

opencode's and codex's telemetry reach the host without any extra mount:
opencode's plugin is staged into the project directory, which is
bind-mounted as `/workspace`; codex's hooks are injected via argv on every
launch, same as outside a container. grok's global hooks file
(`~/.grok/hooks/overcode.json`) is written to the *host's* home directory by
`GrokBackend.prepare_launch()`, not inside the container — it has no effect
on a grok process running inside the container's own filesystem unless you
mount it in yourself. All backends' hook-state exchange directory is already
mounted at `/overcode-state`. Provider credentials present in your shell
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
`GEMINI_API_KEY`, `XAI_API_KEY`) are forwarded into the container.

**grok's container auth story is unverified, not confirmed unsupported.**
Unlike codex/opencode's API-key-friendly auth, grok's normal path is a
SuperGrok/X Premium+ subscription login that writes `~/.grok/auth.json` on
the host. `XAI_API_KEY` is forwarded if set, but whether grok's interactive
browser login flow even works from inside a container's tmux pane has not
been tested with a live docker build (out of scope for this pass — see
`wrappers/README.md`). If you need grok to skip that login, mount your host
`~/.grok` into the container at the same path yourself; the wrapper does not
do this automatically, and doing so also would not install the global hooks
file (which the host-side launch already staged at `~/.grok/hooks/` on the
host, not the container).

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
The grok backend (`src/overcode/backends/grok.py`) is a third data point on
the same pattern, from the opposite direction: it started (Phase 3) with
`SESSION_ID_PRESCRIPTION` and `PERMISSION_INJECTION` declared *alongside*
`RESUME`/`FORK` — both are launch-flag-shaped for grok, so there was no
reason to defer them the way codex deferred its (flag-shaped)
`HOOK_EVENTS` — while still deferring `HOOK_EVENTS`/`TRANSCRIPT_STATS` to
Phase 4, exactly the axis codex's Phase 1→2 split was on. Declare whatever a
phase actually verified, in whatever order the underlying CLI's own launch
grammar makes cheap.
