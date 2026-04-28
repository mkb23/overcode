# Overcode vs Kagan: Feature Bakeoff

## Overview

| | **Kagan** | **Overcode** |
|---|---|---|
| **Repo** | [kagan-sh/kagan](https://github.com/kagan-sh/kagan) | This project |
| **Language** | Python 3.12+ (Textual TUI) + TypeScript (React 19 web + VS Code ext) | Python (Textual TUI) |
| **Stars** | Public, mid-sized (has Discord, VS Code marketplace listing, Snyk/Glama badges; exact count not captured) | N/A (private) |
| **License** | MIT (Copyright 2025 Altynbek Orumbayev / MakerX) — see `LICENSE` | Proprietary |
| **First Commit** | 2026-01-25 (`e561b3f`) | 2025 |
| **Last Commit** | 2026-04-14 (`32d7cec`, v0.17.1-beta.2) — active | Active |
| **Purpose** | Kanban-board TUI that orchestrates 14 coding agents in git worktrees, with managed or interactive runs, AI review, and GitHub issue sync | Claude Code supervisor/monitor with instruction delivery |

## Core Philosophy

Kagan treats **the task** (kanban card) as the primary unit of work, not the agent or the session. Every piece of work flows through a strict four-column state machine — `BACKLOG → IN_PROGRESS → REVIEW → DONE` (`docs/concepts/task-lifecycle.md`) — and the board enforces the transitions. The user creates tasks (manually, by importing GitHub issues, or via an AI orchestrator), presses `s` to start them, and the tool runs a coding agent in an isolated git worktree. When the agent finishes, the task enters REVIEW where a human (or AI) approves and merges. This is fundamentally a **project-management UI with agents underneath**, not an agent-management UI with tasks attached.

A second organizing idea is the **core process** (`docs/concepts/architecture-overview.md`): one backend daemon holds the SQLite state and spawns agents; the TUI, web dashboard, CLI, VS Code extension, and MCP server are all **thin frontends** that talk to the same core. Any task created via MCP immediately appears on the TUI board and the web UI. This multi-frontend architecture is what enables the VS Code extension, `@kagan` chat participant, and the bundled React dashboard to coexist without drift.

The third pillar is **agent-agnosticism**: 14 coding-agent backends are supported (Claude Code, OpenCode, Codex, Gemini CLI, Kimi CLI, GitHub Copilot, Goose, OpenHands, Auggie, Amp, Docker cagent, Stakpak, Mistral Vibe, VT Code — `docs/concepts/architecture-overview.md:45-60`). An AI **orchestrator** (separate from the worker) plans tasks, decomposes work, and drives a persistent chat REPL (`kagan chat`, or the in-TUI AI Panel on `Space`/`Ctrl+I`). Persona presets let teams share custom prompts via GitHub repos with a trust/whitelist flow.

## Feature Inventory

### 1. Agent Support

**Kagan** — 14 worker agents registered in `src/kagan/core/_agent.py` via `BackendSpec` dataclass and `AGENT_BACKENDS` registry:

- Claude Code (`claude`, ACP streaming via `npx claude-code-acp`)
- Codex CLI (`codex`, ACP via `@zed-industries/codex-acp`)
- Gemini CLI (`gemini`)
- Kimi CLI (`kimi`)
- OpenCode (`opencode`)
- GitHub Copilot (`copilot`)
- Goose, OpenHands, Auggie, Amp, Docker cagent, Stakpak, Mistral Vibe, VT Code

Backends are declared with `BackendCapability` flags: `MANAGED_DETACHED_RUN`, `ACP_STREAMING`, `PROMPT_ARGUMENT`, `WORKDIR_ARGUMENT`, `TASK_SCOPED_MCP` (`_agent.py:113-120`). New agents are added by editing the `_BACKEND_SPECS` dict — there's also a Python entry-point plugin group (`kagan.plugins`, see `docs/reference/plugins.md`) but it's marked experimental.

`kagan doctor` reports which agents are installed. `default_agent_backend` in `config.toml` picks the default (claude-code).

**Overcode** — Claude Code only. Agent-locked by design; hook/status integration assumes Claude Code's session model.

### 2. Agent Launching

**Kagan**:
- TUI: `n` on Kanban board → new-task form → `s` to start (managed run) or `a` to attach (interactive launch).
- CLI: tasks imported via `kagan import github --repo owner/repo --state open --label bug`; direct start via MCP `run_start(...)`.
- MCP tool `task_create(...)` supports single (`title`) or batch (`tasks` list).
- Web dashboard: click **+ New Task**, then **Start run** or **Attach**.
- Orchestrator chat (`kagan chat`) can create and start tasks conversationally.

Inputs captured per task: `title`, `description`, `acceptance_criteria`, `priority`, `repo` (for multi-repo projects), `branch` (`b` hotkey), `agent backend` override, `model` override. Prompt is composed from the task metadata via the three-layer prompt pipeline (`core/_prompts.py`): dotfile override (`.kagan/prompts/execution.md`) → code defaults + behavioral settings → `additional_instructions`. Placeholders `{title}`, `{description}`, `{acceptance_criteria}` are filled at launch.

Prompt refinement: `Ctrl+E` hotkey (`[refinement]` config, `skip_length_under=20`, `skip_prefixes=['/', '!', '?']`) rewrites prompts via an AI backend before send. `kagan tools enhance [PROMPT] --agent <name>` does the same from CLI.

Interactive launchers (`attached_launcher`): `tmux` (default Unix/macOS), `vscode` (default Windows), `nvim`, `cursor`, `windsurf`, `kiro`, `antigravity`. Neovim launcher tries `CodeCompanionChat → AvanteChat → CopilotChat → ClaudeCode` in order and preloads `g:kagan_start_prompt` + clipboard.

Templates: **persona presets** (`kagan tools prompts persona import|export|audit|trust|untrust`) distribute prompt-override sets via GitHub repos, gated by a trust whitelist (`registry/` directory, `--acknowledge-risk` flag).

**Overcode**: TUI `n` hotkey (host selector + new-agent dialog), `overcode launch` CLI, `--model` flag, `--resume`. Uses tmux `send-keys` to deliver the initial prompt. No persona/preset distribution system.

### 3. Session/Agent Lifecycle

**Kagan** has two orthogonal state machines:

Task states (`core/enums.py:6-10`): `BACKLOG`, `IN_PROGRESS`, `REVIEW`, `DONE`.

Session/Run states (`core/enums.py:13-18`): `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`.

Task transitions are constrained (`core/_transitions.py`): dragging a card between columns only works in the allowed directions; `REVIEW → DONE` **requires a merge** — no shortcut.

Persistence: SQLite at `kagan.db` in the `platformdirs` data dir (override `KAGAN_DATA_DIR`). Alembic-managed schema. Projects, tasks, reviews, sessions, insights, checkpoints, verifications all persisted. Chat sessions persist separately: max 300 messages per session, max 30 sessions total, oldest pruned automatically (`docs/guides/chat.md:93-97`). Session titles auto-generated after first exchange via a lightweight agent call.

Resume: `kagan chat --session-id <id>` resumes an orchestrator conversation. `kagan tui --session-id <id>` pre-attaches the TUI chat to a persisted session. Task scratchpad accumulates notes; reopening an IN_PROGRESS/REVIEW task shows a **Resume Context** strip (last ~500 chars). Full scratchpad via `task_get(..., include_scratchpad=true)` or `task_events`.

Survives: process restart (state is in SQLite + git worktrees). Core daemon auto-stops after `core_idle_timeout_seconds=180` idle and auto-restarts when a client connects (`core_autostart=true`). Transport fallback is `auto` (socket → tcp).

Cleanup: worktrees cleaned up after merge. `kagan reset` wipes all state (config, DB, worktrees); `--project NAME` scoped reset; `--dry-run` to preview.

**Overcode**: in-memory `Session` dataclass in `session_manager.py`, session state serialized to disk; resume via `--resume`; tmux-backed so sessions survive TUI restart. Statuses richer (`running`, `waiting_user`, `waiting_approval`, `error`, etc.) via 442-pattern regex + Claude Code hooks.

### 4. Isolation Model

**Kagan** isolates via **git worktrees**, one per task per repo (`docs/concepts/task-lifecycle.md:36-53`). Worktrees live at `~/.local/share/kagan/worktrees/task-<id>/` (override `KAGAN_WORKTREE_BASE`) — **outside** the user's repo so nothing pollutes `.gitignore`. Multi-repo projects get one worktree per repo per task.

Base-ref resolution is configurable: `worktree_base_ref_strategy` ∈ `remote` | `local_if_ahead` (default) | `local` — picks whether to branch off the remote tracking branch or the local branch when it's ahead. Branch names are generated per task; `b` hotkey (Kanban board) lets the user override.

Merge workflow: `m` on Task Screen runs `review_merge(...)` (destructive, MCP `destructive` annotation) — merges worktree branch → base branch; task → DONE. `b` on Task Screen runs `review_rebase(...)` with `action=start|continue|abort`. `review_conflicts(...)` returns conflict details (read-only). `serialize_merges=true` (default) queues merges sequentially to avoid races.

Checkpoints: `checkpoint_create(...)` makes a git checkpoint mid-run (`core/_checkpoints.py`); `checkpoint_list(...)` enumerates them; `session_rewind(...)` rewinds the worktree to a previous checkpoint — **undo button for agent work**.

Multi-agent per workspace: no (one worktree per task). Sub-task/sub-worktree: not supported — the model is flat (a task is atomic, though the orchestrator can create many related tasks).

**Overcode**: **shared repo** model (no worktree isolation; agents can collide on files). Manual branch management; "sync to main" = reset + pull; no merge workflow; agent hierarchy is logical (parent/child trees), not filesystem-isolated.

### 5. Status Detection

**Kagan** uses the **ACP (Agent Client Protocol)** for live streaming from supported agents (Claude Code, Codex — see `BackendCapability.ACP_STREAMING`). `_acp.py` and `acp-session-lifecycle.md` describe the session transport. For non-ACP agents, the agent is spawned as a detached subprocess (`MANAGED_DETACHED_RUN`) and its stdout/events are captured to the task scratchpad.

A **watcher** (`core/_watcher.py`, 271 lines) and **agent_monitor** (`core/_agent_monitor.py`) observe subprocess state. Events are appended to the task event stream and surfaced via `task_events(...)`. `task_wait(...)` long-polls for status changes (default 1800s, max 3600s).

Detected statuses: the Session state machine (`PENDING/RUNNING/COMPLETED/FAILED/CANCELLED`), plus implicit live-stream events (agent reasoning, tool calls, insights, verifications). Not LLM-classified — the protocol itself reports authoritative state. No regex-based heuristic polling.

Latency: instant for ACP backends (event-driven); subprocess exit for managed detached runs. Cost: zero extra API calls; detection is in-band.

**Overcode**: hybrid detection — 442-pattern regex polling (`status_detector.py`) + Claude Code hooks (instant, authoritative). 10+ statuses. Zero cost. Only works for Claude Code.

### 6. Autonomy & Auto-Approval

**Kagan**:
- `auto_approve=true` (default) — skip planner permission prompts.
- `auto_review=true` (default) — run AI review automatically on run completion.
- `require_review_approval=false` (default) — whether merge requires review approval first.
- `auto_confirm_single_tasks=false` — skip confirmation dialog for plans that contain only one task.
- `review_strictness` ∈ `strict|balanced|relaxed` controls review rigor.
- `planning_depth` ∈ `always|multi_task|never` controls when the orchestrator produces a plan.

Safety gate: **acceptance criteria required for AI-assisted approve/merge**. Tasks without criteria can still run but require explicit human review (`docs/concepts/task-lifecycle.md:89-95`, `docs/guides/managed-vs-interactive.md:52-58`). This is the main lever preventing runaway automation.

Managed runs are capped by `max_concurrent_agents=3` (configurable). No per-agent budgets; no dollar caps; no risk-assessment LLM. Trust is delegated to the worker agent's own permission model and the worktree isolation.

**Overcode**: supervisor daemon (Claude-powered) gates approvals; per-agent $ budgets with soft enforcement; heartbeat/standing instructions; permission modes `normal|permissive|bypass`. Much richer autonomy controls — but only for Claude Code.

### 7. Supervision & Instruction Delivery

**Kagan** has an **AI Orchestrator** as a first-class concept (distinct from workers):

- `kagan chat` CLI REPL with slash commands: `/help` (`/?`), `/exit` (`/q`), `/clear`, `/new`, `/sessions` (`/s`), `/agents` (`/a`), `/status`, `/project` (`/p`), `/delete`, `/tool`, `/flow` (`/f`). Session-scoped: `/sessions 2` to attach, `/sessions new` to create, `/sessions delete 3`.
- TUI **AI Panel** — `Ctrl+I` to toggle, `Space` to cycle split (vertical→horizontal→vertical) on Kanban/Task screens, `Ctrl+F` for fullscreen, `Ctrl+K` for session switcher.
- Session scoping: `--session-id <task_id>` attaches orchestrator to a single task (sees task state, worktree diff, recent events); no `--session-id` = global (all project tasks).

Standing instructions: `additional_instructions` free-text in `config.toml` (or web Settings → Personalization) is appended to every agent prompt. Prompt override files at `.kagan/prompts/{orchestrator,execution,review}.md` fully replace built-in prompts. No heartbeat / periodic instruction delivery — the orchestrator is reactive (you send, it responds).

During runs, workers can call `insight_add(task_id, category, content)` to append categorized reasoning notes (decision, risk, etc. — `docs/guides/managed-vs-interactive.md:66-74`) which feed into acceptance-criteria coverage checks and REVIEW display. `insight_list(...)` enumerates; `insight_remove(...)` (destructive) deletes.

Verification: `verify_step(...)` records PASS/FAIL verdicts on plan steps mid-execution; `verification_summary(...)` aggregates (`core/_verification.py`). Agents can report "I verified step 3" and humans see a checklist.

Intervention: `Shift+S` stops/detaches active run on board; `a` mid-run attaches and **auto-stops the managed run** then hands control to an interactive session (`docs/guides/managed-vs-interactive.md:43-50`) — explicit "take over" flow.

Audit: `core/_audit.py` tracks actions; MCP `settings_get`/`settings_set` is allowlisted.

**Overcode**: supervisor daemon with 25 standing-instruction presets; heartbeat for periodic delivery; agent hierarchy with cascade operations. No kanban/orchestrator separation — everything is Claude-direct.

### 8. Cost & Budget Management

**Kagan** — **Not supported**. No token tracking, no dollar calculation, no per-agent budget, no enforcement. Trust is delegated to each worker's own billing/auth (e.g., Claude Code's subscription, OpenAI key). GitHub Models integration (`docs/guides/github-models.md`) offers a free tier via OpenAI-compatible endpoints for agents that support it — cost control is "use a free model" not "cap spend."

**Overcode**: per-agent dollar budgets with soft enforcement; token accounting via Claude's JSONL session files; compact/side-question subagent attribution (commit `4f9be06`).

### 9. Agent Hierarchy & Coordination

**Kagan** — **Flat**. Tasks are independent; no parent/child task relationship is exposed in the data model. The orchestrator can create multiple related tasks (`task_create(tasks=[...])`) but they are peers. No cascade operations. No agent-to-agent direct messaging — coordination happens indirectly through the shared task board + scratchpad.

Task decomposition: AI-driven via the orchestrator and the `planning_depth` setting. `/flow` slash command (alias `/f`) exposes flow/plan primitives in chat. But there's no recursive/nested task structure — a plan is a flat list.

`serialize_merges=true` (default) is the closest thing to coordination: multiple ready-to-merge tasks queue sequentially.

**Overcode**: parent/child tree, 5 levels deep, fork-with-context, cascade kill/budget. Much richer hierarchy — but no kanban abstraction.

### 10. TUI / UI

**Kagan** TUI (Textual Python, entry point `kagan`/`kg`, `src/kagan/tui/`):

**Screens** (registered in `app.py` SCREENS dict): Welcome, Kanban board, Workspace, Task screen, Session dashboard, Settings modal.

**Global hotkeys**: `?`/`F1` help, `Ctrl+O` project selector, `Ctrl+R` repository selector, `Ctrl+,` settings, `Ctrl+Q` quit.

**Kanban board hotkeys**: `n` new task, `Enter` open (two-step: inspector first, then full screen), `w` switch to Workspace, `a` attach interactive, `Space` cycle AI split, `p` peek task, `e` edit, `x` delete, `y` copy task ID, `s` start agent, `Shift+S` stop/detach, `Shift+←/→` move task left/right, `/` search, `f` expand description, `Ctrl+F` fullscreen AI chat, `Ctrl+I` toggle AI panel, `Ctrl+K` session switcher, `Esc` close AI panel, `b` set branch, `.` quick actions menu.

**Workspace hotkeys**: `Enter` open session, `n` new session, `x` delete session, `/` search sessions, `Ctrl+I` focus chat, `Ctrl+K` switcher, `w` return to Kanban, `Esc` step back.

**Task Screen hotkeys**: `1`/`2` tab switch, `Enter` primary action, `e` edit, `d` delete, `a` approve, `x` reject, `m` merge, `b` rebase, `Ctrl+F` fullscreen AI, `Ctrl+I` toggle AI, `Ctrl+K` switcher, `Esc` back.

**Session Dashboard hotkeys**: `Enter` start/focus, `s` start, `x` stop, `r` restart, `Ctrl+I` AI panel, `Ctrl+Shift+T` fullscreen chat, `Ctrl+K` switcher, `Esc` back.

**AI Panel input**: `Enter` send, `Shift+Enter` newline, `Tab` accept completion, `Ctrl+J` focus latest output, `Ctrl+C` clear, `Esc` stop agent.

**Welcome Screen**: `Enter` open project, `n` new, `o` open folder, `1-9` quick open by position.

**Modals**: `Enter` confirm, `Esc` cancel, `Ctrl+S` save.

Layout: Kanban board (4 columns), Workspace is orchestrator-first (left sidebar = session list, main = chat surface). AI panel is a split overlay. Mouse supported (`KAGAN_TUI_MOUSE` env, default on; click-to-focus on cards).

Customization: themes (system/dark/light) via web Settings. No user-configurable columns or sort.

Web UI (`kagan web`): React 19 + jotai + Tailwind CSS 4, two views (Board + Workspace), toggle via `Cmd/Ctrl+Shift+W`. `Cmd+Shift+P` command palette, `Cmd+I` cycle AI panel, `Cmd+Shift+F` fullscreen AI, `Cmd+Shift+K` session switcher. Settings page with categorized sidebar (Preferences, Personalization, Shortcuts, Automation, Orchestration, Git, Environment, Models, Connection, System Checks).

VS Code extension (`packages/vscode/`): native Kagan sidebar in Activity Bar; `@kagan` chat participant in VS Code Chat panel; task diffs in built-in diff editor; review verdicts in Comments panel; one-click attach to task terminals.

**Overcode**: Textual full-screen dashboard + optional top/bottom split; ~50+ keybindings; configurable columns; timeline view; no separate kanban or workspace views; `K` for hook detection toggle (from memory).

### 11. Terminal Multiplexer Integration

**Kagan**: tmux is the **default interactive launcher on Unix/macOS** (`attached_launcher="tmux"`), not the core transport. Tmux hosts the worker when you press `a`. But multi-agent parallelism **does not require tmux** — managed runs are just detached subprocesses; their output flows through ACP or the event stream into the board.

Non-tmux launchers (`nvim`, `vscode`, `cursor`, `windsurf`, `kiro`, `antigravity`) are peer options. `skip_attached_instructions_popup=false` shows an instruction modal on first attach.

No tmux pane layout calculation, no split/zoom math — Kagan hands off and steps back. Live agent output is visible via: (1) the tmux pane you attached to, (2) the task detail view's event stream, (3) the AI panel overlay.

**Overcode**: tmux is the primary execution substrate; panes correspond 1:1 to agents; Overcode manages pane lifecycle, layout, and splits directly. Much tighter tmux coupling.

### 12. Configuration

**Kagan** config file: `config.toml` in `KAGAN_CONFIG_DIR` (platformdirs default). Data in `KAGAN_DATA_DIR`, cache in `KAGAN_CACHE_DIR`, worktrees under `KAGAN_WORKTREE_BASE`. DB at `kagan.db`.

**Sections** (from `docs/reference/configuration.md`):

`[general]`: `max_concurrent_agents` (3), `mcp_server_name` ("kagan"), `worktree_base_ref_strategy` (`remote|local_if_ahead|local`), `auto_review` (true), `auto_approve` (true), `auto_skill_discovery` (false), `require_review_approval` (false), `serialize_merges` (true), `default_agent_backend` (claude-code), `additional_instructions` (""), `review_strictness` (`strict|balanced|relaxed`), `planning_depth` (`always|multi_task|never`), `auto_confirm_single_tasks` (false), `attached_launcher` (tmux/vscode), `doctor_verbosity` (`tldr|short|technical`), `interaction_verbosity` (same), `default_model_claude` (null), `default_model_openai` (null), `core_idle_timeout_seconds` (180), `core_autostart` (true), `core_transport_preference` (`auto|socket|tcp`), `task_wait_default_timeout_seconds` (1800), `task_wait_max_timeout_seconds` (3600).

`[refinement]`: `enabled` (true), `hotkey` ("ctrl+e"), `skip_length_under` (20), `skip_prefixes` (`['/', '!', '?']`).

`[ui]`: `skip_attached_instructions_popup` (false), `tui_plugin_ui_allowlist` (`["official.github"]`).

`[plugins]`: `discovery` list of `module.path:ClassName`.

**Environment variables**: `KAGAN_CONFIG_DIR`, `KAGAN_DATA_DIR`, `KAGAN_CACHE_DIR`, `KAGAN_WORKTREE_BASE`, `KAGAN_TUI_MOUSE`, `KAGAN_SKIP_UPDATE_CHECK`, `KAGAN_SKIP_PREFLIGHT`, `KAGAN_ENABLE_PLUGIN_CLI`. Passed into agent sessions: `KAGAN_TASK_ID`, `KAGAN_TASK_TITLE`, `KAGAN_WORKTREE_PATH`, `KAGAN_PROJECT_ROOT`, `KAGAN_CWD`, `KAGAN_MCP_SERVER_NAME`.

**Prompt override files** (at repo root in `.kagan/prompts/`): `orchestrator.md`, `execution.md` (supports `{title}`, `{description}`, `{acceptance_criteria}` placeholders), `review.md`. Full replacement — when present, the built-in prompt is not used. Absence = built-in defaults.

Project-level vs global: config.toml is global. Per-project settings live in the SQLite DB (via `project_setup`/`project_update`). Multi-repo projects supported (one project can link multiple repos).

**Lifecycle hooks**: `core/_hooks.py` exists in source, but is not documented as a user-facing extension point. Users extend via MCP tools and the plugin entry-point system, not shell hooks.

Web/TUI settings sync: web dashboard's `/settings` page writes the same key-value store as config.toml; changes are immediate and bidirectional.

**Overcode**: settings.json + memory/ directory for per-project CLAUDE.md + standing instructions presets. Shell hooks via Claude Code's hook system. Richer lifecycle extensibility for Claude.

### 13. Web Dashboard / Remote Access

**Kagan** ships a **bundled React 19 web dashboard** (`packages/web/`, built into the wheel by `poe web-build` → `src/kagan/server/_web_static/`). Launch: `kagan web` (opens browser automatically; `--no-open` to suppress). Default bind `127.0.0.1:8765`. `--host 0.0.0.0` for LAN/Tailscale. `--readonly` / `--admin` access tiers (mutually exclusive). `--tls` for self-signed HTTPS on `kagan serve`.

Two views: **Board** (kanban) and **Workspace** (orchestrator-first chat). Toggle `Cmd+Shift+W`. Full settings UI with sidebar categories. Real-time sync via SSE across TUI + web + VS Code extension.

API: `kagan serve` runs the HTTP API server without the web UI (for programmatic/CLI integrations). REST + WebSocket/SSE endpoints (`src/kagan/server/_routes.py`, Starlette via FastMCP). Access tiers: default / readonly / admin. `kagan serve` uses same-origin for dashboard; separate pairing flow is explicitly unsupported.

Remote access: Tailscale recommended over raw `--host 0.0.0.0` exposure; reverse-proxy (nginx/Caddy) option; warning against public-internet unauthenticated exposure (`docs/guides/remote-access.md:91`).

Mobile: dashboard is responsive (manages from phone explicitly advertised — `docs/guides/remote-access.md:1-9`).

**Overcode**: web dashboard + HTTP API exists with analytics; Sister integration for cross-machine monitoring. Similar bidirectional real-time sync. No React/VS Code extension ecosystem.

### 14. Git / VCS Integration

**Kagan** is git-native. Worktrees per task (see §4). Branch names auto-generated from task ID; `b` hotkey overrides. Base ref strategy configurable (remote/local_if_ahead/local). Commits happen inside the worktree — either the agent commits, or Kagan commits at merge time (details in `core/_reviews.py`, 465 lines).

Merge (`m` hotkey, `review_merge` MCP): worktree branch → base branch. `review_rebase` with `action=start|continue|abort` for rebase flows. `review_conflicts(...)` surfaces conflict details to the orchestrator. `serialize_merges=true` default.

PR creation: **not documented as a built-in** — the tool merges locally to your base branch. For PR workflows, users presumably push and create PRs externally. `docs/guides/github.md` covers **import** only (issues in → tasks on board), not output.

GitHub integration: `gh auth login` required. `kagan import github --repo owner/repo [--state open|closed|all] [--label bug] [--label priority:high] [--limit 100] [--issues 1,2,42]`. Label auto-mapping: `priority:critical|high|medium|low` → task priority; other labels preserved in description.

Two-step TUI import (filter → select) with preview of already-synced issues marked `(synced)`. `Space` to toggle, `a` select all, `n` deselect all (`docs/guides/github.md:22-41`).

Native **GitHub Models** integration (`docs/guides/github-models.md`): 40+ LLM models, free tier, via OpenAI-compatible endpoints — agents with OpenAI-compatible support (OpenCode, Codex, etc.) can use them.

Git identity: "managed / system / custom" modes in web Settings → Git.

**Overcode**: manual branching; "sync to main" = reset + pull. No merge automation. No GitHub issue import. No PR creation. Much thinner VCS integration.

### 15. Notifications & Attention

**Kagan** — docs don't describe native desktop notifications, sound, or badge counts. Attention cues are in-TUI/web: `Ctrl+K` session switcher shows state; task scratchpad's Resume Context strip surfaces recent notes; board columns show counts (BACKLOG/IN_PROGRESS/REVIEW/DONE — `kagan list` output). Review queue is implicit attention prioritization (REVIEW column is where eyes go).

VS Code extension surfaces review verdicts in the Comments panel and diffs in the diff editor — these are attention hooks via the editor.

No explicit attention-weighting/prioritization algorithm documented.

**Overcode** also lacks native notifications (per candidates.md). Roughly parity.

### 16. Data & Analytics

**Kagan**:
- Session history: SQLite permanent (tasks, reviews, events, insights, checkpoints). Chat sessions bounded (300 msgs × 30 sessions).
- Task events: `task_events(...)` returns paginated execution events (newest-first).
- `kagan list` outputs per-project task counts by status.
- `kagan tools prompts export` exports resolved orchestrator/execution/review prompts for eval workflows (`--type`, `--output`, `--format yml|text`, `--model`).
- `evals/` directory in source tree (`docs/AGENTS.md` mentions eval workflows).
- No analytics dashboard or activity/presence tracking documented.
- No first-class export format (Parquet, CSV, etc.); data lives in SQLite and is queryable directly.

**Overcode**: Parquet export, analytics dashboards, cross-machine presence via Sister. Stronger analytics story.

### 17. Extensibility

**Kagan**:
- **MCP server** (first-class): `kagan mcp` on stdio with access tiers (`--readonly` worker-scope, default orchestrator-scope, `--admin` alias). `--role WORKER|REVIEWER|ORCHESTRATOR` preferred. `--session-id` binds context. `--enable-internal-instrumentation` exposes diagnostics tool.
- **MCP tool catalog** (~30+ tools across `toolsets/`): tasks (`task_get/list/create/update/delete/events/wait`), sessions (`run_start/cancel/get/detach/summary`, `verify_step`, `verification_summary`, `checkpoint_create/list`, `session_rewind`, `insight_add/list/remove`), projects (`project_list/setup/update`), review (`review_decide/merge/rebase/conflicts/verdict/clear_verdicts`), settings (`settings_get/set`), personas (`persona_inspect/import/export/trust`), plus plugins preflight/preview.
- **Plugin entry-point system**: `kagan.plugins` group, `module.path:ClassName` in `[plugins].discovery`. TUI declarative-UI allowlist (`tui_plugin_ui_allowlist=["official.github"]`) for plugins contributing screens. Experimental — CLI gated behind `KAGAN_ENABLE_PLUGIN_CLI=1`.
- **VS Code extension** as an extension point: chat participant, tree view, SCM provider, review comments.
- **Persona presets**: shareable prompt packs via GitHub repos with trust whitelist.

**Overcode**: MCP not mentioned in candidates.md. Hooks + supervisor daemon are the main extension points.

### 18. Developer Experience

**Kagan**:
- Install: `uv tool install kagan` or `uvx kagan`. Mac/Linux fallback: `curl -fsSL https://uvget.me/install.sh | bash -s -- kagan`. Windows PowerShell via `iwr uvget.me/install.ps1 ...`. PyPI distribution.
- First-run: Welcome screen → create project → board. `kagan doctor` runs preflight checks silently at startup, surfaces critical blockers only; manual `kagan doctor --verbosity tldr|short|technical` for detail. `kagan update` for upgrades (`--check-only`, `--prerelease`, `--force`).
- Documentation: MkDocs site at docs.kagan.sh; `docs/` directory has concepts/, guides/, reference/, internal/, troubleshooting.md. `llms.txt` for LLM consumption. Quickstart, managed-vs-interactive, chat, github, remote-access, vscode-extension, github-models guides. Extensive — easily 20+ distinct docs pages.
- Tests: `tests/core/`, `tests/tui/`, `tests/mcp/`, `tests/server/`, `tests/unit/`, `tests/helpers/`. Pytest parallel. `tests/helpers/` for fixtures (no fixtures in test files). Snapshot tests (`poe snapshot-update`).
- CI: lint → fast-gate (unit) → test-pr (full matrix py3.12-3.14 × ubuntu/macos/windows). Pre-commit: gitleaks, ruff, pyrefly, mdformat, uv-lock.
- Quality gates: 2500 LOC/file budget (`poe check-loc`), McCabe complexity cap 20 (ruff C90), pyrefly for typechecking (not mypy), vulture deadcode. Loguru everywhere (not stdlib logging).
- Semantic release with conventional commits; beta prereleases on main.
- Web build: `pnpm` workspace, React/Vite → bundled into wheel. Playwright for E2E (requires live `kagan web`).

**Overcode**: ~1700 tests, 5min runtime, Textual, Python 3.12+, pytest. Quality roughly comparable; Kagan's broader surface (3 client packages) means more CI matrix.

## Unique / Notable Features

1. **Kanban as the primary UX**. The four-column board is the main view, and transitions are enforced at the state-machine level — you cannot drag REVIEW → DONE without a merge (`core/_transitions.py`). This is a fundamentally different mental model from overcode's agent-first dashboard and maps cleanly to how PMs/humans actually think about work.

2. **Checkpoint + rewind**. `checkpoint_create(...)` snapshots the worktree mid-run and `session_rewind(...)` lets you roll back to any prior checkpoint (`core/_checkpoints.py`). This is an "undo button" for agent work — no other tool in the competitive set (per candidates.md) has this.

3. **14 agent backends with capability flags**. `BackendCapability` enum (`_agent.py:113`) lets agents declare `ACP_STREAMING`, `MANAGED_DETACHED_RUN`, `PROMPT_ARGUMENT`, `WORKDIR_ARGUMENT`, `TASK_SCOPED_MCP` — Kagan routes behavior based on capability, not hard-coded per-agent logic. Principled agent-agnostic design.

4. **Orchestrator / worker split**. The orchestrator (chat REPL) decomposes tasks and plans; workers execute. Different backend can be used for each. Scope can be global (all tasks) or task-scoped (`--session-id <task_id>` shows that task's state, diff, events). Clean separation.

5. **ACP streaming**. Agent Client Protocol (`core/_acp.py`) gives instant, authoritative event streaming without polling or regex scraping — for agents that support it (Claude Code, Codex). Per-event cost is zero.

6. **Persona preset distribution via GitHub + trust whitelist**. `kagan tools prompts persona import|export|audit|trust|untrust` with `registry/` whitelist and `--acknowledge-risk` flag. Teams can publish shared prompt packs and pull them in safely. No other tool in the competitive set has this.

7. **Insights**: mid-run structured reasoning notes (`insight_add(category, content)`) that inform acceptance-criteria coverage at review time (`docs/guides/managed-vs-interactive.md:66-74`). Turns agent chain-of-thought into reviewable artifacts.

8. **Verification gates**: `verify_step(task, step, verdict=PASS|FAIL)` records per-step verdicts during execution. `verification_summary(...)` aggregates. Checklist-driven review with AI + human verdicts at the granularity of individual acceptance criteria (`review_verdict(...)`).

9. **Three-layer prompt pipeline** with dotfile overrides (`core/_prompts.py`): `.kagan/prompts/{orchestrator,execution,review}.md` fully replaces built-in; if absent, defaults + behavioral settings + `additional_instructions` compose the prompt. Full user control without forking.

10. **Multi-client architecture**: one core daemon, five frontends (TUI, web, CLI, VS Code ext, MCP clients). Same state, real-time sync via SSE (`docs/concepts/architecture-overview.md:24-37`). VS Code extension in particular is substantial (chat participant, SCM provider, diff editor, comments).

11. **Prompt refinement hotkey** (`Ctrl+E`, `[refinement]` config). Before sending, a lightweight agent rewrites the prompt for clarity. Filters short inputs (<20 chars) and command-like prefixes (`/`, `!`, `?`).

12. **Attach mid-run intervention**. Pressing `a` on a managed-run task shows a warning modal, auto-stops the background agent, and hands off to an interactive launcher (tmux/VS Code/nvim) — an explicit "take over" flow (`docs/guides/managed-vs-interactive.md:43-50`).

## Strengths Relative to Overcode

- **Git worktree isolation out of the box**. One worktree per task per repo at `~/.local/share/kagan/worktrees/task-<id>/`, cleaned up on merge. Overcode has no isolation (shared repo; agents collide). This is a large structural gap.
- **Merge workflow**. `review_merge(...)` merges worktree → base; `review_rebase(start|continue|abort)` handles rebase; `review_conflicts(...)` surfaces conflicts; `serialize_merges=true` queues them. Overcode has "sync to main" (reset + pull) — no merge story at all.
- **Review as a first-class state**. REVIEW column enforces a human (or AI) gate before DONE. Approve/reject/merge/rebase are distinct operations. Overcode has no review abstraction.
- **Acceptance criteria + verification**. Per-criterion verdicts (`review_verdict`), step-by-step verification (`verify_step`), AI-review strictness levels (`strict|balanced|relaxed`). Turns "did the agent finish?" into "did it meet the spec?"
- **Kanban board as UX**. Tasks, not agents, are the primary unit. Matches how PMs and humans track work. Overcode's dashboard is agent-centric and has no task abstraction.
- **GitHub issue import with label auto-mapping**. Two-step filter→select UI in TUI, CLI equivalent, preview command, `(synced)` deduplication. Overcode has no issue import.
- **14 agent backends**. Agent-agnostic via capability flags. Overcode is Claude-only — losing Codex, Gemini, OpenCode, etc.
- **Checkpoint + rewind**. `checkpoint_create(...)` + `session_rewind(...)` for mid-run undo. Overcode has nothing analogous.
- **VS Code extension**. Native chat participant, sidebar, diff integration, review comments. Overcode has web dashboard but no IDE integration.
- **Persona preset distribution**. GitHub-sourced prompt packs with trust whitelist. Overcode has 25 hardcoded standing-instruction presets but no sharing/distribution mechanism.
- **ACP streaming**. Event-driven status for supported agents with zero polling overhead. Overcode achieves near-parity via Claude hooks but only for Claude.
- **Multi-frontend architecture**. One core, five clients (TUI/web/CLI/VS Code/MCP) with real-time SSE sync. Overcode has TUI + web but no VS Code integration.
- **Prompt override files** (`.kagan/prompts/*.md`) — full replacement at the project level. Overcode has memory/ files but no equivalent of full prompt replacement.
- **`kagan doctor` preflight**. Silent startup checks, escalating to visible only on blockers; three verbosity levels. Overcode has no documented equivalent.

## Overcode's Relative Strengths

- **Cost & budget management**. Per-agent dollar budgets, token accounting, soft/hard enforcement. Kagan has nothing — `max_concurrent_agents=3` is the only cap.
- **Supervisor daemon**. Claude-powered oversight with standing-instruction delivery, heartbeats to idle agents, periodic redirection. Kagan's orchestrator is reactive (user-driven chat); no autonomous supervision loop.
- **Agent hierarchy**. Parent/child trees 5 levels deep, cascade kill, fork-with-context. Kagan is flat — tasks are peers.
- **Hook-based status detection with rich taxonomy**. Claude Code hooks give instant, authoritative state; 442-pattern regex fills gaps; 10+ distinct statuses (`waiting_user`, `waiting_approval`, `error`, etc.). Kagan's `SessionStatus` has only 5 states (`PENDING/RUNNING/COMPLETED/FAILED/CANCELLED`).
- **Heartbeat / periodic instruction delivery**. Overcode can nudge idle agents with standing instructions on a timer. Kagan has no equivalent.
- **25 standing-instruction presets**. Curated library. Kagan has `additional_instructions` (freeform) + persona presets (GitHub-sourced) but no curated starter set.
- **Data export to Parquet**. Analytics-friendly format. Kagan data lives in SQLite; no export.
- **Sister integration for cross-machine monitoring**. Multi-host aggregation. Kagan supports remote access but single-host.
- **Claude-deep integration**. Session JSONL parsing, compact/side-question token attribution (commit `4f9be06`), Claude hook system fully leveraged. Kagan treats Claude as one of 14 backends — less depth per-agent.
- **Timeline view & configurable columns**. TUI customization. Kagan has themes but not column config.
- **~50+ keybindings** versus Kagan's ~40 across screens.

## Adoption Candidates

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **Git worktree isolation per task/agent** | High | High | Most impactful structural change. One worktree per agent at `~/.local/share/overcode/worktrees/<id>/`, cleaned on merge. Would eliminate file-conflict pain entirely. See `core/_worktrees.py` (346 lines) for reference implementation. |
| **Merge workflow with rebase/conflicts** | High | Med | `review_merge`, `review_rebase(start\|continue\|abort)`, `review_conflicts` — three MCP tools, maps cleanly onto existing "sync to main" infrastructure. `serialize_merges=true` default to avoid races. |
| **Kanban board view** (BACKLOG/IN_PROGRESS/REVIEW/DONE) | High | Med | Optional second top-level view alongside agent dashboard. Enforce transitions in state machine. Keep agent dashboard as primary; kanban as PM-facing lens. Task as a new abstraction above agents. |
| **Acceptance criteria + per-criterion verdicts** | High | Med | `acceptance_criteria` list per task; `review_verdict(criterion_id, verdict)` records AI + human verdicts. Gates AI-assisted merge. Gives review structure beyond "did it finish?" |
| **Checkpoint + session rewind** | High | Med | `checkpoint_create` snapshots worktree mid-run; `session_rewind` rolls back. Works on top of git worktree isolation. "Undo button" for agent work — unique differentiator if added. |
| **Prompt refinement hotkey** (`Ctrl+E`) | Med | Low | Before-send rewrite via lightweight agent. `[refinement]` config with `skip_length_under=20` and `skip_prefixes=['/', '!', '?']`. Small UX improvement with high visibility. |
| **ACP (Agent Client Protocol)** for non-Claude agents | Med | High | If Overcode ever adds a second agent (Codex/OpenCode), ACP gives free event streaming vs. per-agent regex. Investigate for Codex backend specifically. |
| **Persona preset distribution via GitHub + trust whitelist** | Med | Med | `persona import|export|audit|trust|untrust` with `registry/` whitelist and `--acknowledge-risk`. Teams share standing-instruction packs. Complements Overcode's 25 hardcoded presets. |
| **Insights as structured reasoning notes** | Med | Low | `insight_add(category, content)` appends categorized notes mid-run; feeds acceptance-criteria coverage at review. Cheap to add; surfaces chain-of-thought as reviewable artifacts. |
| **Task-scoped MCP session** (`--session-id <task_id>`) | Med | Med | Scope orchestrator to one agent (sees its state, diff, events). Useful for focused follow-up conversations in overcode's supervisor chat. |
| **Multi-backend capability flags** | Med | Med | If expanding beyond Claude, consider adopting the `BackendCapability` enum pattern (`MANAGED_DETACHED_RUN`, `ACP_STREAMING`, `PROMPT_ARGUMENT`, etc.) — principled routing by capability. |
| **GitHub issue import** with label auto-mapping (`priority:*`) | Med | Med | `overcode import github --repo owner/repo --state open --label bug`. Two-step TUI flow (filter→select). Obvious productivity win; requires `gh` CLI only. |
| **VS Code extension** (`@overcode` chat participant + diff editor + SCM) | Med | High | Large build but huge for adoption. React 19 web UI already exists; extending to VS Code extension is the natural next step. `packages/vscode/` reference structure. |
| **Attach mid-run intervention flow** | Med | Low | Press `a` on a running agent → warning modal → auto-stop managed run → hand off to tmux/editor. "Take over" flow with clear UX. |
| **`kagan doctor` silent preflight with escalation** | Low | Low | Overcode likely has ad-hoc startup checks; consolidating into `overcode doctor --verbosity tldr\|short\|technical` with silent-on-green behavior is a small polish win. |
| **Prompt override files** (`.overcode/prompts/*.md`) | Low | Low | Full replacement of built-in orchestrator/execution/review prompts at the project level. Placeholders like `{title}`. Users gain full control without forking. |
| **Review strictness / planning depth settings** | Low | Low | `review_strictness=strict\|balanced\|relaxed` and `planning_depth=always\|multi_task\|never` — tuning knobs for the supervisor daemon's behavior. Small addition; surfaces intent clearly. |
