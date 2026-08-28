# Agent Backends: Codex CLI + Grok Build Support Plan

**Document Type:** Design Assessment + Phased Implementation Plan
**Date:** August 2026
**Status:** Implemented (Phases 0–5 + Ancillary, Aug 2026)
**Scope:** Adding OpenAI Codex CLI and xAI Grok Build as overcode's third and fourth agent backends, on the `AgentBackend` seam shipped in 0.5.0
**Predecessor:** `docs/design/agent-agnostic-backends-opencode.md` — read its §2 (architecture) and its shipped-notes first; this plan assumes that seam and does not re-explain it.

> **Shipped, 0.6.0.** All five phases plus the opencode Ancillary item
> landed. User-facing documentation is `docs/backends.md`; the architecture
> write-up is `docs/architecture.md` (§Agent Backends); release notes are
> `docs/release-notes-0.6.0.md`. Where reality diverged from this plan, the
> phase sections and Appendices A/B below carry dated notes and remain the
> authority on codex's and grok's actual behaviour. The headline
> divergences, collected from each phase's own shipped-notes:
>
> 1. **`C-c` kills codex outright, but is safe on grok** — the opposite
>    result on two backends verified in the same pass (Phase 0, §2.2/§3.2).
>    Codex's safe interrupt is `Escape`; grok's `Escape` also works but isn't
>    required.
> 2. **Codex's hook-injection crux resolved cleanly**: `-c
>    'hooks.<Event>=[...]'` + `--dangerously-bypass-hook-trust` fires hooks
>    with zero global-file writes, and codex's hook stdin is already
>    snake_case/Claude-shaped, needing no dialect translation (Phase 0/2).
>    One shape correction: `HookHandlerConfig::Command` is a bare string, not
>    an array like Claude's.
> 3. **Grok's stats turned out fuller than planned, but needed two empirical
>    corrections before `GrokStatsReader` could trust them** (Phase 4): (a)
>    `turn_completed.usage` is **per-turn, not cumulative** — a real
>    session's consecutive `usage` objects are not monotonically
>    increasing, so the reader **sums** every object rather than taking
>    "latest wins" the way codex's genuinely-cumulative `token_count` events
>    are read; (b) `costUsdTicks` is **nano-dollars** (1e9/USD), not the
>    millionths an early single sample hadn't ruled out — confirmed against
>    a real session where the millionths reading would have implied an
>    implausible $7,295 for one batch of turns. A third, smaller correction:
>    the context-size proxy lives at `params._meta.totalTokens`, nested
>    inside `params`, not at the update envelope's top level.
> 4. **Grok's `--permission-mode dontAsk` is NOT an alias for `auto`** — it
>    shows the identical approval dialog as `default`; only `auto` actually
>    skips it. `GrokBackend`'s permissive-mode mapping targets `auto` (Phase
>    0/3), and the mode is passed explicitly on *every* launch — Phase 0
>    found the user's own `~/.grok/config.toml` can set
>    `permission_mode = "always-approve"`, and only an explicit flag beats it.
> 5. **Grok is the only backend where fork mints a brand-new prescribed
>    session id** (`fork_prescribes_new_session_id = True`, Phase 3) — unlike
>    Claude Code (also `SESSION_ID_PRESCRIPTION`, but keeps the CLI's own
>    forked id) or codex/opencode (no prescription at all).
> 6. **Codex's cost column was never a dash, and by 0.6.0 it's a sourced
>    estimate, not a placeholder.** Phase 2 found `monitor_daemon.py`'s cost
>    estimator already, app-wide, falls back to the user's *configured
>    default* per-token price for any unrecognized model — so an unpriced
>    codex agent showed a real dollar figure priced as if it were the
>    default model, not a dash. This phase (5) added a `gpt-5.6-sol` entry to
>    `pricing.py` (codex's account-default model — Appendix A), sourced from
>    OpenAI's own pricing docs, so that fallback now only applies to a codex
>    turn on some other model.
> 7. **This phase also added `grok-4.6`/`grok-4.5` entries to `pricing.py`**
>    as the fallback path for when `GrokStatsReader`'s real local
>    `costUsdTicks` figure is unavailable, sourced from xAI's own docs and
>    cross-checked against Phase 4's real stored-cost sample: pricing that
>    session's largest batch at the long-context tier landed within ~12% of
>    the real billed $7.30, consistent with a session whose per-call context
>    had grown past the long-context threshold.
> 8. **The opencode Ancillary item shipped alongside Phase 5, not before
>    it**: `OpencodeBackend.env_prefix()` sets `OPENCODE_PERMISSION` to an
>    allow-everything blob for **bypass** mode only, live-verified via
>    `opencode debug config` to override project-level deny rules — the
>    verify-first work below (§Ancillary) found this route in Phase 0, but
>    the key set implemented is the *full* 15-key set from opencode's
>    published `config.json` schema (`read`, `edit`, `glob`, `grep`, `list`,
>    `bash`, `task`, `external_directory`, `lsp`, `skill`, `todowrite`,
>    `question`, `webfetch`, `websearch`, `doom_loop`), re-derived rather
>    than the partial list ("`bash`, `edit`, `webfetch`, …") Phase 0's probe
>    had observed. A `"*"` wildcard key was also re-tested and confirmed
>    **not** to work as an override — it's accepted (schema tolerates
>    unknown keys) but inert alongside explicit deny keys, not a substitute
>    for them.
> 9. **Devcontainer support (Phase 5) treats grok's install and its
>    container-auth story as two separate verdicts.** codex installs inside
>    a container exactly like Claude Code (`npm i -g @openai/codex`); grok
>    has **no npm package** and uses x.ai's curl installer instead
>    (`curl -fsSL https://x.ai/cli/install.sh | bash`), and `XAI_API_KEY` is
>    forwarded alongside the other provider credentials. Per this phase's
>    explicit scope fence (no live docker build), grok's subscription-gated
>    container **auth** story is documented as *unverified*, not confirmed
>    unsupported — a real container smoke test is still outstanding.

> **Ground truth discipline.** The opencode effort's biggest lesson: three of its
> pre-verification flag assumptions were wrong, one dangerously so (`C-c` kills
> opencode outright). Every claim in this document is therefore tagged:
> **[VERIFIED]** — checked against a live install on this machine (Codex CLI
> 0.150.1, Grok Build 1.0.5, macOS/arm64, Aug 2026), from `--help` output,
> on-disk session artifacts, local bundled docs (`~/.grok/docs/user-guide/`),
> or the openai/codex source tree; **[VERIFY-P0]** — plausible from documentation
> but must be confirmed live in Phase 0 before any phase builds on it.
> Phase 0 exists to convert every [VERIFY-P0] into a verdict, and its appendix
> becomes the authority the way Appendix A did for opencode.

> **Phase 0 shipped Aug 27, 2026.** Every `[VERIFY-P0]` tag below is now a
> verdict; **Appendix A (Codex) and Appendix B (Grok) are the authority**,
> citing `tests/fixtures_codex_panes/` and `tests/fixtures_grok_panes/`. The
> headline divergences from the pre-verification plan:
>
> 1. **`C-c` kills codex outright** (no confirmation, confirmed on an idle
>    session) — the same opencode lesson, repeating on a second backend.
>    **`C-c` is safe on grok** — the opposite result, confirmed live on both.
>    Codex's safe interrupt is `Escape`.
> 2. **Codex's hook-injection crux is resolved**: `-c 'hooks.<Event>=[...]'`
>    + `--dangerously-bypass-hook-trust` genuinely fires hooks, with zero
>    global-file writes — the plan's single biggest open risk. One correction:
>    `HookHandlerConfig::Command` takes a **bare string**, not an array like
>    Claude's `command: [str, ...]`.
> 3. **Codex hook stdin is snake_case**, Claude-shaped
>    (`hook_event_name`/`session_id`/`permission_mode`) — likely little to no
>    dialect translation needed, unlike grok's confirmed camelCase.
> 4. **Grok's stats are not partial** — a full local input/output/cost split
>    exists in `updates.jsonl`'s `turn_completed.usage`
>    (`inputTokens`/`outputTokens`/`cachedReadTokens`/`reasoningTokens`/
>    `costUsdTicks`), contradicting §3.4's original "no split found" research.
> 5. **Grok's `--permission-mode dontAsk` is NOT an alias for `auto`** — it
>    shows the identical approval dialog as `default`. Only `auto` actually
>    skips it; the "permissive" mode mapping in §3.2 targeted the wrong value.
> 6. **Grok's flag-vs-config precedence confirmed**: `--permission-mode
>    default` overrides the user's `always-approve` config setting.
> 7. **The opencode Ancillary section's core question is answered**: opencode
>    honors `OPENCODE_CONFIG` (merges, doesn't replace) and, better still, a
>    dedicated `OPENCODE_PERMISSION` env var (JSON, merged last) that
>    empirically **overrides project-level deny rules** — a clean, file-free
>    per-process bypass route, live-verified via `opencode debug config`.

---

## Executive Summary

**Verdict: both backends are adapter-sized tasks, and Grok is the easy one.**
The 0.5.0 seam means neither backend touches launcher orchestration, status
dispatch, stats plumbing, TUI gating, or the sister protocol — those are done.
Each backend is: one adapter module, one `StatusPatterns` instance grounded in
a captured pane corpus, one `StatsReader`, one mock TUI for e2e, doctor checks,
and docs.

Both CLIs are installed, authenticated, and personally used on this machine —
Codex via the user's ChatGPT subscription (`~/.codex/auth.json`), Grok via
x.ai auth (`~/.grok/auth.json`) — so live verification and corpus capture cost
nothing but time.

**Codex CLI (v0.150.1, installed via `npm i -g @openai/codex`):**

- Resume and fork are **subcommands**, not flags (`codex resume <id>`,
  `codex fork <id>`) [VERIFIED] — `build_command()` handles this fine since it
  receives the full `LaunchSpec`, but it is the first backend whose resume
  grammar is not flag-shaped.
- A **stable, enabled hooks system** (`codex features list` → `hooks stable
  true`) with 12 PascalCase events including `PermissionRequest`, `Stop`,
  `Interrupt`, `UserPromptSubmit`, `SessionStart/End` [VERIFIED from
  openai/codex source: `codex-rs/config/src/hook_config.rs`]. This is
  Claude-shaped enough that overcode's existing `hook-handler` protocol maps
  almost 1:1. Injection route was the open question — **resolved in Phase 0,
  see §2.3**: `-c 'hooks.<Event>=[...]'` + `--dangerously-bypass-hook-trust`.
- **Transcript stats are a JSONL scan**, very close to Claude's:
  `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` with `session_meta`
  (id, cwd, cli_version) and `token_count` events carrying full usage splits
  (input/cached/cache-write/output/reasoning) plus `model_context_window` and
  rate-limit/plan info [VERIFIED from real files].
- No session-id prescription at launch [VERIFIED: no such flag in `--help`];
  discovery mirrors opencode's (hook records the id; fallback cwd+time match).

**Grok Build (v1.0.5, already installed at `~/.grok/bin/grok`):**

- The flag grammar is a **deliberate Claude Code clone** [VERIFIED]:
  `--permission-mode {default,acceptEdits,auto,dontAsk,bypassPermissions,plan}`,
  `-s/--session-id <uuid>` **prescription for new sessions**, `--resume`,
  `--continue`, `--fork-session`, `--allow`/`--deny` with literal
  `--allowedTools`/`--disallowedTools` compat aliases, `--agent <name>`.
- Its hooks system is **explicitly Claude Code-compatible** [VERIFIED from
  bundled docs `~/.grok/docs/user-guide/10-hooks.md`]: same event names
  (`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `StopFailure`,
  `SessionEnd`), same exit-code-2 semantics, same output vocabulary — it even
  scans `~/.claude/settings.json` for hooks and exports `CLAUDE_PROJECT_DIR`.
  Differences are catalogued in its own porting guide: **camelCase stdin keys**
  (`hookEventName` not `hook_event_name`), `toolResult` not `tool_response`,
  a grok-only `StopCancelled` event, and `Notification` events with
  `idle_prompt`/`permission_prompt` matchers.
- Stats are **not** the weak spot after all: session files carry a running
  `totalTokens` per event (context proxy), and **Phase 0 found the full
  input/output/cost split** on a different event than originally checked —
  `updates.jsonl`'s `turn_completed.usage` object
  (`inputTokens`/`outputTokens`/`cachedReadTokens`/`reasoningTokens`/
  `costUsdTicks`, per-model breakdown). `TRANSCRIPT_STATS` can be close to
  full, not partial-with-dashes as originally planned; see §3.4.

**Recommended shape:** five implementation phases after a verification phase.
Codex first (richer stats, explicitly requested first), then Grok, then
hardening. Each phase is sized for one agent session and leaves `main`
shippable. Target release: **0.6.0**.

---

## 1. What the 0.5.0 seam already provides (do not rebuild)

A new backend implements the `AgentBackend` protocol
(`src/overcode/backends/base.py`) and registers in
`src/overcode/backends/__init__.py` via `register_backend()`. Everything else
is generic and dispatches per-session:

| Already generic | Where |
|---|---|
| Launch/restart/revive/fork orchestration | `launcher.py` — calls `backend.build_command(spec)`, `prepare_launch(spec)`, `env_prefix(spec)` |
| Per-session status dispatch (hooks vs polling) | `status_detector_factory.py` — keyed on `HOOK_EVENTS` capability + hook-state freshness |
| Hook-state file protocol | `hook_state_<agent>.json` / `hook_events_<agent>.jsonl` — any producer gets `HookStatusDetector` (obligation badges, foreground, detail column) for free |
| Stats seam | `stats_reader.py` — `make_stats_reader()` returning `AgentSessionStats` or None; None renders dashes |
| Capability gating | TUI/CLI/web/sister actions check `BackendCapability` flags; sisters publish `backend_capabilities` |
| TUI surface | new-agent modal backend toggle, BKD column, `overcode show` — all read the registry |
| Mock harness pattern | `tests/mock_agent_lib.py` (shared `ScenarioRunner`) + `tests/mock_opencode.py` + `OPENCODE_COMMAND`-style env override wiring in e2e conftest |
| Doctor plumbing | per-backend version-range checks, `refine_health_verdict`, schema-drift findings — patterns to copy from `backends/opencode.py` |

The full member list a backend supplies: `name`, `display_name`, `binary`,
`version_args`, `install_hint`, `process_basenames`, `not_found_error`,
`capabilities`, `build_command`, `prepare_launch`, `env_prefix`,
`resume_args`, `graceful_exit_keys`, `clear_conversation_keys`,
`approve_keys`, `reject_keys`, `startup_dialog_rules`, `prompt_ready_chars`,
`status_patterns`, `make_stats_reader`, `health_verdict`
(+ optional `refine_health_verdict`).

---

## 2. Codex CLI findings (v0.150.1)

### 2.1 Install & identity [VERIFIED]

- Proper install: `npm i -g @openai/codex` → `/opt/homebrew/bin/codex`
  (already done on this machine). `codex doctor` validates install/auth/state.
- Binary override env for the mock harness: introduce `CODEX_COMMAND`
  (mirroring `CLAUDE_COMMAND`/`OPENCODE_COMMAND`).
- Process basename **[VERIFY-P0 → ✅ confirmed]**: the top-level process is
  `node /opt/homebrew/bin/codex …` (the npm wrapper); it execs a vendored
  binary whose basename is `codex` (`.../codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex`)
  — this child is the one running the actual TUI. `process_basenames` should
  match `codex` against the child; matching only the parent's argv would miss
  it if `ps` reports the wrapper. Confirmed via `ps aux`/`pgrep -fl codex`
  during corpus capture (Phase 0 probe log).
- Auth: shared `~/.codex/auth.json` (ChatGPT subscription or API key). The
  macOS "Codex app" lives *inside ChatGPT.app* and stages its own alpha CLI at
  `~/.codex/plugins/.plugin-appserver/codex` — ignore it; same `~/.codex`
  state, but the npm CLI is the supported surface.
- Config: `~/.codex/config.toml`; per-launch overrides via
  `-c key=value` (dotted paths, TOML-parsed values) [VERIFIED from help].

### 2.2 Flag mapping [VERIFIED from `--help` unless noted]

| overcode concept | Claude Code | Codex CLI |
|---|---|---|
| Fresh launch | `claude [prompt]` | `codex [prompt]` |
| Model | `--model sonnet` | `-m/--model <model>` (e.g. `gpt-5.2-codex`; bare names — no provider prefix) |
| Bypass permissions | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` |
| Permissive | `--permission-mode dontAsk` | `-a never --sandbox workspace-write` — auto-approve but still sandboxed. **[VERIFY-P0 → ✅ confirmed]** `-a` accepts only `on-request`/`never` (`-a always` errors with the exact possible-values list); no dialog appears under `never`, but the sandbox itself still blocks out-of-workspace writes (silent failure reported back to the model, not a dialog). `--approve-for-me` is a genuine **third tier**: auto-reviews and silently approves (transient `Reviewing approval request (1s • esc to interrupt)` status line, no y/n dialog) — worth its own capability tier rather than folding into "permissive" |
| Normal | (default) | (default: `on-request` approval, sandbox per config) |
| Allowed tools | `--allowedTools a,b` | none — nearest is sandbox modes + `-c` config; ignore `--allowed-tools` like opencode does |
| Persona | `--agent name` | none observed. **[VERIFY-P0 → ✅ confirmed]** `-p/--profile` is documented as "Layer `$CODEX_HOME/<name>.config.toml` on top of the base user config" — a config-layer override, not a persona-by-name flag |
| Prescribe session id | `--session-id <uuid>` | ✗ — no flag exists |
| Resume | `--resume <id>` | `codex resume <SESSION_ID>` (subcommand; `--last` also exists) |
| Fork | `--resume <id> --fork-session` | `codex fork <SESSION_ID>` (subcommand) |
| Extra dirs / cwd | n/a | `-C/--cd <dir>`, `--add-dir` |
| Headless | `claude -p` | `codex exec [msg]` (`--json`, `--output-schema`). **New finding**: `codex exec` does **not** accept `-a` at all (`error: unexpected argument '-a' found`) — it always runs as `approval: never`; `build_command()` must never pass `-a` on the headless path |
| Graceful exit | `C-c`, `/exit` | **[VERIFY-P0 → ✅ confirmed]** both `/quit` and `/exit` exist in the `/` command menu (both labelled "exit Codex") and cleanly return to the shell. **`C-c` is confirmed UNSAFE** — a single bare `C-c` sent to an idle (non-generating) session killed the process instantly, no confirmation, tmux session destroyed within 2s. Never use it as codex's interrupt/exit gesture. The safe interrupt is **`Escape`** (`■ Conversation interrupted - tell the model what to do differently…`, process stays alive) |
| Clear conversation | `/clear` | **[VERIFY-P0 → ✅ confirmed]** `/new` ("start a new chat during a conversation") is present in the `/` menu |
| Approve / reject | `Enter` / `Escape` | **[VERIFY-P0 → ✅ confirmed]** from `permission_required.txt`: options are `1. Yes, proceed (y)` / `2. Yes, and don't ask again for commands that start with … (p)` / `3. No, and tell Codex what to do differently (esc)`; footer `Press enter to confirm or esc to cancel`. Approve = `y` or `Enter` (option 1 default-selected); reject = `Escape` (no literal `n` key) |
| Trust dialog | "I trust this folder" | **[VERIFY-P0 → ✅ confirmed]** `Do you trust the contents of this directory? › 1. Yes, continue / 2. No, quit`, footer `Press enter to continue` — `Enter` accepts. Trust persists per absolute path as `[projects."<path>"] trust_level = "trusted"` in `~/.codex/config.toml`; revisiting a trusted dir shows no dialog. Captured in `trust_dialog.txt` |

`build_command()` note: resume/fork produce `["codex", "resume", <id>, *common]`
— subcommand-first argv. `resume_args()` should return `["resume", <id>]` /
`["fork", <id>]` and `build_command()` must splice them **before** the shared
options, unlike claude/opencode where order is flag-appending. **[VERIFY-P0 →
✅ confirmed]** top-level options are accepted after the subcommand: live-ran
`codex resume 01a0439d-63b8-71d0-bf11-38fb10d0f551 -a never -m gpt-5.6-sol`
(a real session id from a probe run) and it launched cleanly with no
argument-parsing error, replaying the prior transcript. (Note: `-a` is
subcommand/top-level-only — `codex exec` rejects it, see §2.2.)

### 2.3 Telemetry: hooks first, notify fallback

**Hooks [VERIFIED from openai/codex source, `codex-rs/hooks/` + `config/src/hook_config.rs`]:**

- Feature flag `hooks` is **stable and enabled by default** (`codex features
  list`).
- 12 events, TOML keys renamed to PascalCase: `PreToolUse`,
  `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`,
  `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`,
  `SubagentStop`, `Stop`, `Interrupt`.
- Config sources: `hooks.json` in a layer's config folder, or `[hooks]` TOML
  in config layers (user `~/.codex/config.toml`, project, managed,
  requirements). Command hooks receive JSON input. **[VERIFY-P0 → ✅
  confirmed, exact shape captured live]**: stdin is a single JSON object,
  **snake_case** (Claude-shaped, not camelCase):
  ```json
  {"session_id":"01a043a2-f2fc-7f72-ac4a-6af740fcd4dc","turn_id":"01a043a3-05d4-7072-b885-22e30a6454e5","transcript_path":"/Users/mike/.codex/sessions/2026/08/27/rollout-2026-08-27T15-32-27-01a043a2-f2fc-7f72-ac4a-6af740fcd4dc.jsonl","cwd":"/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-codex","hook_event_name":"UserPromptSubmit","model":"gpt-5.6-sol","permission_mode":"default","prompt":"Reply with exactly: hook-tui-test"}
  ```
  `permission_mode` was also observed as `"bypassPermissions"` under
  `--dangerously-bypass-approvals-and-sandbox`. Since the keys are already
  Claude's vocabulary (`hook_event_name`, `session_id`, `permission_mode`),
  `overcode hook-handler` likely needs **little to no dialect translation**
  for codex — a smaller lift than the plan assumed, unlike grok's confirmed
  camelCase (§3.3).
- **Trust model:** per-hook `state` entries with `trusted_hash`;
  `--dangerously-bypass-hook-trust` "runs enabled hooks without requiring
  persisted hook trust … for automation that already vets hook sources".

**The injection question (the crux) [VERIFY-P0 → ✅ RESOLVED — Route 1
works], evidence below:**

1. **`codex -c 'hooks.UserPromptSubmit=[...]'` per-launch override +
   `--dangerously-bypass-hook-trust` — ✅ confirmed working.** Working
   invocation (both `codex exec` and the interactive TUI):
   ```
   OVERCODE_PROBE=1 codex -c 'hooks.UserPromptSubmit=[{hooks=[{type="command",command="cat >> WITNESS; echo ENV=$OVERCODE_PROBE >> WITNESS"}]}]' --dangerously-bypass-hook-trust
   ```
   **One shape correction to the plan**: `HookHandlerConfig::Command` in
   `codex-rs/config/src/hook_config.rs` defines `command: String` — a single
   shell command-line string, **not** an array like Claude's
   `command: [str, ...]`. The array form fails: `Error loading config.toml:
   invalid type: sequence, expected a string in 'hooks'`. Once fixed to a
   plain string, it fired immediately, on the first prompt, in both `exec`
   and the TUI. `--dangerously-bypass-hook-trust` is **required** — the
   identical `-c` config with the flag omitted produced zero hook firings,
   silently (tested twice). Env inheritance confirmed: the witness file's
   second line read `ENV=1`, proving `OVERCODE_PROBE=1` set before launch
   was visible inside the hook subprocess.
2. **Project-layer `.codex/hooks.json` — ✅ also works**, same trust
   semantics (silent no-op without the bypass flag, fires with it). Without
   any `-c`/bypass flag but with a `.codex/hooks.json` present, the
   **interactive** TUI shows an explicit hook-trust review dialog (not
   silently skipped, unlike `exec`):
   ```
     Hooks
     Lifecycle hooks from config and enabled plugins.
     ⚠ 1 hook needs review before it can run.
     Event ... UserPromptSubmit  1  0  1  When the user submits a prompt ...
     Press t to trust all; enter to review hooks; esc to close
   ```
   `t` trusts, but durably writes a
   `[hooks.state."<abs-path-to-hooks.json>:user_prompt_submit:0:0"]
   trusted_hash = "sha256:…"` entry into the user's **global**
   `~/.codex/config.toml` — i.e. the interactive-trust gesture pollutes
   global config, whereas Route 1's `--dangerously-bypass-hook-trust` does
   not. **Route 1 is therefore the cleaner mechanism** (zero global-file
   writes), exactly as the plan's preference order anticipated.
3. User-layer `~/.codex/hooks.json` — attempted once (per the "2-3 attempts
   max" discipline) with `--dangerously-bypass-hook-trust`; it did not fire
   (empty witness) and was not further diagnosed since Routes 1 and 2 already
   fully answer the injection question. **⚠️ inconclusive, deprioritized —
   not needed**: Route 1 is the implementation target.

Event mapping (overcode vocabulary is already neutral):

| codex event | overcode hook event | status |
|---|---|---|
| `UserPromptSubmit` | `UserPromptSubmit` | running |
| `PreToolUse` / `PostToolUse` | `PreToolUse` / `PostToolUse` | running |
| `PermissionRequest` | `PermissionRequest` | **waiting_approval** |
| `Stop` | `Stop` | waiting_user |
| `Interrupt` | (interrupt marker → downgrade running) | waiting_user |
| `SessionStart` | (record `agent_session_id`) | — |
| `SessionEnd` | `SessionEnd` | terminated |

The hook command should be `overcode hook-handler` itself if codex's stdin
JSON is close enough to Claude's, else a `--dialect codex` translation shim
(see §3.3 — build one dialect mechanism for both backends).

**Notify fallback [VERIFIED to exist: the user's own config carries
`notify = [<program>, "turn-ended"]`]:** coarse turn-ended pings; use only if
hooks injection fails entirely, combined with pane polling.

### 2.4 Stats: rollout JSONL reader [VERIFIED from real session files]

- Files: `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-ts>-<uuid>.jsonl`.
- Line 1: `{"type":"session_meta","payload":{"id","cwd","cli_version","originator","model_provider",…}}`.
- Token events: `{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens","cached_input_tokens","cache_write_input_tokens","output_tokens","reasoning_output_tokens","total_tokens"},"last_token_usage":{…},"model_context_window":…},"rate_limits":{…"plan_type":…}}}`.
- Mapping: input → `input_tokens`; output → `output_tokens` (+
  `reasoning_output_tokens`, matching the opencode convention); cache read →
  `cached_input_tokens`; cache write → `cache_write_input_tokens`; context →
  latest `total_token_usage.total_tokens` vs `model_context_window`;
  interactions → count of user `response_item` messages excluding
  scaffolding. **[VERIFY-P0 → ✅ confirmed, better signal than proposed]**:
  every `response_item` of `type: "message"` carries
  `payload.internal_chat_message_metadata_passthrough.content_item_kinds`, an
  array tagging the item's origin precisely — real user turns carry
  `["user.text"]`; injected `<environment_context>` scaffolding carries
  `["environments.environment_context"]`; injected system/skills scaffolding
  (role `"developer"`) carries `["host_skills.instructions",
  "permissions.instructions", "collaboration_mode.instructions", …]`. Real
  example (verbatim):
  ```json
  {"type":"response_item","payload":{"type":"message","id":"msg_01a0439d-8b4d-7b30-a8c0-71886681910d","role":"user","content":[{"type":"input_text","text":"count from 1 to 20 slowly explaining each number"}],"internal_chat_message_metadata_passthrough":{"turn_id":"01a0439d-8994-7550-a1b5-42182d032a57","create_time":1787840793.421791,"content_item_kinds":["user.text"]}}}
  ```
  Recommended filter: `type=="response_item" AND payload.type=="message" AND
  payload.role=="user" AND "user.text" in
  payload.internal_chat_message_metadata_passthrough.content_item_kinds` —
  more robust than string-matching the `<environment_context>`/
  `<user_instructions>` wrapper text, since it doesn't depend on that
  XML-ish scaffolding staying textually stable across codex releases.
  Model: **[VERIFY-P0 → ✅ confirmed]** lives in the `turn_context` event's
  payload, at `turn_context.payload.model` (duplicated at
  `turn_context.payload.collaboration_mode.settings.model`) — **not**
  reliably in `session_meta` (which only carries it indirectly, inside a
  free-text `base_instructions.provenance.model` field not meant for
  programmatic reads). One `turn_context` line exists per turn; take the
  latest for "current model."
- Cost: not stored (subscription); recompute via `pricing.py` (needs current
  OpenAI codex-model entries).
- Discovery: primary — the `SessionStart` hook records the session id into
  hook state (`agent_session_ids`), exactly like the opencode plugin; fallback
  — scan recent day-directories for `session_meta.cwd` == agent start dir
  within the launch window. `~/.codex/history.jsonl` (global
  `{"session_id","ts","text"}` prompt log) is a tertiary cross-check.
- Same defensive posture as `OpencodeStatsReader`: read-only, any surprise →
  None → dashes, schema drift → doctor finding.

### 2.5 Capability forecast (declare only what Phase 0 confirms)

`RESUME` ✅ · `FORK` ✅ · `HOOK_EVENTS` ✅ (injection route resolved — `-c
'hooks.<Event>=[...]'` + `--dangerously-bypass-hook-trust`, §2.3) ·
`TRANSCRIPT_STATS` ✅ · `SESSION_ID_PRESCRIPTION` ❌ · `PERMISSION_INJECTION` ❌ ·
`SKILLS` ❌ (codex has skills; no overcode integration — same stance as opencode) ·
`SANDBOX_PROBE` ❌ (n.b. codex has *its own* sandbox — the Claude loopback probe
still doesn't apply) · `SUBSCRIPTION_USAGE` ❌ (rate-limit/plan data *is* in the
rollout JSONL; a later enhancement could surface it, but the Anthropic-API
widget stays gated) · `AGENT_TEAMS` ❌.

---

## 3. Grok Build findings (v1.0.5)

### 3.1 Install & identity [VERIFIED]

- Already installed: `~/.grok/bin/grok` (on PATH), stable channel, auth in
  `~/.grok/auth.json`. Fresh installs: `curl -fsSL https://x.ai/cli/install.sh | bash`;
  requires SuperGrok or X Premium+.
- Binary override env: introduce `GROK_COMMAND`. Process basename: `grok`.
  **[VERIFY-P0 → ✅ confirmed]** via `pgrep -fl grok` while a probe session
  was alive (pid running `grok -m grok-4.5`) — no wrapper/child split like
  codex's, `grok` is the process basename directly.
- Config: `~/.grok/config.toml`. Note the user's config sets
  `[ui] permission_mode = "always-approve"` — launch flags must override
  config for overcode's modes to mean anything. **[VERIFY-P0 → ✅ confirmed
  definitively]**: launched with `--permission-mode default` and a live
  approval dialog appeared for `Run the command: echo hello`; launched
  without the flag (relying on the config's `always-approve`) and the same
  command ran silently, no dialog, footer showed the `always-approve` tag.
  The flag beats the config.
- Bundled offline docs at `~/.grok/docs/user-guide/` (hooks, sessions,
  headless, sandbox, permissions) — cite these, they version with the binary.

### 3.2 Flag mapping [VERIFIED from `--help`]

| overcode concept | Claude Code | Grok Build |
|---|---|---|
| Fresh launch | `claude [prompt]` | `grok [prompt]` |
| Model | `--model sonnet` | `-m/--model <id>` (bare ids: `grok-4.6`, `grok-4.5`; `grok models` lists) |
| Bypass permissions | `--dangerously-skip-permissions` | `--permission-mode bypassPermissions` |
| Permissive | `--permission-mode dontAsk` | `--permission-mode dontAsk` (accepted for compat). **[VERIFY-P0 → ❌ REFUTED]**: `dontAsk` is **not** aliased to `auto` — it shows the exact same full approval dialog as `default` (confirmed live, both produced identical `permission_required.txt`-style chrome). Only `--permission-mode auto` actually skips the dialog (footer shows bare `· auto`, no prompt for the same command). **`GrokBackend`'s permissive-mode mapping must target `auto`, not `dontAsk`.** |
| Normal | (default) | `--permission-mode default` |
| Allowed tools | `--allowedTools a,b` | `--allow <RULE>` repeatable — help says "compat alias: --allowedTools" → `PERMISSION_INJECTION` ✅. **[VERIFY-P0 → ✅ confirmed]**: `--allow 'Bash(echo *)'` (Claude-style `Tool(glob)` grammar) accepted with no parse error and **actually suppressed** the approval dialog live for a matching command under `--permission-mode default` — full round-trip verified, not just argument-parsing |
| Persona | `--agent name` | `--agent <name-or-file>` |
| Prescribe session id | `--session-id <uuid>` | `-s/--session-id <uuid>` — "for a **new** conversation; must not already exist" → `SESSION_ID_PRESCRIPTION` ✅. Round-trip confirmed live: minted uuid → `~/.grok/sessions/<percent-encoded-abs-cwd>/<uuid>/` appeared (encoding: full absolute path percent-encoded including the leading slash, `/`→`%2F`, e.g. `/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok` → `%2FUsers%2Fmike%2F.claude%2Fjobs%2Ff6bc7dbe%2Ftmp%2Fprobe-grok`) |
| Resume | `--resume <id>` | `-r/--resume <id-or-title>` |
| Fork | `--resume <id> --fork-session` | `--resume <id> --fork-session` (identical; `--session-id` names the forked session — better than Claude) |
| Headless | `claude -p` | `-p/--single` or `grok agent`; `--output-format streaming-messages-json` is literally "the Anthropic Messages API wire format" |
| Graceful exit | `C-c`, `/exit` | **[VERIFY-P0 → ✅ confirmed]**: `/quit` cleanly exits, printing a `grok --resume <uuid>` hint. **`C-c` is confirmed SAFE** — sent bare `C-c` mid-generation in a disposable session: interrupts only, process and session stayed alive, a follow-up prompt in the same session got a normal response. The opposite result from codex/opencode. Interrupt via `Escape`: a single press fully interrupts and settles the turn (`Turn cancelled by user in 4.3s.`); no second Escape needed |
| Clear conversation | `/clear` | **[VERIFY-P0 → ✅ confirmed]**: `/new` ("start new session") — there is no literal `/clear`, confirmed via the `/` command menu (`command_menu.txt`) |
| Approve / reject | `Enter` / `Escape` | **[VERIFY-P0 → ✅ confirmed]** from `permission_required.txt`: hint line `1/3:select │ Tab:next option │ Ctrl+o:always-approve │ Ctrl+c:cancel │ Esc:scrollback`; options `1` = "Yes, and don't ask again for anything (always-approve mode)", `2` = "Yes, proceed", `3` = "No, reject". Pressing the digit alone (no Enter) executes the choice immediately — confirmed `2` approved instantly |
| Trust dialog | "I trust this folder" | folder-trust exists for hooks/MCP (`--trust` flag, `/hooks-trust`) [VERIFIED]; whether a startup dialog appears. **[VERIFY-P0 → ✅ confirmed absent]**: launched plain `grok` in a brand-new, never-visited, git-initialized scratch dir with no hooks/MCP configured — chrome was byte-identical to `idle_fresh.txt`, no dialog at all (`trust_dialog.txt`). Folder trust silently gates hooks/MCP/LSP without an interactive startup prompt |

Watch out: grok has its own `--worktree` feature and its own background
tasks/subagents — overcode should not fight them; pass nothing and let users
opt in via `--backend-arg`.

### 3.3 Telemetry: Claude-compatible hooks with a camelCase dialect

[VERIFIED from `~/.grok/docs/user-guide/10-hooks.md`, which is exhaustive.]

- Hook sources (merged): global `~/.grok/hooks/*.json` (**always trusted**),
  project `.grok/hooks/*.json` (**requires folder trust** — avoid: overcode
  must not auto-trust folders), `~/.claude/settings.json` (compat scan!),
  `~/.grok/config.toml` `[[hooks.<Event>]]` TOML.
- **Chosen route: one global file `~/.grok/hooks/overcode.json`**, written by
  `prepare_launch()` with a marker field, registering `UserPromptSubmit`,
  `PreToolUse`, `PostToolUse`, `Stop`, `StopFailure`, `StopCancelled`,
  `SessionEnd`, and `Notification` (matchers `idle_prompt`,
  `permission_prompt`). The hook command is inert outside overcode sessions:
  hook processes inherit the grok process env, so the command guards on
  `OVERCODE_SESSION_NAME` being set (same inertness contract as the opencode
  plugin; document it identically). Global-but-inert beats project-scoped
  because project hooks need `--trust`, which grants MCP+LSP+hook trust to the
  whole folder — too big a side effect for overcode to take silently.
- **Dialect:** grok sends camelCase (`hookEventName`, `toolName`,
  `toolInput`, `toolUseId`) where Claude sends snake_case, and `toolResult`
  for `tool_response`. Extend `overcode hook-handler` with input-dialect
  auto-detection (presence of `hookEventName` ⇒ camelCase ⇒ normalize keys,
  map grok tool names via the alias table grok itself documents:
  `run_terminal_command`→`Bash`, `read_file`→`Read`, `search_replace`→`Edit`,
  `web_search`→`WebSearch`, `spawn_subagent`→`Task`, …). One mechanism, reused
  for codex if its stdin dialect differs too.
- Event mapping:

| grok event | overcode hook event | status |
|---|---|---|
| `UserPromptSubmit` | `UserPromptSubmit` | running |
| `PreToolUse` / `PostToolUse` | `PreToolUse` / `PostToolUse` | running |
| `Notification` matcher `permission_prompt` | `PermissionRequest` | **waiting_approval** |
| `Stop` (reason `end_turn` only) | `Stop` | waiting_user |
| `StopCancelled` | `Stop` (+ interrupt marker) | waiting_user |
| `StopFailure` | `StopFailure` | error |
| `Notification` matcher `idle_prompt` | `Stop` (idle backstop) | waiting_user |
| `SessionEnd` | `SessionEnd` | terminated |
| `SessionStart` | (record `agent_session_id`) | — |

  The hooks doc's own "complete busy/idle indicator takes five registrations"
  section (UserPromptSubmit + Stop + StopFailure + StopCancelled +
  idle_prompt backstop) is a precise spec for exactly overcode's problem —
  follow it, including: key on `promptId` and ignore stale turn reports;
  settle unconditionally when `promptId` is absent; exit early when
  `subagentType` is present (subagent events must not flip the main session's
  status); filter the session-end `Stop` by `reason`.
- Keep observe-hook timeouts short (grok default 5s); never register a
  blocking `Stop` gate.

**Live hook-firing verification [VERIFY-P0 → ✅ confirmed, exact camelCase
stdin captured]:** a witness hook (`~/.grok/hooks/overcode-probe.json`,
registering `UserPromptSubmit`/`Stop`/`Notification`) was run through one
full turn with `OVERCODE_PROBE=1` exported before launch, then deleted.
Confirmed camelCase, env inheritance (`OVERCODE_PROBE=1` visible in every
witness line), and the doc's `reason`-filter prescription (bold below):

```json
// UserPromptSubmit
{"hookEventName":"user_prompt_submit","sessionId":"01a043a2-...","cwd":"/Users/mike/.claude/jobs/f6bc7dbe/tmp/probe-grok","workspaceRoot":"...","timestamp":"2026-08-27T14:32:25.782754+00:00","promptId":"1f28e0e5-...","permissionMode":"default","prompt":"<user_query>\nRun the command: echo hookprobe\n</user_query>"}

// Notification, matcher permission_prompt (fired from a real live dialog)
{"hookEventName":"notification","sessionId":"01a043a2-...","cwd":"...","workspaceRoot":"...","timestamp":"...","transcriptPath":".../updates.jsonl","permissionMode":"default","notificationType":"permission_prompt","message":"Tool permission requested","level":"info"}

// Stop, genuine turn end
{"hookEventName":"stop","sessionId":"01a043a2-...","cwd":"...","workspaceRoot":"...","timestamp":"...","transcriptPath":"...updates.jsonl","promptId":"1f28e0e5-...","permissionMode":"default","reason":"end_turn","stopHookActive":false,"lastAssistantMessage":"`hookprobe`","backgroundTasks":[],"sessionCrons":[]}

// Stop, session-teardown fire (bonus finding, triggered by killing the tmux session)
{"hookEventName":"stop","sessionId":"01a043a2-...","cwd":"...","workspaceRoot":"...","timestamp":"...","permissionMode":"default","reason":"shutdown","stopHookActive":false}
```

The last example **confirms the doc's own filter prescription is load-bearing,
not theoretical**: session teardown fires a second `Stop` with
`reason:"shutdown"` (not `"end_turn"`) — any hook handler that doesn't check
`reason == "end_turn"` will double-settle/mis-settle on every session end.
`toolName`/`toolInput`/`toolResult` were not independently re-verified live
(only `UserPromptSubmit`/`Stop`/`Notification` were registered this pass);
the tool-name alias table above is taken directly from the bundled docs,
which the design doc already cites correctly.

### 3.4 Stats: fuller than expected [VERIFY-P0 → ❌ REFUTED (in grok's favor)]

[Originally verified from a real 413-message session under
`~/.grok/sessions/<url-encoded-cwd>/<session-uuid>/`; **Phase 0 live probe
corrects the headline finding below.**]

- Per-session dir contents: `chat_history.jsonl`, `events.jsonl` (phases,
  `tool_started/completed`, `permission_requested/resolved`,
  `turn_started/ended` — undocumented, treat as diagnostic only),
  `updates.jsonl` (persisted ACP `session/update` stream; `_meta.totalTokens`
  running total per update), `summary.json` (`current_model_id`,
  `num_messages`, timestamps, git info — **no token fields**),
  `prompt_history.jsonl` (per-project prompt log with session ids).
- **Correction: a full input/output/cost split DOES exist locally.** The
  original research found only a running `totalTokens` per update and
  concluded no split was available; a live probe of `updates.jsonl` found
  that its `turn_completed` update carries a full `usage` object:
  `{inputTokens, outputTokens, totalTokens, cachedReadTokens,
  cacheCreationTokens, reasoningTokens, modelCalls, apiDurationMs,
  costUsdTicks, modelUsage: {<model>: {...same fields per model...}}}`. This
  directly contradicts the original "no local input/output split was found"
  claim — the split was simply on a different event (`turn_completed`, not
  the running per-update `_meta.totalTokens`) than the original research
  read. **`costUsdTicks`'s unit scale was not cross-checked against a priced
  invoice** — one sample turn (~13.6k input, 31 output, high reasoning
  effort) showed `costUsdTicks: 113440000`, consistent with nano-dollars
  (≈$0.113) but treat that scale as ⚠️ unconfirmed until Phase 4 verifies it
  against a real billed amount.
- `GrokStatsReader` therefore: interactions ← `prompt_history.jsonl` count for
  the session id; context ← latest `_meta.totalTokens` from `updates.jsonl`;
  model ← `summary.json.current_model_id`; **input/output/cost ← the
  `turn_completed.usage` object above, summed across turns for the session
  — not None/dashes as originally planned.** Re-scope Phase 4's
  `TRANSCRIPT_STATS` declaration from "⚠️ partial" toward "✅ full" pending
  the `costUsdTicks` scale confirmation. Declare `TRANSCRIPT_STATS` only once
  the columns actually fill honestly; the seam already handles any remaining
  partial `AgentSessionStats` fields as dashes.
- `grok trace --help` and `grok export --help` were checked as the doc
  prescribed but are not needed now that `updates.jsonl` itself has the
  split; no further token-split hunting required.
- Session location is trivially prescribable: overcode mints the uuid via
  `--session-id`, so the reader keys straight into
  `sessions/<percent-encoded-abs-cwd>/<uuid>/` (see §3.2 for the exact
  encoding) — no discovery problem at all.

> **Phase 4 shipped-notes (Aug 28, 2026).** The two items §3.4 above left
> unconfirmed were determined empirically against a real 413-message session
> (`01a015cb-...` under the xway project's `~/.grok/sessions/` directory)
> before `GrokStatsReader` was written, per this phase's brief:
>
> 1. **Per-turn, not cumulative.** The original phrasing ("determine
>    empirically whether usage is per-turn or cumulative") is now resolved:
>    `turn_completed.usage` objects in a real multi-turn `updates.jsonl` are
>    **not** monotonically increasing across the file — the observed
>    `inputTokens` sequence for that session's seven `turn_completed` events
>    was 4,130,868 → 89,480 → 452,829 → 557,890 → 743,659 → 230,282 →
>    116,561, tracking `numTurns`/`modelCalls` the same non-monotonic way.
>    A "latest wins" reader (the codex convention, correct there because
>    codex's `token_count` events genuinely are cumulative) would have badly
>    undercounted grok's totals. `GrokStatsReader` therefore **sums** every
>    `turn_completed.usage` object in the session.
> 2. **`costUsdTicks` is nano-dollars** (1e9 ticks per USD), not the
>    millionths (1e6) the single sample in §3.4 above was consistent with but
>    hadn't ruled out. Cross-checked against the same real session: a small
>    batch (~13.6k input tokens, heavy reasoning effort) priced at
>    113,440,000 ticks → $0.11, and the session's largest batch (4.13M input
>    tokens, 3.34M of it cached, 137k output+reasoning tokens) priced at
>    7,295,125,400 ticks → $7.30 — both land in a plausible dollar-per-token
>    range for grok's published pricing. The millionths reading would have
>    put the second figure at $7,295 for one batch of turns, which is not
>    plausible for the token counts involved.
>
> One more correction, not previously flagged as uncertain: `_meta.totalTokens`
> (the running context-size proxy) is **not** at the envelope's top level —
> it lives at `params._meta.totalTokens`, nested inside `params` alongside
> `update`. An initial read of the same real session (recursive key search
> across the whole line) misattributed a match to the top level; a direct
> check found zero top-level `_meta` keys and 333 `params._meta.totalTokens`
> occurrences in the same file. `GrokStatsReader` reads the latter.
>
> Full detail, including the reader's SQL-free per-file scan and the doctor
> schema-drift check, lives in `src/overcode/backends/grok_stats.py`'s module
> docstring and `docs/backends.md`'s "Stats: grok's updates.jsonl /
> summary.json / prompt_history.jsonl" section.

### 3.5 Capability forecast

`RESUME` ✅ · `FORK` ✅ · `SESSION_ID_PRESCRIPTION` ✅ · `PERMISSION_INJECTION` ✅
(rule-syntax confirmed live, §3.2) · `HOOK_EVENTS` ✅ (stdin shapes confirmed
live, §3.3) · `TRANSCRIPT_STATS` ✅ **upgraded from ⚠️ partial** — §3.4's
Phase-0 correction found a full local input/output/cost split, pending only
the `costUsdTicks` unit-scale confirmation ·
`SKILLS` ❌ (grok has skills + a marketplace; unintegrated) · `SANDBOX_PROBE` ❌ ·
`SUBSCRIPTION_USAGE` ❌ · `AGENT_TEAMS` ❌.

---

## 4. Risks

1. **Codex hook injection needs `--dangerously-bypass-hook-trust`. [Phase 0 →
   ✅ RESOLVED, risk realized but contained.]** Confirmed live: without the
   flag, the identical `-c 'hooks...'` config produces zero hook firings,
   silently. With it, Route 1 (`-c` override + the flag, no files written)
   works cleanly on the first prompt, in both `exec` and the TUI, with
   confirmed env inheritance. The flag's name is still radioactive — surface
   it plainly in `overcode show`/docs as planned. (The file-based fallback,
   Route 2, also works but durably writes a trust entry to the user's
   *global* `~/.codex/config.toml` on manual accept — Route 1 avoids that
   entirely and is the implementation target.)
2. **Codex release cadence.** npm shows multiple releases/week (0.148→0.150 in
   days). Same mitigations as opencode: `TESTED_CODEX_RANGE`, doctor version
   check, committed pane corpus, polling fallback.
3. **Grok subscription gating.** Grok Build needs SuperGrok/X Premium+; CI and
   other machines cannot assume it. All tests must run against the mock; live
   verification is a manual Phase 0 step on this machine.
4. **Grok config-vs-flag precedence. [Phase 0 → ✅ RESOLVED, flag wins.]**
   Confirmed live: with `--permission-mode default` passed explicitly, a real
   approval dialog appeared for a test command despite the config's
   `always-approve`; omitting the flag reproduced the silent auto-approve.
   `GrokBackend.build_command()` must still pass the mode explicitly on every
   launch (never rely on the config default) — that discipline is now a
   confirmed requirement, not a hedge.
5. **Four TUIs' chrome to track.** Each corpus is a snapshot; doctor version
   ranges + the `autoupdate`-style warnings are the containment. **[Phase 0 →
   partially resolved]** Codex: `codex features list` shows `in_app_updates
   stable true` — an in-app update mechanism is **enabled by default**
   (⚠️ exact trigger cadence not directly observed; treat as "on" for a
   doctor warning). Grok: no auto-update toggle found in `grok update --help`
   or `~/.grok/config.toml`, and no background update chatter was observed
   during probing (⚠️ inferred no, not exhaustively watched over a long
   session). Add a codex doctor warning; grok's warning is lower priority
   pending stronger evidence either way.
6. **Shared-file merge conflicts if phases run in parallel.** Codex and Grok
   phases both touch `backends/__init__.py`, docs, the feature table, and
   e2e conftest. Run backend tracks sequentially, or in worktrees with the
   Grok track rebasing after each Codex phase lands.

---

## 5. Phased Implementation Plan

Ground rules (unchanged from the opencode plan, plus one):

- Every phase leaves `main` shippable: full unit suite green
  (`COLUMNS=200 TERM=dumb NO_COLOR=1 uv run pytest tests/unit -q` is the
  environment-stable invocation on this machine), Claude/opencode behavior
  untouched, argv for existing backends byte-identical.
- Corpus before patterns; live verification before flags; capability flags
  declare only what a phase verified.
- No renames, no refactors of the seam itself — these are adapter phases. If
  the seam genuinely can't express something (it shouldn't happen; codex
  subcommands fit through `build_command`), stop and flag rather than
  reshaping the protocol mid-phase.
- Each phase brief below is self-contained for handoff; the implementing
  agent should read this doc, `docs/design/agent-agnostic-backends-opencode.md`
  §2 + Appendix A, `backends/opencode.py` (the template), and the files the
  phase names.

### Phase 0 — Live verification + pane corpora (no src changes)

**Objective:** convert every [VERIFY-P0] above into a ✅/❌/⚠️ verdict recorded
in **Appendix A (Codex)** and **Appendix B (Grok)** of this document, and
commit pane-capture corpora for both TUIs.

**Method:** drive the real CLIs inside tmux (`tmux new-session -d`,
`send-keys`, `capture-pane -p`), exactly as the opencode corpus was built.
Use cheap models (`-m gpt-5.1-codex-mini` or similar for codex; `grok-4.5`
for grok) and trivial prompts; total spend is a few cents / subscription
minutes.

**Work items:**
1. **Codex corpus** → `tests/fixtures_codex_panes/`: fresh-idle, busy/spinner,
   permission/approval dialog, idle-after-response, error (bad model name),
   interrupted, exited-shell, trust-dialog (run in a never-visited dir), plus
   a `README.md` documenting capture conditions (version, model, terminal
   size), mirroring `tests/fixtures_opencode_panes/README.md`.
2. **Grok corpus** → `tests/fixtures_grok_panes/`: same state list. Grok's
   default may be the "minimal"/inline UI — capture both `--fullscreen` and
   default, decide which overcode standardizes on (recommend passing the
   explicit flag at launch so chrome is deterministic).
3. **Codex flag verdicts:** subcommand+options ordering; `-a` accepted values;
   permissive mapping; `/quit` vs `/exit`, `/new`; approval-dialog keys;
   whether `C-c` is safe; trust-dialog handling; hooks injection route (test
   `-c 'hooks...'` + `--dangerously-bypass-hook-trust` with a hook that
   writes a witness file; test env inheritance of `OVERCODE_*`); notify
   events; stdin JSON shape of each hook event (fetch the generated schemas
   from `codex-rs/hooks/schema/generated/` and confirm against a live fire);
   model field location in rollout JSONL; `ps` basename.
4. **Grok flag verdicts:** `--session-id` prescription round-trip (launch with
   minted uuid → confirm `sessions/<enc>/<uuid>/` appears); flag-vs-config
   permission precedence; `--allow` rule syntax; `dontAsk` acceptance;
   exit/clear slash commands; approval keys; hook firing with a witness hook
   in `~/.grok/hooks/` (confirm env inheritance, camelCase payload, tool-name
   aliases, `Notification` matchers); token-split hunt (`grok trace --help`,
   `grok export`, newer session formats); auto-update behavior; `ps` basename.
5. Update this document's §2/§3 tables in place with verdicts + write
   Appendices A/B (the mapping tables with per-row verdict marks, opencode
   Appendix-A style).

**Scope fence:** no `src/` changes. Deliverables are fixtures + this doc.
**Acceptance:** both fixture dirs committed with READMEs; zero remaining
[VERIFY-P0] tags in §2/§3; appendices list every gesture/flag with a verdict
and a capture or command-output citation.

### Phase 1 — Codex backend MVP (launch + polling status)

**Objective:** `overcode launch -n x -B codex` works end-to-end with
pane-polling status; restart/revive/resume/fork/kill/send all work; Claude,
opencode users see zero change.

**Key files:** new `backends/codex.py`; `backends/__init__.py` (register);
`tests/mock_codex.py` + `tests/unit/test_mock_codex.py` (build on
`tests/mock_agent_lib.py`); `tests/unit/test_backend_codex.py` (golden argv
matrix: fresh/resume/fork × modes × model × extra-args, keyed to Phase 0
verdicts); `tests/unit/test_status_detector_codex.py` replaying
`tests/fixtures_codex_panes/`; e2e conftest wiring for `CODEX_COMMAND`.

**Work items:**
1. `CodexBackend` per §2.2/§2.5 with Phase-0-verified gestures and dialog
   rules; capabilities: `RESUME | FORK` only (no `HOOK_EVENTS`/
   `TRANSCRIPT_STATS` until Phase 2). `prepare_launch()` is a no-op this
   phase. `CODEX_COMMAND` override; `CodexNotFoundError` with npm install
   hint.
2. `CODEX_PATTERNS` `StatusPatterns` instance from the corpus; add contract
   coverage to `tests/unit/test_status_detector_contract.py`.
3. Mock codex TUI emitting corpus-accurate chrome for: launch-idle,
   permission dialog, busy, error. Scenario YAMLs for the e2e flows the
   claude/opencode mocks already cover.
4. Doctor: `TESTED_CODEX_RANGE` constant + version check + (if Phase 0 found
   auto-update on by default) an update-channel warning, gated on fleet
   containing a codex agent.
5. `docs/backends.md`: add codex column stub to both tables, marked
   as pane-polling-only with hooks not yet landing until the next phase, so
   docs never overstate. (Shipped by Phase 2, since superseded — see that
   phase's brief below and `docs/backends.md`'s current codex sections.)

**Acceptance:** live smoke on this machine: launch, watch status turn
green→idle correctly, send instruction, approve a permission prompt via
`overcode send <n> approve`, restart, fork, kill; full unit suite green; e2e
mock suite green.

### Phase 2 — Codex telemetry: hooks + stats

> **Shipped Aug 27, 2026 — one live-smoke correction to this brief's "full
> token/cost/context columns" framing.** Tokens and context populate exactly
> as planned. Cost does not render as dashes when no codex `pricing.py`
> entry exists, as the original brief assumed (and as `CodexStatsReader`
> itself correctly does — it always returns cost as unrecoverable/omitted).
> The dash never reaches the UI because `monitor_daemon.py`'s cost
> estimator (`settings.get_model_pricing` → `_get_list_pricing`) was
> already, app-wide, falling back to the user's *configured default*
> per-token price for any unrecognized model on any backend — live-verified
> during Phase 2 smoke testing, where a codex agent's cost column showed a
> real, non-zero dollar figure priced at the account's default (Sonnet-rate)
> model rather than a placeholder. This is pre-existing, backend-generic
> behavior, not a Phase 2 defect, and out of this phase's scope to change
> (no codex-specific model prices were added to `MODEL_PRICING`, per the
> brief's own "if unsure of a price, omit the entry" instruction — omitting
> the entry just means the *fallback* price applies, not that the column
> goes blank). `docs/backends.md` was corrected to describe this accurately
> rather than repeat the "shows dashes" assumption.

**Objective:** hooks-grade status (incl. `waiting_approval`) and full
token/cost/context columns for codex agents.

**Key files:** `backends/codex.py`; `backends/codex_stats.py` (new);
`cli/hooks.py` / `hook_handler.py` (dialect normalization if Phase 0 showed
codex stdin ≠ Claude stdin); `tests/unit/test_codex_stats.py` (fixture
rollout files: normal, schema-drift, multi-session-same-cwd);
`tests/unit/test_opencode_plugin.py`-style tests for whatever injection
artifact exists.

**Work items:**
1. Implement the Phase-0-chosen injection route in
   `env_prefix()`/`build_command()` (route 1: `-c` overrides + bypass-trust
   flag) or `prepare_launch()` (routes 2/3: written hook file with marker,
   idempotent, env-guarded inert). Record session id from `SessionStart` into
   hook state (`agent_session_ids`).
2. Dialect handling in the hook handler if needed (shared mechanism with
   Phase 4 — auto-detect key style, normalize; keep overcode's on-disk
   hook-state schema byte-compatible).
3. `CodexStatsReader` per §2.4: id-keyed via hook state; cwd+time fallback;
   `history.jsonl` cross-check; cost recomputed via `pricing.py` (add current
   codex model prices); every failure → None; drift → doctor finding.
4. Flip capabilities: `| HOOK_EVENTS | TRANSCRIPT_STATS`. Per-session
   dispatch picks hooks mode automatically when state files are fresh.
5. `health_verdict`/`refine_health_verdict`: define "observability injected"
   for the chosen route (argv contains the `-c hooks` override, or the hook
   file exists) → `missing-settings` verdict otherwise.
6. Supervisor: backend-aware unblock recipe for codex's approval dialog in
   `daemon_claude_skill.md` (pattern from the opencode section).

**Acceptance:** live: codex agent shows hooks-mode status detail, permission
prompt flips to waiting_approval and `overcode send approve` clears it;
tokens/cost/context populate within one daemon tick of a turn; budgets
enforce; doctor healthy; killing the injection artifact degrades to polling
with a doctor `missing-settings` verdict, not a crash.

### Phase 3 — Grok backend MVP (launch + polling status)

**Objective:** `overcode launch -n x -B grok` end-to-end with polling status
and — uniquely — session-id prescription and permission injection from day
one, since both are launch-flag-shaped.

**Key files:** new `backends/grok.py`; registry; `tests/mock_grok.py`;
`tests/unit/test_backend_grok.py`; `tests/unit/test_status_detector_grok.py`
replaying `tests/fixtures_grok_panes/`; e2e conftest (`GROK_COMMAND`).

**Work items:**
1. `GrokBackend`: mode mapping `bypass→bypassPermissions`,
   `permissive→dontAsk` (or Phase 0's verdict), `normal→default` — passed
   explicitly on every launch (risk 4); `--allowed-tools` → repeated
   `--allow` rules (translate csv → rules per Phase 0 syntax);
   `--session-id` prescription wired to overcode's existing
   prescribed-session-id path (first non-Claude backend to use it — the
   launcher already gates on `SESSION_ID_PRESCRIPTION`); resume/fork via
   `--resume <id> [--fork-session --session-id <new>]` (prescribe the fork's
   id too); `--agent` persona passthrough; deterministic UI flag from Phase 0
   (`--fullscreen` or default) so the pattern set has one chrome to match.
   Capabilities this phase: `RESUME | FORK | SESSION_ID_PRESCRIPTION |
   PERMISSION_INJECTION`.
2. `GROK_PATTERNS` from corpus + contract test registration.
3. Mock grok TUI + scenarios; e2e flows.
4. Doctor: `TESTED_GROK_RANGE`; subscription-absent detection (binary present
   but auth missing → actionable finding naming `grok login`).
5. `docs/backends.md` grok column stub, honesty-marked.

**Acceptance:** live smoke as Phase 1's, plus: prescribed session id appears
as `sessions/<enc>/<uuid>/` on disk and `overcode show` displays it; fork
produces a distinct prescribed id; suite green.

### Phase 4 — Grok telemetry: hooks + partial stats

**Objective:** hooks-grade status for grok; the honest subset of stats
columns.

**Key files:** `backends/grok.py`; `backends/grok_stats.py`;
`hook_handler.py` (camelCase dialect — shared mechanism from Phase 2);
`tests/unit/test_grok_hooks.py` (golden hook-file content, marker/clobber
semantics, dialect translation table incl. grok→Claude tool-name aliases);
`tests/unit/test_grok_stats.py` (fixture session dirs).

**Work items:**
1. `prepare_launch()` writes `~/.grok/hooks/overcode.json` (marker field,
   never clobber a de-markered file, idempotent, §3.3 registrations,
   short timeouts, no blocking gates). Hook command guards on
   `OVERCODE_SESSION_NAME` (inert outside overcode — document identically to
   the opencode plugin's footprint section, including "not removed on agent
   death" and "delete freely, we recreate").
2. Hook-handler dialect: camelCase normalization + tool-name alias table +
   grok-specific rules from §3.3 (promptId staleness, `subagentType` early
   exit, `Stop` reason filter, `StopCancelled` → interrupt marker,
   `Notification(permission_prompt)` → `PermissionRequest`,
   `Notification(idle_prompt)` → idle backstop).
3. `GrokStatsReader` per §3.4 keyed by prescribed id: model, interactions,
   context (totalTokens); tokens/cost None unless Phase 0 found a split.
   Capabilities: `| HOOK_EVENTS` and `TRANSCRIPT_STATS` only per that verdict.
4. `refine_health_verdict`: hook file present + marker → healthy; else
   `missing-settings`.
5. Supervisor unblock recipe for grok's permission dialog.

**Acceptance:** live: grok agent shows waiting_approval on a permission
prompt and clears via gesture; interrupt (Esc) shows waiting_user not stuck
running; a second, non-overcode `grok` session in another terminal fires no
overcode hooks (inertness proof); stats columns show exactly the declared
subset, dashes elsewhere; suite green.

### Phase 5 — Hardening, docs, release 0.6.0

**Objective:** four-backend polish; nothing new, everything honest.

**Work items:**
1. `docs/backends.md`: full codex/grok columns in the "Feature support at a
   glance" table (TUI-key rows), support matrix, flag-mapping table (now 4
   columns), telemetry + stats sections for both, footprint notes
   (`~/.grok/hooks/overcode.json`, codex's injection artifact), doctor
   section. README + `docs/README.md` blurbs: "Claude Code, opencode, Codex,
   Grok". `docs/architecture.md` §Agent Backends refresh.
2. Devcontainer: `OVERCODE_BACKEND` cases for `codex`
   (`npm i -g @openai/codex`) and `grok` (curl installer; document that grok
   auth inside a container needs a mounted `~/.grok/auth.json` or is
   unsupported — verdict from a single container smoke test).
3. Cross-backend sweep: `overcode doctor` on a 4-backend fleet; BKD column
   badges distinct; new-agent modal cycles all four; supervisor context lines
   name each backend; `overcode send approve/reject` resolves per backend.
4. Update this doc's status header to Shipped + divergence notes (the
   opencode doc's convention); release notes `docs/release-notes-0.6.0.md`;
   `AUDIT.md` entry; bump `pyproject.toml` to 0.6.0.
5. E2e: one mixed-fleet scenario with all four mocks alive in one dashboard.

**Acceptance:** suite + e2e green; docs tables complete and each ✅ traceable
to a phase acceptance run; 0.6.0 tagged notes drafted (not pushed until
review).

---

## 6. Sizing & sequencing

| Phase | Size | Risk | Gate |
|---|---|---|---|
| 0 Verification + corpora | M — mostly live driving + doc edits | Low (no src) | Zero [VERIFY-P0] left; fixtures committed |
| 1 Codex MVP | M-L | Medium (first subcommand-grammar backend) | Live smoke + suites green |
| 2 Codex telemetry | M-L | Medium-high (injection route) | Hooks-grade live acceptance |
| 3 Grok MVP | M | Low-medium (grammar is Claude-shaped) | Prescription round-trip + smoke |
| 4 Grok telemetry | M | Medium (dialect + event subtleties) | Inertness + waiting_approval live |
| 5 Hardening + 0.6.0 | M | Low | Mixed-fleet e2e + docs audit |

Strict order: 0 → 1 → 2 and 0 → 3 → 4 (grok track depends only on Phase 0 +
the shared dialect mechanism, which Phase 2 builds first — if tracks run in
parallel, land the dialect shim early in Phase 2 or let Phase 4 build it).
Phase 5 last. Pragmatic descopes if needed: ship codex polling-only (defer
Phase 2), or ship grok without stats columns — capability gating makes both
tiers honest by construction.

---

## Ancillary (post-Phase-5): true bypass-permissions for opencode

> **Shipped Aug 28, 2026.** `OpencodeBackend.env_prefix()` now sets
> `OPENCODE_PERMISSION` to an allow-everything JSON blob for **bypass** mode
> only (`dangerously_skip_permissions` or `permissiveness_mode == "bypass"`);
> permissive stays `--auto` alone, unchanged. The allow-everything blob's key
> set was re-derived from opencode's own published schema
> (`https://opencode.ai/config.json`, `$defs.PermissionConfig`) rather than
> the partial list ("`bash`, `edit`, `webfetch`, …") the Phase 0 pass below
> had observed: the real schema has 15 keys (`read`, `edit`, `glob`, `grep`,
> `list`, `bash`, `task`, `external_directory`, `lsp`, `skill`, `todowrite`,
> `question`, `webfetch`, `websearch`, `doom_loop`), all forced to `"allow"`.
> Re-verified live (opencode v1.18.23, a fresh scratch project distinct from
> Phase 0's) via `opencode debug config`: a project `opencode.json` denying
> `bash`/`edit`/`webfetch` still showed those three as `"deny"` in the
> resolved config until `OPENCODE_PERMISSION` was set, at which point all
> three flipped to `"allow"`. One correction to the paragraph below's
> "`"*"` treat as unconfirmed shorthand": it's now confirmed **not** to work
> as a wildcard override — `OPENCODE_PERMISSION='{"*":"allow"}'` against the
> same deny-rule project left `bash`/`edit`/`webfetch` at `"deny"` and simply
> added a literal `"*": "allow"` key alongside them; the schema's per-key
> `additionalProperties` acceptance is why it doesn't error, not evidence it
> means "everything." Explicit per-tool keys remain the only implementation
> target. See `docs/backends.md`'s "Permission modes" section for the
> user-facing writeup and `tests/unit/test_backend_opencode.py::TestEnvPrefix`
> for the golden env matrix (bypass gets the var, permissive/normal don't).

> **Verified Aug 27, 2026 (Phase 0 pass, opencode v1.18.23).** All three
> verify-first items below are resolved, and the answer is better than the
> "if only a project-file route exists…" fallback anticipated: a clean,
> file-free, per-process env-var route exists and was live-verified via
> `opencode debug config` (a resolved-configuration dump command not
> mentioned in the original research).

Shipped opencode behavior collapses both `permissive` and `bypass` onto
`--auto`, under which opencode's own `"deny"` rules still win — there is no
real `--dangerously-skip-permissions` analogue. Requested improvement: in
**bypass** mode only, have `prepare_launch()` materialize an allow-everything
permission config for the launched process so deny rules cannot block it.

**Verify-first items — verdicts:**
- **`OPENCODE_CONFIG=/path/to/file.json` is honored** ✅ — confirmed live: a
  scratch project with no `permission` key in its `opencode.json`, launched
  with `OPENCODE_CONFIG` pointing at a file setting `permission.bash: allow`,
  showed that key in `opencode debug config`'s resolved output. It **merges,
  does not replace**: in the load order it is applied *before* the project's
  `opencode.json`/`.jsonc` files, so project-file keys win on conflict but
  keys the project file doesn't set do come through from the env file
  (confirmed: setting a conflicting `username` in the env-config file did
  **not** override the project's `username`, but the non-conflicting
  `permission.bash` key did come through). Two siblings, same semantics:
  `OPENCODE_CONFIG_CONTENT` (inline JSON string instead of a file path,
  applied even later — after project files) and `OPENCODE_CONFIG_DIR` (an
  additional directory scanned for `opencode.json`/`.jsonc`, like a project
  `.opencode` dir).
- **A dedicated, better-fit env var exists: `OPENCODE_PERMISSION`** ✅ new
  finding, not anticipated by the original research. It's a JSON blob merged
  into the `permission` config at the very *end* of the resolution pipeline —
  after project config, not before — and empirically **does override
  project-level deny rules**, unlike `OPENCODE_CONFIG`. Live proof: with a
  scratch project's `opencode.json` setting `{"permission":{"bash":"deny",
  "edit":"deny"}}`, launching with `OPENCODE_PERMISSION='{"bash":"allow",
  "edit":"allow"}'` produced a resolved config (`opencode debug config`)
  showing `bash: allow, edit: allow` — the project's deny rules did not win.
  This is exactly the "allow-everything for bypass mode" mechanism the
  ancillary item was hunting for, and it needs **zero file writes** —
  `prepare_launch()`/`env_prefix()` can set it per-process for bypass-mode
  launches only, with no marker file, no project pollution, nothing to clean
  up.
- **Permission grammar**: per-tool keys with values `allow`/`deny`/`ask`
  (confirmed keys observed in the binary: `bash`, `edit`, `webfetch`, …,
  `PermissionLevel` enum). `opencode debug config` did not reject an
  unrecognized `"*"` wildcard key either (echoed back verbatim), but its
  semantic meaning as a true "allow everything" catch-all was not separately
  confirmed against the schema — **explicit per-tool keys are the
  Phase-0-verified-safe grammar**; treat `"*"` as unconfirmed shorthand.
- **Footprint**: fully env-based, so the "leaves the user's own sessions and
  files untouched" bar is trivially met — no files are written or read
  beyond what `OPENCODE_CONFIG`/`OPENCODE_PERMISSION` themselves point at,
  and neither is set unless overcode launches the process itself.

**Recommendation**: implement the ancillary improvement via `env_prefix()`
setting `OPENCODE_PERMISSION` to an allow-everything JSON blob (built from
the tool-key set opencode's schema recognizes) for **bypass** mode launches
only — no `OPENCODE_CONFIG` file needed, no project `.opencode` writes, no
marker/never-clobber ceremony required at all.

---

## Appendix A — Codex CLI verified mapping (Phase 0)

**Status: verified against a live Codex CLI 0.150.1 during Phase 0 (Aug 27,
2026, macOS/arm64, npm install, model `gpt-5.6-sol` — the account default;
`gpt-5.1-codex-mini` was rejected under this machine's ChatGPT-subscription
auth with an inline `invalid_request_error`, not an argv error). Every row
below is marked ✅ confirmed, ❌ refuted, or ⚠️ partially/inconclusively
verified. The pane corpus the behavioural rows were read from is committed
at `tests/fixtures_codex_panes/`.

| overcode concept | Claude Code | Codex CLI | Verdict |
|---|---|---|---|
| Binary | `claude` (`CLAUDE_COMMAND`) | `codex` (`CODEX_COMMAND`, new) | ✅ |
| Process basename in `ps` | `claude` | parent is `node /opt/homebrew/bin/codex`; it execs a vendored binary whose basename is `codex` (the one actually running the TUI) | ✅ confirmed via `ps`/`pgrep` |
| Model | `--model sonnet` | `-m/--model <model>` — account default is `gpt-5.6-sol`, not `gpt-5.2-codex` as originally guessed; `gpt-5.1-codex-mini` rejected under ChatGPT-subscription auth | ✅ (model id corrected) |
| Bypass permissions | `--dangerously-skip-permissions` | `--dangerously-bypass-approvals-and-sandbox` | ✅ confirmed: banner showed `sandbox: danger-full-access`, zero prompts for a test command |
| Permissive (sandboxed) | `--permission-mode dontAsk` | `-a never --sandbox workspace-write` | ✅ confirmed: no approval dialog, but the sandbox itself still blocks out-of-workspace writes (silent failure reported back to the model, no dialog) |
| Auto-review tier (new, not in Claude) | n/a | `--approve-for-me` | ✅ confirmed a genuine third tier: auto-reviews and silently approves (transient `Reviewing approval request (1s • esc to interrupt)` status, no y/n dialog ever shown) |
| `-a` accepted values | n/a | `on-request` / `never` only | ✅ confirmed — `-a always` errors with the exact possible-values list |
| `-a` on `codex exec` | n/a | ❌ not accepted at all (`error: unexpected argument '-a' found`); `exec` always behaves as `never` | ❌ new finding — headless `build_command()` path must never pass `-a` |
| Allowed tools | `--allowedTools a,b` | none — sandbox modes + `-c` config only | ✅ (absent from `--help`, no live test needed) |
| Persona | `--agent name` | none — `-p/--profile` layers `$CODEX_HOME/<name>.config.toml`, a config layer, not a persona | ✅ confirmed from `--help` text |
| Prescribe session id | `--session-id <uuid>` | ✗ no flag | ✅ (unchanged from prior research) |
| Resume | `--resume <id>` | `codex resume <id>` (subcommand); top-level options (`-m`, `-a`, `-s`, `-c`, …) accepted **after** the subcommand | ✅ confirmed live: `codex resume <real-id> -a never -m gpt-5.6-sol` launched cleanly |
| Fork | `--resume <id> --fork-session` | `codex fork <id>` (subcommand, same grammar as resume) | ✅ (unchanged) |
| Graceful exit | `C-c`, `/exit` | `/quit` or `/exit` (both in the `/` menu, both labelled "exit Codex") | ✅ confirmed |
| Bare `C-c` | safe in Claude | ❌ **kills the process instantly, no confirmation** — confirmed on an idle (non-generating) session, tmux session destroyed within 2s | ❌ refuted — never use as codex's interrupt/exit gesture |
| Interrupt (safe gesture) | n/a | `Escape` — `■ Conversation interrupted - tell the model what to do differently…`, process stays alive | ✅ confirmed, this is the safe gesture |
| Clear conversation | `/clear` | `/new` ("start a new chat during a conversation") | ✅ confirmed present in `/` menu |
| Approve / reject keys | `Enter` / `Escape` | approve = `y` or `Enter` (option 1, default-selected); reject = `Escape` (option 3; **no literal `n` key**) | ✅ confirmed, exact hint text captured in `permission_required.txt` |
| Trust dialog | "I trust this folder" | `Do you trust the contents of this directory? › 1. Yes, continue / 2. No, quit` — `Enter` accepts; persists per-path as `[projects."<path>"] trust_level="trusted"` in `~/.codex/config.toml` | ✅ confirmed, captured in `trust_dialog.txt` |
| Hook stdin dialect | snake_case | snake_case (`hook_event_name`, `session_id`, `turn_id`, `transcript_path`, `cwd`, `model`, `permission_mode`, `prompt`) | ✅ confirmed Claude-shaped — little/no dialect translation likely needed, unlike grok |
| Hook injection route | `--settings '<json>'` | `-c 'hooks.<Event>=[{hooks=[{type="command",command="<single-string-shell-cmd>"}]}]' --dangerously-bypass-hook-trust` | ✅ **works** — resolves the plan's single biggest open risk; `command` is a bare string per `HookHandlerConfig::Command` (`codex-rs/config/src/hook_config.rs`), not an array like Claude's |
| Hook trust-bypass necessity | n/a | without `--dangerously-bypass-hook-trust`, the identical `-c hooks...` config fires zero hooks, silently | ✅ confirmed required |
| Alternative: project `.codex/hooks.json` | n/a | also works with the bypass flag; without it, the interactive TUI shows a hook-trust review dialog (`t` to trust) that durably writes a `[hooks.state...]` entry to the **global** `~/.codex/config.toml` | ⚠️ works, but pollutes global config on manual accept — the `-c`+bypass-flag route is cleaner (no global writes) |
| Alternative: `~/.codex/hooks.json` | n/a | attempted once with the bypass flag; did not fire, not further diagnosed (Routes 1/2 already answer the question) | ⚠️ inconclusive, deprioritized — not the implementation target |
| Hook env inheritance | n/a | `OVERCODE_PROBE=1` set before launch was visible inside the hook subprocess | ✅ confirmed |
| User-turn/scaffolding filter | n/a | `response_item.payload.internal_chat_message_metadata_passthrough.content_item_kinds` contains `"user.text"` for real turns vs `"environments.environment_context"`/`"host_skills.instructions"` etc. for scaffolding | ✅ new finding — more robust than string-matching the XML-ish wrapper tags originally proposed |
| Model field in rollout JSONL | n/a | `turn_context.payload.model` (duplicated at `.collaboration_mode.settings.model`); **not** reliably in `session_meta` | ✅ confirmed with real JSON quoted in §2.4 |
| Auto-update default | n/a | `codex features list` → `in_app_updates  stable  true` | ⚠️ enabled by default per feature flag; exact trigger cadence not directly observed |

Still not verified, deliberately: `codex exec --json`/`--output-schema`
output shape (headless mode wasn't a Phase 0 checklist item); `mcp`/plugin
subsystems; behavior under API-key (non-ChatGPT-subscription) auth, which
may accept `gpt-5.1-codex-mini` where subscription auth rejected it.

## Appendix B — Grok Build verified mapping (Phase 0)

**Status: verified against a live Grok Build 1.0.5 during Phase 0 (Aug 27,
2026, macOS/arm64, model `grok-4.5` where accepted — note below, otherwise
account default). Every row below is marked ✅ confirmed, ❌ refuted, or ⚠️
partially verified. The pane corpus is committed at
`tests/fixtures_grok_panes/`.**

| overcode concept | Claude Code | Grok Build | Verdict |
|---|---|---|---|
| Binary | `claude` (`CLAUDE_COMMAND`) | `grok` (`GROK_COMMAND`, new) | ✅ |
| Process basename in `ps` | `claude` | `grok` — no wrapper/child split like codex | ✅ confirmed via `pgrep -fl grok` |
| Model | `--model sonnet` | `-m/--model <id>` — `grok-4.5` no longer exists (`grok models` lists only `grok-4.6`); the **interactive TUI silently falls back to default** on a bad id with zero visible error, while **headless `-p` fails loudly** (`Invalid params: "unknown model id"`, exit 1) | ⚠️ confirmed asymmetric — a doctor/pre-launch check against `grok models` is worth adding since the TUI itself won't surface a bad `--model` |
| Bypass permissions | `--dangerously-skip-permissions` | `--permission-mode bypassPermissions` | ✅ (unchanged; not separately re-tested this pass, `auto` was the tested skip-dialog path — see next row) |
| Permissive | `--permission-mode dontAsk` | ❌ **`dontAsk` is NOT aliased to `auto`** — shows the identical full approval dialog as `default`. Only `--permission-mode auto` skips the dialog | ❌ refuted — `GrokBackend`'s permissive-mode mapping must target `auto`, not `dontAsk` |
| Normal | (default) | `--permission-mode default` | ✅ confirmed — dialog appears |
| Flag-vs-config precedence | n/a | user's `~/.grok/config.toml` sets `[ui] permission_mode = "always-approve"`; `--permission-mode default` **does** override it live (dialog appears with the flag; silent auto-approve without it) | ✅ confirmed definitively — flag beats config |
| Allowed tools | `--allowedTools a,b` | `--allow 'Bash(echo *)'` (Claude-style `Tool(glob)` grammar) | ✅ confirmed — accepted with no parse error and **actually suppressed** the dialog live, full round-trip verified |
| Prescribe session id | `--session-id <uuid>` | `-s/--session-id <uuid>`; session dir = `~/.grok/sessions/<percent-encoded-abs-cwd>/<uuid>/` (`/`→`%2F`, including the leading slash) | ✅ confirmed round-trip live with a minted uuid |
| Graceful exit | `C-c`, `/exit` | `/quit` (clean exit, prints a `grok --resume <uuid>` hint) | ✅ confirmed |
| Bare `C-c` | safe in Claude | ✅ **safe** — interrupts only; process and session stay alive; a follow-up prompt in the same session works normally | ✅ confirmed — opposite of the codex/opencode result |
| Interrupt (Escape) | n/a | single `Esc` fully interrupts and settles the turn (`Turn cancelled by user in 4.3s.`); no second Escape needed | ✅ confirmed |
| Clear conversation | `/clear` | `/new` — there is no literal `/clear` | ✅ confirmed via `/` command menu (`command_menu.txt`) |
| Approve / reject keys | `Enter` / `Escape` | digit keys, no Enter required: `1`=always-approve, `2`=approve once, `3`=reject; hint `1/3:select │ Tab:next option │ Ctrl+o:always-approve │ Ctrl+c:cancel │ Esc:scrollback` | ✅ confirmed, captured in `permission_required.txt` |
| Trust dialog | "I trust this folder" | ✅ confirmed **absent** at plain startup, even in a brand-new never-visited directory — folder trust silently gates hooks/MCP/LSP with no interactive startup prompt | ✅ confirmed absent (matches bundled docs) |
| UI mode (default vs `--fullscreen`) | n/a | byte-identical chrome on this account (no `[ui] screen_mode = "minimal"` override present); recommend passing `--fullscreen` explicitly anyway for determinism against future config drift | ✅ recommendation: standardize on `--fullscreen` |
| Hook stdin dialect | snake_case | camelCase (`hookEventName`, `sessionId`, `promptId`, `permissionMode`, `notificationType`, `reason`, …) | ✅ confirmed live, exact JSON captured in §3.3 |
| Hook env inheritance | n/a | `OVERCODE_PROBE=1` set before launch was visible inside the hook subprocess | ✅ confirmed |
| Session-end double-`Stop` | n/a | real turn-end `Stop` fires `reason:"end_turn"`; session teardown fires a **second** `Stop` with `reason:"shutdown"` | ✅ confirmed live — hook handler must filter on `reason=="end_turn"` |
| Token/cost split | assumed absent (original research) | ❌ **refuted — a full split exists**: `updates.jsonl`'s `turn_completed.usage` carries `inputTokens`/`outputTokens`/`cachedReadTokens`/`cacheCreationTokens`/`reasoningTokens`/`costUsdTicks`/per-model breakdown | ❌ refuted, in grok's favor — `TRANSCRIPT_STATS` can be closer to full than "partial, dashes for tokens/cost" (⚠️ `costUsdTicks` unit scale not cross-checked against a billed amount) |
| Auto-update default | n/a | no auto-update toggle found in `grok update --help` or `config.toml`; no background update chatter observed | ⚠️ inferred no, not exhaustively watched over a long session |

Still not verified, deliberately: `PreToolUse`/`PostToolUse` stdin shapes and
the grok→Claude tool-name alias table (taken from bundled docs, not
independently re-fired live this pass — only `UserPromptSubmit`/`Stop`/
`Notification` were registered); behavior of `--worktree` and background
subagent tasks interacting with overcode's launch model.

## Appendix C — Research provenance (Aug 27, 2026)

Compiled from: live `--help`/`doctor`/`features` output of Codex CLI 0.150.1
(npm) and Grok Build 1.0.5 (stable) on this machine; real session artifacts
(`~/.codex/sessions/**/rollout-*.jsonl`, `~/.codex/history.jsonl`,
`~/.grok/sessions/<enc-cwd>/<uuid>/{summary.json,events.jsonl,updates.jsonl,
chat_history.jsonl}`, `~/.grok/sessions/<enc-cwd>/prompt_history.jsonl`);
grok's bundled user guide (`~/.grok/docs/user-guide/`, esp. `10-hooks.md`,
`24-monitoring-usage.md`); openai/codex source (`codex-rs/config/src/
hook_config.rs`, `codex-rs/hooks/schema/generated/*.schema.json`,
`codex-rs/hooks/src/engine/discovery.rs`, fetched via `gh api` Aug 2026);
and x.ai's Grok Build announcement for install/subscription facts. The macOS
Codex app was traced to `ChatGPT.app`'s embedded `Codex Framework.framework`;
its staged alpha CLI (`~/.codex/plugins/.plugin-appserver/codex`,
0.148.0-alpha.21) is deliberately not used.
