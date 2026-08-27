# Agent Backends: Codex CLI + Grok Build Support Plan

**Document Type:** Design Assessment + Phased Implementation Plan
**Date:** August 2026
**Status:** Planned (Phases 0–5)
**Scope:** Adding OpenAI Codex CLI and xAI Grok Build as overcode's third and fourth agent backends, on the `AgentBackend` seam shipped in 0.5.0
**Predecessor:** `docs/design/agent-agnostic-backends-opencode.md` — read its §2 (architecture) and its shipped-notes first; this plan assumes that seam and does not re-explain it.

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
  almost 1:1. Injection route is the open question ([VERIFY-P0] §2.3).
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
- Stats are the weak spot: session files carry a running `totalTokens` per
  event (context proxy) but **no local input/output split was found**
  [VERIFIED absent from `summary.json`/`chat_history.jsonl` of a real 413-message
  session]; the split is exported via OpenTelemetry only. `TRANSCRIPT_STATS`
  will be partial unless Phase 0 finds a local source.

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
- Process basename: `codex` (napi wrapper resolves to a vendored
  `codex-*-darwin-arm64` binary; confirm the `ps` basename in Phase 0
  [VERIFY-P0]).
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
| Permissive | `--permission-mode dontAsk` | `-a never --sandbox workspace-write` — auto-approve but still sandboxed [VERIFY-P0: `-a` accepted values beyond `on-request`/`never`; also evaluate `--approve-for-me`] |
| Normal | (default) | (default: `on-request` approval, sandbox per config) |
| Allowed tools | `--allowedTools a,b` | none — nearest is sandbox modes + `-c` config; ignore `--allowed-tools` like opencode does |
| Persona | `--agent name` | none observed [VERIFY-P0: profiles via `-p/--profile` are config layers, not personas] |
| Prescribe session id | `--session-id <uuid>` | ✗ — no flag exists |
| Resume | `--resume <id>` | `codex resume <SESSION_ID>` (subcommand; `--last` also exists) |
| Fork | `--resume <id> --fork-session` | `codex fork <SESSION_ID>` (subcommand) |
| Extra dirs / cwd | n/a | `-C/--cd <dir>`, `--add-dir` |
| Headless | `claude -p` | `codex exec [msg]` (`--json`, `--output-schema`) |
| Graceful exit | `C-c`, `/exit` | [VERIFY-P0] — expect `/quit` or `/exit` slash command; do NOT assume `C-c` is safe (opencode lesson) |
| Clear conversation | `/clear` | [VERIFY-P0] — expect `/new` |
| Approve / reject | `Enter` / `Escape` | [VERIFY-P0] from corpus — codex approval dialog keys |
| Trust dialog | "I trust this folder" | [VERIFY-P0] — codex prompts for folder trust on first run in a directory; capture exact chrome |

`build_command()` note: resume/fork produce `["codex", "resume", <id>, *common]`
— subcommand-first argv. `resume_args()` should return `["resume", <id>]` /
`["fork", <id>]` and `build_command()` must splice them **before** the shared
options, unlike claude/opencode where order is flag-appending. Verify that
top-level options (`-m`, `-s`, `-a`, `-c`) are accepted after the subcommand
[VERIFY-P0]; if not, build the full argv inside `build_command()` per shape.

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
  requirements). Command hooks receive JSON input (per generated
  `*.command.input.schema.json` schemas — fetch these in Phase 0 for exact
  stdin shapes [VERIFY-P0]).
- **Trust model:** per-hook `state` entries with `trusted_hash`;
  `--dangerously-bypass-hook-trust` "runs enabled hooks without requiring
  persisted hook trust … for automation that already vets hook sources".

**The injection question (the crux) [VERIFY-P0], in preference order:**

1. `codex -c 'hooks.UserPromptSubmit=[...]'` per-launch override +
   `--dangerously-bypass-hook-trust`. If `-c` reaches hook config, this is the
   exact analogue of Claude's `--settings` — per-launch, no files, no global
   pollution. Test first.
2. Project-layer `.codex/hooks.json` written by `prepare_launch()` (the
   opencode-plugin pattern: marker line, idempotent, never clobber user
   files) + whatever trust step it needs.
3. User-layer `~/.codex/hooks.json` with an env guard (hook script no-ops
   unless `OVERCODE_SESSION_NAME` is set — hook processes inherit the session
   env [VERIFY-P0]). Global but inert outside overcode, like the opencode
   plugin's guard.

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
  interactions → count of user `response_item` messages (excluding
  `<environment_context>`/`<user_instructions>` scaffolding [VERIFY-P0: exact
  filter]). Model: [VERIFY-P0] locate in `turn_context` or `session_meta`.
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

`RESUME` ✅ · `FORK` ✅ · `HOOK_EVENTS` ✅ (pending injection route) ·
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
- Binary override env: introduce `GROK_COMMAND`. Process basename: `grok`
  [VERIFY-P0 via `ps` during corpus capture].
- Config: `~/.grok/config.toml`. Note the user's config sets
  `[ui] permission_mode = "always-approve"` — launch flags must override
  config for overcode's modes to mean anything [VERIFY-P0: flag-beats-config].
- Bundled offline docs at `~/.grok/docs/user-guide/` (hooks, sessions,
  headless, sandbox, permissions) — cite these, they version with the binary.

### 3.2 Flag mapping [VERIFIED from `--help`]

| overcode concept | Claude Code | Grok Build |
|---|---|---|
| Fresh launch | `claude [prompt]` | `grok [prompt]` |
| Model | `--model sonnet` | `-m/--model <id>` (bare ids: `grok-4.6`, `grok-4.5`; `grok models` lists) |
| Bypass permissions | `--dangerously-skip-permissions` | `--permission-mode bypassPermissions` |
| Permissive | `--permission-mode dontAsk` | `--permission-mode dontAsk` (accepted for compat; grok treats `auto` as nearest [VERIFY-P0: accepted vs aliased]) |
| Normal | (default) | `--permission-mode default` |
| Allowed tools | `--allowedTools a,b` | `--allow <RULE>` repeatable — help says "compat alias: --allowedTools" → `PERMISSION_INJECTION` ✅ [VERIFY-P0: rule syntax matches Claude's `Bash(x *)` grammar] |
| Persona | `--agent name` | `--agent <name-or-file>` |
| Prescribe session id | `--session-id <uuid>` | `-s/--session-id <uuid>` — "for a **new** conversation; must not already exist" → `SESSION_ID_PRESCRIPTION` ✅ |
| Resume | `--resume <id>` | `-r/--resume <id-or-title>` |
| Fork | `--resume <id> --fork-session` | `--resume <id> --fork-session` (identical; `--session-id` names the forked session — better than Claude) |
| Headless | `claude -p` | `-p/--single` or `grok agent`; `--output-format streaming-messages-json` is literally "the Anthropic Messages API wire format" |
| Graceful exit | `C-c`, `/exit` | [VERIFY-P0] — expect `/exit` or `/quit`; test interrupt semantics before trusting Escape/C-c |
| Clear conversation | `/clear` | [VERIFY-P0] |
| Approve / reject | `Enter` / `Escape` | [VERIFY-P0] from corpus |
| Trust dialog | "I trust this folder" | folder-trust exists for hooks/MCP (`--trust` flag, `/hooks-trust`) [VERIFIED]; whether a startup dialog appears [VERIFY-P0] |

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

### 3.4 Stats: partial, honestly

[VERIFIED from a real 413-message session under
`~/.grok/sessions/<url-encoded-cwd>/<session-uuid>/`.]

- Per-session dir contents: `chat_history.jsonl`, `events.jsonl` (phases,
  `tool_started/completed`, `permission_requested/resolved`,
  `turn_started/ended` — undocumented, treat as diagnostic only),
  `updates.jsonl` (persisted ACP `session/update` stream; `_meta.totalTokens`
  running total per update), `summary.json` (`current_model_id`,
  `num_messages`, timestamps, git info — **no token fields**),
  `prompt_history.jsonl` (per-project prompt log with session ids).
- `GrokStatsReader` therefore: interactions ← `prompt_history.jsonl` count for
  the session id; context ← latest `_meta.totalTokens` from `updates.jsonl`;
  model ← `summary.json.current_model_id`; input/output/cost ← None (dashes)
  unless Phase 0 finds a split (check `grok trace export` and newer-file
  formats before conceding [VERIFY-P0]). Declare `TRANSCRIPT_STATS` only if
  the columns we can fill render honestly; the seam already handles partial
  `AgentSessionStats` fields as dashes.
- Session location is trivially prescribable: overcode mints the uuid via
  `--session-id`, so the reader keys straight into
  `sessions/<encoded-cwd>/<uuid>/` — no discovery problem at all.

### 3.5 Capability forecast

`RESUME` ✅ · `FORK` ✅ · `SESSION_ID_PRESCRIPTION` ✅ · `PERMISSION_INJECTION` ✅
(pending rule-syntax check) · `HOOK_EVENTS` ✅ · `TRANSCRIPT_STATS` ⚠️ partial ·
`SKILLS` ❌ (grok has skills + a marketplace; unintegrated) · `SANDBOX_PROBE` ❌ ·
`SUBSCRIPTION_USAGE` ❌ · `AGENT_TEAMS` ❌.

---

## 4. Risks

1. **Codex hook injection may need `--dangerously-bypass-hook-trust`.** The
   flag is designed for exactly this ("automation that already vets hook
   sources") but the name is radioactive; if it proves required, surface it in
   `overcode show`/docs plainly. If `-c` cannot reach hooks at all, fall back
   to file-based injection or notify+polling; the phase gates on Phase 0's
   verdict. (Mitigated: capability honesty means worst case is polling-only
   status, the opencode Phase-4 tier.)
2. **Codex release cadence.** npm shows multiple releases/week (0.148→0.150 in
   days). Same mitigations as opencode: `TESTED_CODEX_RANGE`, doctor version
   check, committed pane corpus, polling fallback.
3. **Grok subscription gating.** Grok Build needs SuperGrok/X Premium+; CI and
   other machines cannot assume it. All tests must run against the mock; live
   verification is a manual Phase 0 step on this machine.
4. **Grok config-vs-flag precedence.** The user's config sets
   `permission_mode = "always-approve"`; if flags don't beat config, overcode's
   "normal" mode silently becomes bypass. Phase 0 must verify precedence and,
   if needed, pass the mode explicitly on every launch.
5. **Four TUIs' chrome to track.** Each corpus is a snapshot; doctor version
   ranges + the `autoupdate`-style warnings are the containment. Grok's
   `update` subcommand and codex's `codex update` both auto-move; check
   whether either auto-updates by default [VERIFY-P0] and add doctor warnings
   if so.
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
   "polling-tier (Phase 2 pending)" so docs never overstate.

**Acceptance:** live smoke on this machine: launch, watch status turn
green→idle correctly, send instruction, approve a permission prompt via
`overcode send <n> approve`, restart, fork, kill; full unit suite green; e2e
mock suite green.

### Phase 2 — Codex telemetry: hooks + stats

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

Shipped opencode behavior collapses both `permissive` and `bypass` onto
`--auto`, under which opencode's own `"deny"` rules still win — there is no
real `--dangerously-skip-permissions` analogue. Requested improvement: in
**bypass** mode only, have `prepare_launch()` materialize an allow-everything
permission config for the launched process so deny rules cannot block it.

Verify-first items (same discipline as Phase 0):
- Whether opencode honors an `OPENCODE_CONFIG` env var or another per-process
  config injection point, and how it merges with the project's
  `opencode.json` (override vs replace — replacing the user's project config
  wholesale is unacceptable).
- The exact permission grammar for "allow everything" (e.g.
  `{"permission": {"*": "allow"}}` or per-tool keys) in the tested opencode
  range.
- That a per-process route leaves the user's own `opencode` sessions and
  files untouched (the plugin-footprint standard: project files only with
  marker + never-clobber, or better, no files at all via env).

If only a project-file route exists, follow the telemetry plugin's rules
(marker line, idempotent, never clobber user content) and document the
footprint in `docs/backends.md`; if no clean route exists, keep `--auto` and
document the limitation — do not silently edit user configs.

---

## Appendix A — Codex CLI verified mapping (Phase 0 fills this)

Placeholder: Phase 0 records the per-row verdict table here, opencode
Appendix-A style, citing fixtures and command output.

## Appendix B — Grok Build verified mapping (Phase 0 fills this)

Placeholder: as Appendix A.

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
