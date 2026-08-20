# Agent-Agnostic Backends: Design Assessment for opencode Support

**Document Type:** Design Assessment + Phased Implementation Plan
**Date:** August 2026
**Status:** Implemented (Phases 1–6, Aug 2026)
**Scope:** Making overcode *partially* agent-agnostic, with the opencode CLI (opencode.ai) as the second supported backend

> **Shipped.** All six phases landed. User-facing documentation is
> `docs/backends.md`; the architecture write-up is `docs/architecture.md`
> (§Agent Backends). Where reality diverged from this plan, the phase
> sections below carry a dated note and **Appendix A is the authority** on
> opencode's actual behaviour. The headline divergences:
>
> 1. **Three Phase-4 flag/gesture assumptions were wrong** — `--permissions`
>    does not exist, `ctrl+x q` is unnecessary and a bare `C-c` kills
>    opencode outright, and opencode draws no prompt glyph at all.
> 2. **opencode calls every export of a plugin module as a plugin factory**,
>    so the bundled telemetry plugin has exactly one export (Phase 5).
> 3. **`--fork` and `--agent` both turned out to be real**, so `FORK` is on
>    and personas work — better than the plan assumed.
> 4. **Phase 6 added capability publication to the sister protocol**
>    (`SessionDaemonState.backend_capabilities`), which §5's item 2 only
>    sketched. Old sisters report nothing and are read as claude-code-full.
> 5. **`--claude-arg` became `--backend-arg`** with the old spelling kept as
>    a hidden alias, and `Session`'s renamed fields are read under both key
>    sets and written under both for one release — the "on-disk migration is
>    a non-event" claim held, but `to_dict` dual-writes so a *downgrade* is
>    also safe.

---

## Executive Summary

**Verdict: feasible and sane.** Overcode's architecture is more favorable than its ~880 "claude" mentions suggest. The transport layer (tmux), state publication (tolerant JSON dataclasses), pure-function daemon cores, declarative TUI columns, web API, and sister protocol are all genuinely agent-agnostic. The Claude coupling concentrates in five well-bounded places:

1. **Launch argv construction** — one choke point (`launcher._build_claude_command`, plus one reach-through in `web_control_api.py`)
2. **Transcript/stats parsing** — one funnel (`history_reader.get_session_stats`)
3. **Status detection** — two detectors (pane-polling patterns + Claude Code hook protocol), dispatched *globally* rather than per-agent
4. **Process/config probing** — binary-name matching, `~/.claude` paths, settings.json editing
5. **Naming debt** — `ClaudeLauncher`, `claude_session_ids`, `--claude-arg`, help text (mechanical)

On the other side, **opencode's integration surface is unusually good** — in some ways better than Claude Code's:

- HTTP server (`opencode serve`) with an OpenAPI-spec'd API and an **SSE event bus** (`/event`) emitting exactly the signals overcode needs: `session.idle`, `permission.asked`, `tool.execute.before/after`, `session.error`
- A **JS/TS plugin system** with a `permission.ask` hook (programmatic allow/deny) and an `event` hook receiving every bus event — a plugin can write overcode's existing hook-state files directly
- **SQLite session store** with per-session `cost`, `tokens_input/output/reasoning/cache_*` columns — cost/token sync is a single SQL query instead of JSONL scraping
- CLI flags mapping cleanly onto overcode's launch model: `--session <id>` (resume), `--fork`, `--auto` (≈ bypass permissions), `-m provider/model`, `--permissions` (≈ allowedTools)

**The main risk is churn, not architecture.** opencode releases every 2–3 days, migrated storage JSON→SQLite at v1.2.0, and has a v2 core rearchitecture in flight. The mitigation is to build the opencode adapter on the **most stable contracts** (plugin hook API + HTTP/SSE, both SDK-generated) and treat SQLite schema and TUI screen text as fallback/secondary, plus pin versions (`"autoupdate": false`) and gate on a version check in `overcode doctor`.

**Recommended shape:** a small `AgentBackend` protocol with a capability matrix, `Session.backend` discriminator field, per-session (not global) status-detector dispatch, a `StatsReader` seam, and an opencode adapter whose primary telemetry channel is a bundled opencode plugin that translates bus events into overcode's existing (already backend-neutral) hook-state file format. Claude Code remains first-class; opencode features degrade gracefully where no analogue exists (skills, sandbox badge, subscription usage widget, agent teams).

Estimated total effort: **6 phases**, each independently shippable and sized for handoff to a single Opus-5-class agent session. Phases 1–3 are pure internal refactors that leave Claude behavior identical (full regression suite green); opencode only becomes user-visible in Phase 4.

---

## 1. Current-State Findings

### 1.1 What is already agent-agnostic (ports for free)

| Layer | Evidence |
|---|---|
| tmux transport | `tmux_manager.py`, `tmux_utils.py` — pure panes/send-keys/capture; 0 functional Claude references |
| Daemon business logic | `monitor_daemon_core.py`, `supervisor_daemon_core.py`, `tui_logic.py` — ~950 lines of pure functions over dataclasses |
| State publication | `SessionDaemonState`/`MonitorDaemonState` with tolerant `from_dict`; web API passes raw `daemon_state` through (`web_api.py:359`); sister protocol has the same passthrough (`sister_poller.py:311`). **New fields are additive and schema-safe by construction.** |
| Status vocabulary | `status_constants.py` — running / waiting_user / waiting_approval / error / terminated etc. are generic; display and analytics don't care where a status came from |
| Summarizer | `summarizer_component.py` + `summarizer_client.py` — summarizes raw pane text via OpenAI/Anthropic APIs; works on any TUI agent unchanged |
| TUI columns | `summary_columns.py` — declarative registry; columns already None-guard on missing stats |
| Wrapper contract | `wrapper.py` — `wrapper.sh <argv…>` with `$@` passthrough is CLI-neutral (only `devcontainer.sh` bakes in the Claude npm install) |
| `OVERCODE_*` env contract | `OVERCODE_SESSION_NAME/ID/TMUX_SESSION/PARENT_*` — backend-neutral session identity injected into the child process |
| Pricing | `pricing.py` already holds OpenAI models alongside Anthropic; longest-substring-match lookup |
| Hook-state file format | `hook_state_<agent>.json` / `hook_events_<agent>.jsonl` — overcode's **own** schema (event, timestamp, tool_name, tool_input, obligations, foreground). Claude-driven content, neutral container. This is the keystone for Phase 5. |

### 1.2 Where the coupling lives (ranked by cost)

| Rank | Area | Files | Difficulty |
|---|---|---|---|
| 1 | Hook protocol + status semantics | `hook_handler.py`, `hook_status_detector.py`, `follow_mode.py`, `cli/hooks.py` | HARD — implements Claude Code's hook event vocabulary, stdin JSON schema, tool-name taxonomy, exit-code-2 blocking |
| 2 | Transcript/usage parsing | `history_reader.py` (1074 lines), `monitor_daemon.sync_claude_code_stats` | HARD but funnelled — everything flows through `get_session_stats() -> ClaudeSessionStats` |
| 3 | Launch flag grammar + session-id lifecycle | `launcher.py`, `web_control_api.py:142` (private reach-through), `cli/agent.py`, `tui.py` | HARD — 9 Claude flags, `--settings` hook injection, `--session-id`/`--resume`/`--fork-session` semantics, startup-dialog handshake, `/exit`+`/clear` slash commands |
| 4 | Supervisor-as-claude | `supervisor_daemon.py`, `daemon_claude_skill.md` | MODERATE-HARD — spawns a real `claude`; `⏺`/`⎿`/`❯` completion heuristics; decision logic already pure |
| 5 | Polling patterns | `status_patterns.py`, `status_detector.py` | MODERATE — `StatusPatterns` dataclass already parameterizes ~80%; leaks: hardcoded `⏺` prefixes, `'? for shortcuts'`, `"esc to interrupt"`, `\xa0` prompt detection, status-bar count regexes outside the dataclass |
| 6 | Config/skills/agents file layout | `claude_config.py`, `cli/perms.py`, `cli/skills.py`, `bundled_skills.py`, `agent_scanner.py` | MODERATE — `~/.claude/{settings.json,skills,agents}`, `Bash(x *)` permission grammar |
| 7 | Session model field names | `session_manager.py`, `monitor_daemon_state.py` | MODERATE — mechanical, but persisted JSON + sister wire format need aliasing |
| 8 | Process/binary identification | `doctor.py:182` (`basename == "claude"`), `dependency_check.py`, `sandbox_detect.py` | EASY-MODERATE |
| 9 | Input quirks | `tmux_utils.send_keys_to_pane` — `!` bash-mode delay, `/` literal+0.5s autocomplete wait | MODERATE — needs a per-backend input profile |
| 10 | Cosmetics | help text, TUI labels, `--claude-arg`, exceptions | EASY |

Key structural facts an implementer should internalize:

- There is **no backend/agent-type concept anywhere today**. The nearest precedent is `Session.provider` (`"web"`/`"bedrock"`) — an API-transport selector whose plumbing (Session → daemon state → web → sister → TUI) is the exact template for a `backend` field.
- `launcher._send_launch_for_session` (`launcher.py:666`) is the **single render point** for launch, restart, revive, and fork. All backend dispatch for command construction can happen there — plus the one reach-through at `web_control_api.py:142` that calls `_build_claude_command` across the module boundary (must be fixed or restarts silently revert to Claude).
- `StatusDetectorDispatcher` mode (hooks vs polling) is **global, set once, toggled by the `K` hotkey** — explicitly "no per-agent dispatch" (`status_detector_factory.py`). A mixed fleet requires per-session dispatch. Mitigating: `StatusDetectorProtocol` is already the right shape.
- `CLAUDE_COMMAND` env override exists (`launcher.py:139`) but only swaps the binary, not the flag grammar/transcripts/hooks. The E2E mock (`tests/mock_claude.py`, 680 lines, YAML-scenario-driven byte-accurate fake TUI) is wired through it; `MOCK_SCENARIO` handling is baked into production `launcher.py:257-259`.
- The repo's own bakeoff docs already flag this: `docs/design/bakeoffs/overcode-vs-kagan.md` recommends a `BackendCapability` enum; `overcode-vs-ccmanager.md:97` records opencode's `△ Permission required` pane marker; `docs/design/acp-agent-integration-analysis.md` covers the ACP alternative (see §4.3).

### 1.3 opencode integration surface (researched Aug 2026, v1.18.19)

| Need | opencode mechanism | Stability |
|---|---|---|
| Launch TUI | `opencode [project]`; model via `-m provider/model`; auto-approve via `--auto` (deny rules still enforced); tool restriction via `--permissions bash,edit,…` | Stable |
| Resume / fork | `--continue` / `--session <id>` / `--fork` | Stable |
| Headless | `opencode run [msg]` (`--format json`), `opencode serve` (port 4096), `opencode attach <url>`, `run --attach <url>` | Stable-ish (attach has a hang-on-server-death bug #18984) |
| Idle/approval events | SSE `GET /event`: `session.idle`, `session.status`, `permission.asked`, `permission.replied`, `message.part.updated`, `tool.execute.*`, `session.error` | **Most stable contract** (OpenAPI + SDK-generated) |
| Programmatic approval | `POST /session/:id/permissions/:permissionID`; or plugin `permission.ask` hook setting `output.status = allow/deny/ask` | Stable |
| Plugins | JS/TS in `.opencode/plugins/` or `~/.config/opencode/plugins/`; context includes SDK client + Bun shell; `event` hook sees every bus event | Stable |
| Cost/tokens | SQLite `~/.local/share/opencode/opencode.db` (env `OPENCODE_DB`/`OPENCODE_DATA_DIR`), `session` table: `cost`, `tokens_input/output/reasoning/cache_read/cache_write`, `model`, `directory`, timestamps | **Caveat:** migrated from JSON files at v1.2.0; `cost` often 0 for subscription auth — recompute from tokens like ccusage does |
| Permission prompt (pane text) | Dialog titled **"Permission required"**, options "Allow once / Allow always / Reject", Esc = reject | Screen text = least stable; use only as polling fallback |
| Config | `opencode.json` (project) / `~/.config/opencode/opencode.json` (global); `permission` map (allow/ask/deny, per-tool patterns); `agent` definitions; `keybinds`; `autoupdate` | Stable |
| tmux behavior | Works under tmux/send-keys; mouse capture on by default (disable via TUI config for supervised panes); known tmux quirks (#16351, #10610) | Watch |

**No opencode analogue exists for:** Claude skills (`~/.claude/skills`), the `/sandbox` loopback-listener heuristic, the Anthropic subscription usage API (`usage_monitor.py`), agent teams, and `--session-id` *prescription* (opencode assigns its own session IDs; discovery must be reversed — find the session by directory/time, don't prescribe it).

---

## 2. Target Architecture

### 2.1 The `AgentBackend` seam

One new module, `src/overcode/backends/` (package):

```
backends/
  __init__.py        # registry: get_backend(name) -> AgentBackend; BACKENDS = {"claude-code": ..., "opencode": ...}
  base.py            # AgentBackend Protocol + BackendCapability enum + LaunchSpec dataclass
  claude_code.py     # extraction of today's behavior (must be behavior-identical)
  opencode.py        # Phase 4+
```

```python
class BackendCapability(Flag):
    RESUME = auto()               # relaunch continuing a prior conversation
    FORK = auto()                 # branch a conversation into a new agent
    SESSION_ID_PRESCRIPTION = auto()  # overcode chooses the session id up front
    HOOK_EVENTS = auto()          # push telemetry (hook-state files) available
    TRANSCRIPT_STATS = auto()     # tokens/cost/context readable from disk
    PERMISSION_INJECTION = auto() # per-launch permission allowlist
    SKILLS = auto()               # skills/persona file discovery
    SANDBOX_PROBE = auto()
    SUBSCRIPTION_USAGE = auto()
    AGENT_TEAMS = auto()

class AgentBackend(Protocol):
    name: str                     # "claude-code" | "opencode"
    binary: str                   # for dependency_check + doctor.find_process
    capabilities: BackendCapability

    def build_command(self, spec: LaunchSpec) -> list[str]: ...
    def env_prefix(self, spec: LaunchSpec) -> dict[str, str]: ...
    def resume_args(self, session_id: str, fork: bool) -> list[str]: ...
    def graceful_exit_keys(self) -> list[str]: ...        # claude: C-c + "/exit"; opencode: ctrl+x q
    def clear_conversation_keys(self) -> list[str]: ...   # claude: "/clear"; opencode: "/new"
    def approve_keys(self) -> list[str]: ...              # claude: Enter; opencode: Enter (first option)
    def reject_keys(self) -> list[str]: ...               # claude: Escape; opencode: Escape
    def startup_dialog_rules(self) -> list[DialogRule]:...# ("I trust this folder" -> Enter, etc.)
    def prompt_ready_chars(self) -> set[str]: ...
    def input_profile(self) -> InputProfile: ...          # send-keys quirks (bash-!, slash delay)
    def status_patterns(self) -> StatusPatterns: ...
    def make_stats_reader(self) -> StatsReader: ...       # Phase 2 seam
    def check_binary(self) -> DependencyResult: ...
    def health_verdict(self, argv: list[str]) -> str: ... # doctor: "was observability injected?"
```

`LaunchSpec` is a small dataclass carrying what `launch()` already computes: name, session ids, permissiveness_mode, model, allowed_tools, extra_args, persona/agent, provider, teams, settings-injection payload. `ClaudeCodeBackend.build_command` is a cut-paste of `_build_claude_command` + `_build_launch_settings`.

**Dispatch point:** `Session.backend: str = "claude-code"` (tolerant `from_dict` makes this schema-safe); resolved to an `AgentBackend` inside `_send_launch_for_session`, `restart`, `revive`, `launch_fork`, and `web_control_api.restart_agent` (which must stop calling the private builder and go through the launcher).

**Capability gating:** TUI/CLI/web/sister actions check `backend.capabilities` — e.g. fork buttons hidden/erroring cleanly when `FORK` is absent, sandbox badge suppressed without `SANDBOX_PROBE`, usage widget hidden without `SUBSCRIPTION_USAGE`.

### 2.2 Status detection: per-session dispatch, neutral interchange

- `StatusDetectorDispatcher` keys the hooks/polling choice off the **session** (`session.backend` + per-session detection preference) instead of a single global mode. The `K` hotkey becomes a per-agent (or per-backend-default) toggle.
- The hardcoded Claude glyphs (`⏺` tool prefixes, `'? for shortcuts'`, `"esc to interrupt"`, `\xa0`, status-bar count regexes) move into `StatusPatterns` fields; `get_patterns(backend)` returns the backend's set. Where string parametrization isn't enough (the phase-ordering in `detect_status` is somewhat Claude-shaped), the polling detector grows small per-backend strategy hooks rather than a fork of the 14-phase logic.
- **The hook-state file format stays exactly as-is** and becomes the documented neutral interchange: any backend that can *produce* `hook_state_<agent>.json` / `hook_events_<agent>.jsonl` gets the full `HookStatusDetector` experience (obligation badges, foreground classification, status detail column) for free.

### 2.3 opencode telemetry: plugin-first, polling-fallback

Primary channel — a small bundled opencode plugin (`src/overcode/opencode_plugin/overcode.js`, installed into the project's `.opencode/plugins/` at launch, or referenced via config):

```
opencode bus event            → overcode hook-state event
──────────────────────────────────────────────────────────
session.idle                  → Stop
permission.asked              → PermissionRequest
permission.replied            → (clears waiting_approval)
tool.execute.before           → PreToolUse   (tool_name, tool_input mapped)
tool.execute.after            → PostToolUse
session.error                 → StopFailure/error
message.updated (user)        → UserPromptSubmit
session.deleted / exit        → SessionEnd
```

The plugin knows `OVERCODE_SESSION_NAME` / `OVERCODE_TMUX_SESSION` / `OVERCODE_STATE_DIR` from the environment (already injected by the launcher) and writes the JSON files directly (atomic write, same schema) — no HTTP hop, no new daemon. This reuses ~800 lines of `HookStatusDetector` unchanged and works identically in containers (state dir is already mounted by the devcontainer wrapper).

Fallback channel — an opencode `StatusPatterns` set for pane polling ("Permission required", "Allow once", spinner markers), for users who don't want the plugin or when the plugin breaks on an opencode update.

Stats channel — `OpencodeStatsReader` querying the SQLite `session` table (read-only, `mode=ro` URI) matched by working directory + launch time window; recompute cost from tokens via `pricing.py` when the stored `cost` is 0. Later refinement: have the plugin write the opencode session ID into the hook-state file, making the mapping exact (analogous to `claude_session_ids` ownership today).

### 2.4 What deliberately stays Claude-only

- **Supervisor daemon's own meta-agent** stays a Claude process. It *supervises* any backend (it acts via tmux send-keys and reads neutral daemon state), but its approve/reject gestures must come from the target session's backend (`approve_keys`/`reject_keys`) instead of hardcoded Enter/Escape in `daemon_claude_skill.md`.
- **Skills, agent teams, sandbox badge, subscription usage widget** — capability-gated, hidden for opencode.
- **`opencode serve`/`attach` split-process mode** — explicitly out of scope for the MVP (attach bugs, port discovery unresolved). The design keeps the door open: the plugin channel and an SSE channel would feed the same hook-state files.

---

## 3. Consequences & Risks

**Costs you accept by doing this:**

1. **A second moving target.** opencode ships every 2–3 days and is mid-rearchitecture ("v2"). Mitigations: build on plugin/API contracts (SDK-stable), recommend `"autoupdate": false`, add an `overcode doctor` check for the tested opencode version range, and keep the polling fallback so a plugin break degrades to coarse status rather than nothing.
2. **A capability-matrix UX.** Some columns/actions go dark per backend (skills, sandbox, usage, teams; possibly context-window % early on). The codebase already None-guards most columns, but the UX needs deliberate "not applicable" rendering rather than misleading zeros.
3. **Test-matrix growth.** The container/e2e suites need a `mock_agent --flavor opencode` and duplicated scenario YAMLs for the flows that differ (permission dialog, startup). The mock architecture (ScenarioRunner + fixture packs) supports this cheaply, but CI time grows.
4. **Naming migration debt.** `claude_session_ids` → `agent_session_ids` etc. touches ~220 identifier sites plus persisted JSON. Tolerant `from_dict` + property aliases make it safe, but it's noise; the plan defers pure renames to the final phase and uses aliases in the interim.
5. **Sister/remote asymmetry.** Old sisters won't report `backend`; default to `"claude-code"`. Control verbs a remote backend can't honor (fork) fail at call time today — capability info should ride the existing raw `daemon_state` passthrough so newer TUIs can gray them out.
6. **Supervisor quality.** The supervisor prompt's unblock recipes are tuned to Claude's dialogs. opencode's "Allow always" second-stage confirmation and different menu semantics need their own recipe text, or the supervisor will mis-drive opencode prompts.

**What you get:**

- opencode agents visible in the dashboard with live status (green/orange/red), pane preview, summaries (summarizer works day one), send-instruction, kill/restart, standing orders, hierarchy, budgets (once stats land), sister aggregation — i.e. ~85% of the overcode experience.
- A real seam that makes backend #3 (e.g. Codex CLI, Gemini CLI, or an ACP bridge) an adapter-sized task instead of a re-audit.
- A cleaner core: the launcher god-class gets split into backend-neutral orchestration + backend argv builders, which pays down existing debt regardless of opencode.

**Recommendation:** proceed, with the phase order below — refactor-first (Phases 1–3 are zero-behavior-change and independently valuable), opencode-visible from Phase 4, telemetry richness in Phase 5, parity/polish in Phase 6. Do **not** attempt full parity or a plugin-marketplace-style backend API; two backends, capability-gated, is the right scope.

---

## 4. Alternatives Considered

1. **Wrapper-only hack** (point a wrapper at opencode, no code changes): launches it, but status detection misfires on Claude patterns, stats stay dark or wrong, restart/fork emit Claude flags at an opencode process. Rejected — it's the current escape hatch and demonstrably insufficient.
2. **ACP bridge as the universal abstraction** (see `acp-agent-integration-analysis.md`): elegant long-term, but the earlier analysis found ACP covers ~25-30% of overcode's needs (no cost/metrics/control), and opencode's native surface is richer than its ACP one. Rejected for now; the `AgentBackend` seam doesn't preclude an `acp` backend later.
3. **`opencode serve` + `attach` as the primary integration** (drive everything over HTTP/SSE, TUI just attaches): architecturally the cleanest, but attach is buggy (#18984), per-TUI embedded server ports aren't discoverable, and it diverges from overcode's "the pane is the source of truth" model. Deferred; the plugin writes the same files an SSE consumer would.

---

## 5. Phased Implementation Plan

Ground rules for every phase:

- **Each phase must leave `main` shippable**: full unit + container test suites green, Claude Code behavior byte-identical unless the phase says otherwise.
- Phases are written to be handed to an Opus-5-class agent as a self-contained brief. Each lists: objective, scope fence, key files, design constraints, acceptance criteria.
- No pure-rename churn before Phase 6; use aliases.
- Reference material the implementing agent should read first: this doc; `docs/design/bakeoffs/overcode-vs-kagan.md` (§BackendCapability); `docs/claude-session-files.md`; `launcher.py`; `status_detector_factory.py`; `hook_handler.py`.

### Phase 1 — Backend seam + Claude extraction (zero behavior change)

**Objective:** introduce `backends/` (registry, `AgentBackend` protocol, `BackendCapability`, `LaunchSpec`), extract `ClaudeCodeBackend` from the launcher, add `Session.backend` / `SessionDaemonState.backend` (default `"claude-code"`), and route all command construction through the backend.

**Scope fence:** no opencode code. No renames of persisted fields. No TUI changes beyond plumbing. Claude argv output must be **byte-identical** for every existing code path.

**Key work items:**
1. `backends/base.py`, `backends/claude_code.py`, `backends/__init__.py` as sketched in §2.1. Move `_build_claude_command`, `_build_launch_settings`, `_build_launch_cmd_str`'s Claude env bits (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`, `CLAUDE_CODE_USE_BEDROCK`), startup-dialog rules (`_wait_for_prompt` string rules), `/exit`/`/clear` strings, and `PROMPT_READY_CHARS` into `ClaudeCodeBackend`. Keep `CLAUDE_COMMAND` env override working (it's the mock harness contract).
2. `Session.backend` + `SessionDaemonState.backend` fields; plumb through `_build_session_metadata`, `create_session`, daemon `_publish_state`. Free via tolerant `from_dict`.
3. Dispatch in `_send_launch_for_session` (launcher.py:666), `restart`, `revive`, `launch_fork` via `get_backend(session.backend)`.
4. **Fix the reach-through:** `web_control_api.restart_agent` (web_control_api.py:131-163) must call a public launcher method instead of `launcher._build_claude_command`.
5. Generalize `dependency_check`: `require_agent_cli(backend)` delegating to `backend.check_binary()`; keep `require_claude()` as a wrapper. Parameterize `doctor.find_claude_process` → `find_agent_process(expected_basenames)` (doctor.py:161-184) and its three monitor_daemon call sites.
6. Capability gating helpers: `session_backend(session).capabilities` checks in `launch_fork`, `web_control_api.fork_agent`, and CLI `fork` (error message "backend X does not support fork") — no-op for Claude since it has all capabilities.
7. Tests: golden-argv tests asserting `ClaudeCodeBackend.build_command` output equals pre-refactor strings for a matrix of (fresh, resume, fork, session-id, bypass/permissive/normal, model, agent, allowed-tools, extra-args, teams, bedrock); registry tests; a `backend` round-trip persistence test; full existing suite green.

**Acceptance:** all existing tests pass unmodified (except imports); `git grep '_build_claude_command' src` shows only backend-internal uses; launching/restarting/forking via CLI, TUI, and web API works against the mock harness.

### Phase 2 — Stats seam (`StatsReader`) + graceful degradation

**Objective:** put `history_reader` behind a `StatsReader` protocol so a backend without Claude transcripts degrades to "unknown" instead of wrong/zero.

**Key work items:**
1. `StatsReader` protocol: `get_stats(session) -> AgentSessionStats | None`, `discover_session_ids(session) -> list[str]`, `get_window_token_usage(...)`. `AgentSessionStats` = rename-alias of `ClaudeSessionStats` (keep `ClaudeSessionStats = AgentSessionStats` alias; do not rename fields).
2. `ClaudeStatsReader` wraps today's `history_reader` functions; `NullStatsReader` returns None/empty. Backend factory method `make_stats_reader()`.
3. `monitor_daemon`: `sync_claude_code_stats` → `sync_agent_stats(session, reader)`; `_sync_session_ids` and `_discover_all_session_ids` go through `reader.discover_session_ids` (Claude impl keeps history.jsonl logic + ownership guard). Container stats path (`_sync_container_stats`) stays Claude-only inside `ClaudeStatsReader`.
4. TUI/columns audit: every `claude_stats`-consuming column (summary_columns.py:539–1158) must render a deliberate placeholder (`–`) when stats are None — most already do; fix the ones showing 0/`$0.00`. Same for doctor findings `tokens_zero/context_zero/cost_zero` (suppress when backend lacks `TRANSCRIPT_STATS`).
5. `usage_monitor` + sandbox badge behind `SUBSCRIPTION_USAGE` / `SANDBOX_PROBE` capability checks (widget hidden, not erroring).
6. Tests: NullStatsReader session renders sane TUI row (use `testing/tui_eye` snapshot); daemon tick with a stats-less backend neither crashes nor writes garbage tokens; Claude paths regression-covered by existing history_reader tests.

**Acceptance:** a hand-created session with `backend="__null_test__"` (test-only registered backend) shows dashes for tokens/cost/ctx and correct status; all Claude behavior unchanged.

### Phase 3 — Per-session status detection dispatch

**Objective:** make the hooks-vs-polling choice and the pattern set per-session/backend rather than global; move remaining hardcoded Claude glyphs into `StatusPatterns`.

**Key work items:**
1. Extend `StatusPatterns` with the currently-hardcoded items: tool-output prefixes (`⏺`), busy marker (`"esc to interrupt"`), input-hint marker (`"? for shortcuts"`), prompt nbsp convention, interrupt markers (from `hook_status_detector._INTERRUPT_PROMPT_MARKERS`), status-bar extraction regexes (bash count, subagents, monitors, auto-accept). `status_detector.py` and `status_patterns.py` module-level regexes consume the instance fields. `get_patterns(backend_name)` selects the set.
2. `StatusDetectorDispatcher`: `detect_status(session)` resolves mode per session — explicit per-agent override (`Session.hook_status_detection` already exists) → backend default (`HOOK_EVENTS` capability + recent hook-state freshness, reusing `settings.resolve_detection_mode` logic) → polling. The `K` hotkey toggles the **selected agent** (fallback: all agents of that backend); persisted per-agent instead of the single `detection_mode` file (keep reading the old file as a default for migration).
3. Polling detector: accept the patterns object fully (no module-global lookups); audit the 14 phases for Claude-only assumptions and put phase-level variance behind small pattern-driven predicates (e.g. `is_busy(lines)`, `is_input_ready(lines)`), not a subclass fork.
4. Tests: contract tests (`test_status_detector_contract.py`) parameterized over backends; regression corpus (`test_status_detector_realistic.py`) still passes for Claude; dispatcher unit tests for mixed-mode fleets.

**Acceptance:** two mock agents in one fleet, one forced hooks-mode and one polling-mode, both report correct statuses in the same daemon tick; `K` toggles only the selected agent.

### Phase 4 — opencode backend MVP (launch + polling status)

**Objective:** first user-visible opencode support: launch, monitor (polling), instruct, restart, kill, resume — no plugin, no stats yet.

> **Shipped Aug 2026.** The flag/gesture specifics written below were the *pre-verification plan*; three of them turned out to be wrong (`--permissions` does not exist, `ctrl+x q` is unnecessary and `C-c` kills opencode outright, and there is no prompt glyph at all). **Appendix A is now the authority** — it records what was empirically confirmed or refuted against a live v1.18.19. User-facing documentation is `docs/backends.md`.

**Key work items:**
1. `backends/opencode.py`:
   - `build_command`: `opencode` (binary via `OPENCODE_COMMAND` env override, mirroring `CLAUDE_COMMAND`); model → `-m <provider/model>`; permissiveness: bypass → `--auto`, permissive → `--auto` (document the difference; opencode has no exact "dontAsk"), normal → nothing; allowed_tools → `--permissions <csv>`; extra args pass through. Resume → `--session <id>`; fork → `--session <id> --fork` (capability `FORK` **on** — verify against the pinned opencode version at implementation time; if `--fork` semantics don't fit, drop the capability rather than emulating).
   - No `SESSION_ID_PRESCRIPTION`, `HOOK_EVENTS` (until Phase 5), `TRANSCRIPT_STATS` (until Phase 5), `SKILLS`, `SANDBOX_PROBE`, `SUBSCRIPTION_USAGE`, `AGENT_TEAMS`, `PERMISSION_INJECTION`.
   - `graceful_exit_keys`: Ctrl-C interrupt then `ctrl+x q` (leader) — verify; `clear_conversation_keys`: `/new`. `prompt_ready_chars`, startup rules: determined empirically (opencode has no trust-folder dialog by default).
   - `input_profile`: no bash-`!` special-case; slash commands still literal+delay (opencode has slash commands too); mouse-capture note: launcher writes/recommends TUI config disabling mouse for supervised panes.
   - `health_verdict`: process present = OK (no `--settings` analogue to check until Phase 5's plugin, then "plugin loaded" becomes the check).
2. `OPENCODE_PATTERNS` (`StatusPatterns` instance): permission = "Permission required" / "Allow once" / "Reject"; busy/spinner markers; prompt char; error patterns — sourced from a captured pane corpus. **Build the corpus first**: run real opencode in tmux, capture panes for idle/working/permission/error states, commit as fixtures (like `tests/unit/test_status_detector_realistic.py` does for Claude).
3. CLI/TUI surface: `overcode launch --backend opencode|-b` (default from `get_new_agent_defaults()["backend"]`); new-agent modal backend picker; backend column/badge in `summary_columns.py` (visible only when fleet is mixed); `overcode show` prints backend.
4. Mock: generalize `tests/mock_claude.py` → keep it, add `tests/mock_opencode.py` sharing `ScenarioRunner` via a small extracted module (or `mock_agent.py --flavor`), emitting opencode-accurate permission dialog/prompt chrome; wire `OPENCODE_COMMAND` in e2e conftest; at least: launch-and-idle, permission-prompt, error scenarios.
5. Docs: `docs/backends.md` (support matrix table), README mention, `doctor` checks opencode version against a tested range and warns on `autoupdate: true`.

**Acceptance:** `overcode launch -b opencode --prompt "…"` on a machine with opencode installed shows a green/orange-correct agent; send-instruction, restart --fresh, kill, resume all work; e2e suite passes with the opencode mock; a Claude-only user sees zero UI change.

### Phase 5 — opencode telemetry: plugin + SQLite stats

> **Shipped Aug 2026.** Built and driven against a live opencode v1.18.19.
> Two things the plan did not anticipate, both load-bearing:
> 1. **opencode calls *every* export of a plugin module as a plugin factory.**
>    An exported helper is invoked with the plugin context and throws during
>    load, taking the opencode process down with it. The bundled plugin
>    therefore has exactly one export; test seams hang off it as properties.
> 2. **`message.updated` re-fires for the same user message after the turn
>    ends**, so a naive "role == user → UserPromptSubmit" mapping pins the
>    agent green forever. The plugin de-duplicates by message id and prefers
>    the `chat.message` hook, which fires exactly once per prompt.
>
> Also new: `session.status {type: busy|idle}` and `permission.replied
> {reply: once|always|reject}` are real events; `permission.replied` is what
> clears `waiting_approval`, in both the allow and the reject case.
> User-facing documentation is `docs/backends.md`.

**Objective:** parity-grade status detection (hooks-equivalent) and cost/token columns for opencode.

**Key work items:**
1. Bundled plugin `src/overcode/opencode_plugin/overcode-telemetry.js` implementing the §2.3 event mapping, writing `hook_state_<agent>.json` / appending `hook_events_<agent>.jsonl` (same schema, atomic tmp+rename, same rotation limits) using env `OVERCODE_SESSION_NAME/TMUX_SESSION/STATE_DIR`. Also record the opencode session ID into hook state on `session.created`/first event.
2. Install path: launcher (opencode backend) ensures the plugin is referenced for the launched process — preferred: set `OPENCODE_CONFIG_DIR`/project `.opencode/plugins/` symlink or a generated project-local config including the plugin; must not pollute the user's global config; must work under the devcontainer wrapper (state dir already mounted).
3. Map event vocabulary into `HookStatusDetector`: add opencode tool names to the neutral maps where needed (the detector maps events→status via `_HOOK_STATUS_MAP` which is already overcode-vocabulary; the plugin emits overcode event names directly, so changes should be minimal). Backend gains `HOOK_EVENTS`; per-session dispatch (Phase 3) picks hooks mode automatically when state files are fresh.
4. `OpencodeStatsReader`: read-only SQLite (`file:...?mode=ro`), locate DB via `OPENCODE_DB`/`OPENCODE_DATA_DIR`/default path; match session rows primarily by the session ID captured by the plugin, fallback by `directory` + launch-time window; map tokens columns onto `AgentSessionStats` (`tokens_reasoning` → output bucket or a new field); cost = stored `cost`, recomputed via `pricing.py` when 0 (add opencode-routable model keys as needed); `current_context_tokens` from the latest assistant message's token snapshot if cheaply available, else leave None (column shows `–`). Handle DB-absent/locked/schema-drift by returning None (never crash the daemon tick) and surfacing a doctor finding.
5. Supervisor: `daemon_claude_skill.md` gains a backend-aware unblock section; supervisor context lines include each agent's backend; approve/reject gestures come from `backend.approve_keys()/reject_keys()` via a new `overcode send <name> approve|reject` alias so the skill text stays backend-neutral.
6. Tests: plugin unit tests run under Bun/Node in CI if feasible, else golden-file tests on the writer logic via a tiny JS test script; SQLite reader tests against a fixture DB (commit a schema-matched fixture, plus a schema-drift fixture asserting graceful None); e2e scenario where mock opencode also writes hook state.

**Acceptance:** an opencode agent shows live hooks-grade status (incl. waiting_approval on permission ask), token/cost/context columns populate, budgets enforce, and the supervisor can approve/reject an opencode permission prompt via standing orders.

### Phase 6 — Naming, docs, and hardening

> **Shipped Aug 2026.** Renames landed with aliases on every public surface
> (`ClaudeLauncher`/`AgentLauncher`, `claude_session_ids`/`agent_session_ids`,
> `active_claude_session_id`/`active_agent_session_id`, `extra_claude_args`/
> `extra_cli_args`, `claude_agent`/`agent_persona`, `ClaudeNotFoundError`/
> `AgentCliNotFoundError`, `ClaudeSessionStats`/`AgentSessionStats`,
> `--claude-arg`/`--backend-arg`). Two additions beyond the plan:
> `SessionDaemonState.backend_capabilities` (serialized flag names, published
> by the monitor daemon and consumed by sister TUIs), and `OVERCODE_BACKEND`
> in the launch env prefix for non-default backends only, so a Claude Code
> launch line stays byte-identical while `devcontainer.sh` can pick which CLI
> to install. The one *removal* from the plan: no allow-failure CI job runs
> the pattern corpus against latest opencode — the committed corpus plus
> `doctor`'s version-range check cover drift without a scheduled network job.

**Objective:** pay down naming debt, finish capability-gated UX polish, cross-machine story.

**Key work items:**
1. Renames with aliases: `ClaudeLauncher` → `AgentLauncher` (alias kept), `claude_session_ids`/`active_claude_session_id` → `agent_session_ids`/`active_agent_session_id` (dataclass property aliases + `from_dict` migration reading old keys — on-disk migration is a non-event thanks to tolerant deserialization; sister payloads keep emitting both for one release), `extra_claude_args` → `extra_cli_args`, `--claude-arg` → `--backend-arg` (deprecated alias retained), exceptions generalized (`AgentCliNotFoundError` with `ClaudeNotFoundError` subclass), help text/`cli/_shared.py` copy.
2. Sister protocol: include `backend` + serialized capabilities in agent payloads (rides the existing `daemon_state` passthrough); remote fork/action buttons gray out on missing capability; document that pre-backend sisters default to claude-code.
3. `devcontainer.sh`: parameterize agent install step per backend (npm claude vs opencode install), keyed off a `OVERCODE_BACKEND` env the launcher already exports.
4. Docs: architecture.md update (backends section + diagram), backends.md support matrix finalized, release notes; `AUDIT.md` entry.
5. Version-drift guardrails: `doctor` verdict for plugin-not-loaded (opencode), tested-version-range constants in `backends/opencode.py`, CI job (optional, allow-failure) running the polling-pattern corpus against latest opencode to detect chrome drift early.

**Acceptance:** grep for `claude` in `src/` returns only the claude-code backend module, Claude-specific subsystems (usage monitor, sandbox, skills), and compat aliases; mixed-fleet sister setup renders correctly across one old + one new host.

---

## 6. Phase Sizing & Sequencing Notes

| Phase | Size (single-agent session) | Risk | Ship gate |
|---|---|---|---|
| 1 Backend seam | L — wide but mechanical; golden-argv tests derisk | Low (behavior-frozen) | Full suite green, argv byte-identical |
| 2 Stats seam | M | Low | Null-backend TUI snapshot |
| 3 Per-session detection | M-L — dispatcher rework + pattern field migration | Medium (regression surface = status corpus) | Realistic-corpus tests green |
| 4 opencode MVP | L — needs live opencode for corpus capture | Medium (external dep) | e2e with opencode mock |
| 5 Telemetry | L — JS plugin + SQLite reader + supervisor | Medium-high (opencode churn) | e2e hooks-grade scenario |
| 6 Polish | M | Low | grep audit + sister compat test |

Sequencing is strict for 1→2→3→4→5; Phase 6 items can be cherry-picked earlier when touching adjacent files. Phases 1–3 are worth doing even if opencode is later abandoned — they delete existing debt (launcher god-class, global detection mode, silent-zero stats).

A pragmatic descope if velocity matters: ship Phase 4 with polling-only status and no stats ("observability-lite" tier), and treat Phase 5 as demand-driven.

---

## Appendix A — Claude Code ↔ opencode feature mapping

**Status: verified against a live opencode v1.18.19 during Phase 4 (Aug 2026, macOS/arm64, Homebrew npm install, `openai/gpt-4o-mini`).** Every row below is marked ✅ confirmed, ❌ refuted, or ⚠️ unverified. The pane corpus the behavioural rows were read from is committed at `tests/fixtures_opencode_panes/`.

| overcode concept | Claude Code | opencode | Verdict |
|---|---|---|---|
| Binary | `claude` (`CLAUDE_COMMAND`) | `opencode` (`OPENCODE_COMMAND`, new) | ✅ |
| Process basename in `ps` | `claude` | `opencode` (argv[0]); the shim symlinks to a compiled Bun `opencode.exe`, so both basenames are matched | ✅ |
| Bypass permissions | `--dangerously-skip-permissions` | `--auto` (deny rules still win) | ✅ |
| Permissive | `--permission-mode dontAsk` | `--auto` — **no separate mode exists**, so permissive and bypass are identical on opencode | ✅ (documented as approximate) |
| Allowed tools | `--allowedTools a,b` | ❌ **`--permissions` does not exist in v1.18.19.** Not in `opencode --help` (top-level or `run`). Tool restriction is config-only (`permission` map in `opencode.json`). overcode ignores `--allowed-tools` for opencode rather than emitting a flag that fails the launch | ❌ refuted |
| Model | `--model sonnet` | `--model provider/model` (`-m` alias) — must be fully qualified | ✅ |
| Persona | `--agent name` (`.claude/agents/*.md`) | ✅ **`--agent <name>` is a real launch flag** (the doc had guessed "config-only") | ❌ refuted (in opencode's favour) |
| Prescribe session id | `--session-id <uuid>` | ✗ — opencode mints `ses_<random>` ids. Confirmed via `opencode session list`. The launcher now skips prescription for backends without `SESSION_ID_PRESCRIPTION` so a bogus UUID is never bound | ✅ |
| Resume | `--resume <id>` | `--session <id>` — replays history in the TUI | ✅ |
| Fork | `--resume <id> --fork-session` | `--session <id> --fork` — creates a new session titled `… (fork #1)`; verified in `opencode session list` | ✅ confirmed → capability `FORK` **on** |
| Hook/telemetry injection | `--settings '<json>'` hooks → `overcode hook-handler` | bundled plugin at `<project>/.opencode/plugins/overcode-telemetry.js` writing hook-state files | ✅ built + driven live (Phase 5) |
| Transcripts/stats | `~/.claude/projects/<enc>/<sid>.jsonl` | SQLite `~/.local/share/opencode/opencode.db` (`session` table). Schema read live in Phase 5 and **matches the researched column list exactly** (`cost`, `tokens_input/output/reasoning/cache_read/cache_write`, `model` JSON, `directory`, `parent_id`, `time_created/updated`); per-turn tokens live in `message.data` JSON | ✅ confirmed |
| Graceful exit | C-c, `/exit` | ❌ **`ctrl+x q` unneeded and `C-c` is dangerous**: a single Ctrl-C kills opencode outright (no confirmation). `/exit` is a real slash command ("Exit the app") and works mid-turn. overcode sends `Escape`, `Escape`, `/exit`⏎ — the first Escape only *arms* the interrupt (`esc interrupt` → `esc again to interrupt`), the second cancels the turn | ❌ refuted / replaced |
| Clear conversation | `/clear` | `/new` ("New session") — verified to reset the pane to the banner | ✅ |
| Permission prompt text | "Do you want to proceed", `❯ 1. Yes` | `△ Permission required` / `# Shell command` / `Allow once   Allow always   Reject` / `ctrl+f fullscreen  ⇆ select  enter confirm`. The dialog *replaces* the input box — no info bar while it is up | ✅ |
| Approve / reject keys | Enter / Escape | Enter (confirms preselected *Allow once*) / Escape (dismisses, abandons the tool call) | ✅ both driven live |
| Trust-folder dialog | "I trust this folder" | none — with a provider credential in the env, opencode goes straight to the input box. No provider picker, no onboarding | ✅ confirmed absent |
| Prompt glyph | `❯` / `>` | ❌ **no prompt char.** The input is a *box* with a `┃` gutter; an empty input is a bare `┃` line, and the model footer (`┃  Build · GPT-4o mini OpenAI`) sits inside the same box | ❌ refuted / replaced |
| Busy marker | `esc to interrupt` | `esc interrupt` (and `esc again to interrupt` after one Escape), with a `⬝`/`■` block spinner | ✅ |
| Input-hint marker | `? for shortcuts` | `ctrl+p commands` / `tab agents`, plus the box's `╹▀▀▀` bottom border | ✅ |
| Assistant/tool output glyphs | `⏺`, `⎿` | `▣` closes each assistant turn (`▣  Build · GPT-4o mini · 6.0s`); `→ Read …`, `✱ Glob …`, `$ <cmd>` head individual tool calls | ✅ |
| Slash-command menu | `  /cmd   Description` | `┃ /cmd   Description   ┃` — drawn *inside* the box gutter | ✅ |
| Error rendering | `⎿ API Error: …` | ❌ **no structural marker.** Prose inside a red-*coloured* `┃` box; ANSI is stripped before matching, so only message texts are usable and the general case degrades to `waiting_user` | ❌ refuted |
| Status-bar counters (bashes / subagents / monitors / auto-accept) | `2 bashes`, `3 local agents`, `1 monitor`, `⏵⏵ auto-accept` | none — opencode's info bar carries only directory, tokens, cost, hints. Patterns are built unmatchable | ✅ confirmed absent |
| Session-id discovery | `--session-id` prescription | On `/exit` opencode prints a farewell block: `Session   <title>` / `Continue  opencode -s ses_…` — the cheapest non-SQLite source of the id | ✅ new finding (Phase 5) |
| Global config | `~/.claude/settings.json` | `~/.config/opencode/opencode.jsonc` (installer writes `.jsonc`, docs say `.json` — overcode reads both) | ✅ |
| Skills | `~/.claude/skills` | opencode *does* ship a `/skills` command, but there is no overcode integration | ⚠️ present, unintegrated |
| Subscription usage API | api.anthropic.com oauth/usage | ✗ | ✅ |
| Sandbox probe | loopback-listener heuristic | ✗ | ✅ |
| Mouse capture under tmux | n/a | Not observed to interfere: `send-keys`/`capture-pane` worked throughout corpus capture without touching TUI config | ⚠️ not stress-tested |

| Post-interrupt pane | `Interrupted by user` / `Interrupted · What should Claude do instead?` | `▣  Build · GPT-4o mini · interrupted` — a suffix on the finished-turn footer pill, not inline transcript text. Persists indefinitely; the partial response is left unmarked | ✅ new finding (Phase 6) |

Still not verified, deliberately: reasoning/"thinking" chrome — no reasoning-capable model was driven, so `thinking_markers` is left empty rather than guessed.

## Appendix B — Research provenance

Compiled from three deep-dive investigations (Aug 2026): (1) full Claude-coupling inventory of `src/overcode` with file:line references; (2) architecture/seam analysis including the "claude" hardcoding census (~880 mentions, 42 modules; 7 binary-name literals; 40 `~/.claude` path uses); (3) opencode surface research against opencode.ai docs, the anomalyco/opencode repo (`packages/core`, `packages/plugin`, `packages/tui`), release notes, and issue tracker. Key opencode caveats: storage moved to SQLite at v1.2.0; stored `cost` unreliable under subscription auth; `attach` mode has known bugs; plugin/HTTP surfaces are the stability sweet spot.
