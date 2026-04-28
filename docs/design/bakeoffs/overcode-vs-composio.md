# Overcode vs Composio Agent Orchestrator: Feature Bakeoff

## Overview

| | **Composio Agent Orchestrator** | **Overcode** |
|---|---|---|
| **Repo** | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | This project |
| **Language** | TypeScript (Next.js/React web dashboard) | Python (Textual TUI) |
| **Stars** | ~5,600 (per candidates.md) | N/A (private) |
| **License** | MIT (`LICENSE`) | Proprietary |
| **First Commit** | 2026-02-13 (commit `0273e8f3`) | 2025 |
| **Last Commit** | 2026-04-15 (commit `ba77929c`, PR #1158) | Active |
| **Purpose** | Web dashboard for supervising fleets of autonomous AI coding agents, each in its own worktree + branch + PR | Claude Code supervisor/monitor with instruction delivery |

## Core Philosophy

**Composio Agent Orchestrator (AO)** is a "single pane of glass" dashboard for running 10–30+ parallel AI coding agents. Its core premise (`README.md:24`) is that agents should work autonomously in isolation — each with its own git worktree, branch, and PR — and humans only get pulled in when judgment is required. The system runs an *orchestrator agent* (itself a Claude Code instance) that spawns worker agents, monitors them, and escalates PRs/CI/reviews via configurable "reactions". It is explicitly **agent-agnostic**: Claude Code, Codex, Aider, OpenCode, and Cursor are all first-class plugins (`packages/plugins/agent-*`). The workflow is issue-centric: `ao spawn <issue>` → agent works in worktree → opens PR → CI/reviews feed back → reactions auto-route to agent or human.

The mental model is a **kanban board of autonomous agents**: a 6-column web UI (`DESIGN.md:99-107`) — Working / Pending / Review / Respond / Merge / Done — where each session card has a status pill and live activity dot. State lives in flat files under `~/.agent-orchestrator/{hash}-{projectId}/` (no database); sessions survive restarts by reconstructing from metadata files. Task decomposition is implicit via `ao batch-spawn` and issue trackers (GitHub, Linear, GitLab) rather than LLM-driven splitting.

**Overcode** by contrast is a **terminal-native supervisor** for Claude Code specifically. Agents share the repo (no worktree isolation), the dashboard is a full-screen Textual TUI in tmux, and the emphasis is on active supervision — a Claude-powered supervisor daemon, standing-instruction presets, heartbeats, per-agent cost budgets, and a 5-deep parent/child agent hierarchy. Overcode leans heavily into Claude Code's native hooks for authoritative status detection; AO leans on plugin-agnostic polling + JSONL activity logs + optional webhooks.

## Feature Inventory

### 1. Agent Support

- **5 agents** as first-class plugins (`packages/plugins/agent-*`): Claude Code (`agent-claude-code`), Codex (`agent-codex`), Aider (`agent-aider`), OpenCode (`agent-opencode`), Cursor (`agent-cursor`).
- Fully **agent-agnostic** — `defaults.agent` in config picks the default; per-project and per-role (`orchestrator` vs `worker`) overrides allowed (`agent-orchestrator.yaml.example:26,85-92`).
- New agents added via **plugin system**: implement `Agent` interface (`getLaunchCommand`, `getEnvironment`, `detectActivity`, `getActivityState`, `isProcessRunning`, `getSessionInfo`, optional `getRestoreCommand`, `postLaunchSetup`, `setupWorkspaceHooks`, `recordActivity`), export `PluginModule<Agent>` with Zod-validated config, place under `packages/plugins/agent-{name}/` (`CLAUDE.md:210-270`).
- OpenCode supports subagent selection (sisyphus, oracle, librarian).
- **Overcode**: Claude Code only; hardcoded detection; no plugin slot for other agents.

### 2. Agent Launching

- **CLI commands**: `ao start [url|path]`, `ao spawn [issue] [--agent X] [--branch Y] [--prompt Z]`, `ao batch-spawn 101 102 103`, `ao send <session> "message"`, `ao session ls --json`, `ao session kill <session>`, `ao session restore <session>` (`docs/CLI.md`, `packages/cli/src/`).
- **Inputs** (`SessionSpawnConfig`, `types.ts:225-235`): `projectId` (required), `issueId`, `branch`, `prompt`, `agent`, `subagent` (OpenCode only).
- **Prompt delivery** two modes (`Agent.promptDelivery`, `types.ts:322-329`):
  - `"inline"` — prompt passed via `-p` at launch.
  - `"post-launch"` — prompt sent via `runtime.sendMessage()` after agent starts (avoids Claude `-p`'s one-shot exit).
- **Templates**: `agentRules` (inline string), `agentRulesFile` (path to markdown), `systemPrompt` / `systemPromptFile` assembled by `prompt-builder.ts` (3 layers: base + rules + file) (`agent-orchestrator.yaml.example:79-100`, `examples/`).
- Web dashboard spawn: click-to-spawn + the kanban "Pending" column.
- **Overcode**: `overcode launch`, `overcode send`, TUI `n` hotkey with host/model/prompt flow.

### 3. Session / Agent Lifecycle

- **16 session statuses** (`types.ts:28-45`): `spawning`, `working`, `pr_open`, `ci_failed`, `review_pending`, `changes_requested`, `approved`, `mergeable`, `merged`, `cleanup`, `needs_input`, `stuck`, `errored`, `killed`, `idle`, `done`.
- **6 independent activity states** (`types.ts:48-54`): `active`, `ready`, `idle`, `waiting_input`, `blocked`, `exited`. Status + activity orthogonal — a session can be `pr_open` + `idle`.
- **Persistence**: flat files, no database. Storage tree at `~/.agent-orchestrator/{hash}-{projectId}/`:
  - `sessions/{sessionId}` (key-value metadata file)
  - `worktrees/{sessionId}/` (git worktree)
  - `archive/{sessionId}_{timestamp}` (completed sessions, ISO8601 stamped)
  - `.origin` marker (multi-checkout collision avoidance) (`ARCHITECTURE.md:12-59,156-166`)
- **Survive restarts**: yes — `ao start` scans sessions dir, reconstructs objects, resumes polling.
- **Resume**: `Agent.getRestoreCommand()` optional method; `isRestorable()` check (`types.ts:140-145,356-360`). Crashed workers can be respawned with prior branch context injected.
- **Cleanup**: `cleanup` is an intermediate terminal state; archive keeps ISO8601-stamped metadata snapshots.
- **Overcode**: ~10 statuses via regex/hook detection; sessions persisted as JSON via `session_manager.py`; resume via `--resume`.

### 4. Isolation Model

- **Git worktrees** default (`workspace-worktree` plugin). Each session: `~/.agent-orchestrator/{hash}-{projectId}/worktrees/{sessionId}/`, own branch, own HEAD.
- Alternative: `workspace-clone` (full repo clone fallback).
- **Containers/Docker**: Not supported.
- **Branch naming** by tracker plugin: `feat/GH-{issueNumber}` (GitHub), `feat/LIN-{issueId}` (Linear). Auto-created via `git worktree add -b`.
- **Session-prefix derivation** (`ARCHITECTURE.md:88-135`): single-word → first 3 chars (`integrator→int`), kebab → initials (`agent-orchestrator→ao`), CamelCase → caps (`PyTorch→pt`). Tmux session name: `{hash}-{prefix}-{num}` where `hash = SHA256(config-dir)[:12]` — enables multiple checkouts of same repo without collision.
- **Shared workspaces**: no — every agent isolated.
- **Merge workflow**: `SCM.mergePR(pr, method)` with `merge|squash|rebase` (`types.ts:616-617`). Auto-merge via reaction `approved-and-green { action: auto-merge }` (disabled by default, `examples/auto-merge.yaml:17`). `getMergeability()` returns `{noConflicts, blockers[]}`; conflicts escalated, agent must resolve and push.
- **Sub-tasks**: `ao batch-spawn` runs parallel workers but they're peers, not parent/child.
- **Overcode**: shared repo, no worktrees; "sync to main" = reset + pull; parent/child hierarchy handled in-memory with cascade kill.

### 5. Status Detection

- **Primary**: polling loop in the lifecycle manager at a hardcoded **5s interval** (`CLAUDE.md:173`, "do not change"). Per session it probes:
  - `Agent.getActivityState()` — reads JSONL activity log or agent-native API.
  - `SCM.getPRState()`, `SCM.getCISummary()`, `SCM.getReviewDecision()`.
  - Git worktree state (branch, commits).
- **Secondary**: optional **webhooks** (GitHub/GitLab) via `SCM.verifyWebhook()` + `parseWebhook()`. Configured per-project at `scm.webhook.{path,secretEnvVar}` — reduces latency from 5s to <1s for CI/review events.
- **3 activity-detection patterns**:
  - Agent-native JSONL (Claude Code `.claude/settings.json` PostToolUse hooks, Codex native JSONL).
  - AO activity JSONL `{workspacePath}/.ao/activity.jsonl` written by `recordActivity()` (Aider, OpenCode, new agents).
  - Terminal-output classification via `detectActivity()` (deprecated but retained).
- **46 event types** (`types.ts:909-946`) covering session lifecycle, PR, CI, reviews, automated reviews, merges, reactions, summary.
- **No LLM** used for status detection (unlike dmux).
- **Latency**: 5s polling / <1s via webhooks. **Cost**: GitHub GraphQL batch via optional `enrichSessionsPRBatch?()` to avoid per-PR REST calls (`types.ts:650-662`); local JSONL reads are free.
- **Overcode**: 442-line regex patterns + Claude Code hooks (instant, authoritative). No webhooks.

### 6. Autonomy & Auto-Approval

- **4 permission modes** (`AgentPermissionMode`, `types.ts:1295-1314`): `permissionless`, `default`, `auto-edit`, `suggest` (+ legacy alias `skip` → `permissionless`). Set per-project via `agentConfig.permissions`.
- **Reactions** (`agent-orchestrator.yaml.example:138-157`) drive unattended operation:
  - `ci-failed: {auto:true, action:send-to-agent, retries:2}`
  - `changes-requested: {auto:true, action:send-to-agent, escalateAfter:30m}`
  - `approved-and-green: {auto:false, action:auto-merge}` (opt-in)
  - `agent-stuck: {threshold:10m, action:notify, priority:urgent}`
- **No explicit risk scoring**. Escalation via `retries` count or `escalateAfter` duration. Two-stage delete confirmation in UI (2s amber "kill?" state, `DESIGN.md:328`).
- **Overcode**: supervisor daemon with Claude-powered judgment; 3 permission modes (normal/permissive/bypass); no reaction system.

### 7. Supervision & Instruction Delivery

- **Send to running agent**: `ao send <session> "Fix the tests"` → `Runtime.sendMessage(handle, message)`; tmux impl uses `tmux send-keys`.
- **Standing instructions**: `agentRules` (inline) + `agentRulesFile` (markdown path) injected into every prompt by `prompt-builder.ts`.
- **Heartbeat**: no explicit heartbeat, but 5s `getActivityState()` probe drives `active→ready→idle→stuck` transitions (30s/5min/10min thresholds).
- **Supervisor / meta-agent**: dedicated **orchestrator session** spawned by `ao start`, separate from workers. `defaults.orchestrator.agent` can differ from `defaults.worker.agent` (`types.ts:1101-1102`). Orchestrator receives worker states, PR info, feedback; decides spawns, retries, escalations.
- **Intervention history**: session metadata records `createdAt`, `lastActivityAt`, `restoredAt`, `status`, `pr`, `issue`. Activity JSONL = per-session timeline. `OrchestratorEvent` structure with priority/message/data. No dedicated audit log.
- **Overcode**: 25 standing-instruction presets; heartbeat cadence configurable; supervisor daemon uses Claude directly for judgement; intervention logged per agent.

### 8. Cost & Budget Management

- **Token tracking**: optional via `AgentSessionInfo.cost = {inputTokens, outputTokens, estimatedCostUsd}` (`types.ts:444-452`). Claude Code plugin populates; others optional.
- **Pricing**: delegated to plugin — Claude Code uses Claude API pricing; no shared pricing table.
- **Per-agent budgets**: **Not supported** — no `budget` field in config, no enforcement.
- **Display**: USD in session detail view; no per-project roll-up, no alerts.
- **Overcode**: per-agent $ budgets, soft enforcement, dashboard aggregates, parquet export.

### 9. Agent Hierarchy & Coordination

- **Implicit parent/child**: orchestrator session spawns workers; `Session` type has no explicit parent field — hierarchy is 1-level (orchestrator → many workers).
- **Agent-to-agent comms**: indirect only. Orchestrator observes workers, sends instructions via `Runtime.sendMessage()`. No peer messaging.
- **Task decomposition**: manual (config lists projects) + batch (`ao batch-spawn`). `orchestratorRules` reserved for future AI-driven splitting.
- **Cascade**: reactions chain (CI fail → send-to-agent → re-push → CI → …). No cascade kill; each session killed independently.
- **Follow mode**: Not supported.
- **Overcode**: 5-level parent/child tree, cascade kill, fork-with-context, follow mode with stuck timeout.

### 10. TUI / UI

- **Web dashboard** (`packages/web/`) — **not a TUI**. Next.js 15 App Router + React 19 + Tailwind CSS v4 + xterm.js 5.3.0.
- Accessed at `http://localhost:3000`. SSE (5s) for session updates; WebSocket for terminal PTY (default port 3001).
- **Layout** (`DESIGN.md:92-107`): 6-column Kanban desktop (Working/Pending/Review/Respond/Merge/Done), 3-column tablet, mobile accordion in urgency order (Respond > Merge > Review > Pending > Working).
- Session card: 2px status-colored left border, title, branch/PR metadata, status pill (dot + label), inset highlight. Topbar: page name only. Sidebar: project selector + session list.
- **Design system** (`DESIGN.md:10-91`): warm charcoal `#121110` background with cream `#f0ece8` text, warm periwinkle accent `#8b9cf7`, amber `#e2a336` for "respond". Semantic status colors separate **respond (amber, human decision)** from **error (red, broken system)**.
- Typography: JetBrains Mono (data), Geist Sans (body).
- **Keyboard shortcuts**: **None documented or implemented** (grep returns no shortcut maps). All interaction is mouse/touch.
- **Customization**: light/dark mode toggle; CSS custom properties. Columns/branding hard-coded.
- **Overcode**: ~50+ keybindings in Textual TUI; configurable columns, sort, timeline view.

### 11. Terminal Multiplexer Integration

- **tmux** default (`runtime-tmux` plugin) with `process` fallback (no multiplexing, child-process runtime).
- **zellij / screen / custom**: Not supported.
- One tmux session per agent, named `{hash}-{prefix}-{num}` (e.g. `a3b4c5d6e7f8-int-1`). One window, one pane.
- `isAlive()` checks tmux session exists; `getOutput()` uses `tmux capture-pane -p -S -{lines}`; `sendMessage()` uses `tmux send-keys` with Enter.
- **Live output**: xterm.js terminal in dashboard binds via WebSocket (`DirectTerminal.tsx`, `Terminal.tsx`) authenticated by session token; bidirectional (users can type into the agent).
- No layout calculation / split / zoom logic — multiplexing happens inside tmux per session, not across sessions.
- **Overcode**: tmux with zellij bakeoff considered; TUI split (top/bottom) around agent panes; capture-pane polling.

### 12. Configuration

- **Format**: YAML. **Location**: `agent-orchestrator.yaml` in repo root (auto-generated by `ao start`). Example 150 lines at `agent-orchestrator.yaml.example`.
- No global config file; all config is project-level YAML. Runtime data (not config) at `~/.agent-orchestrator/{hash}-{projectId}/`.
- **Top-level fields** (`OrchestratorConfig`, `types.ts:1017-1063`):
  1. `port` (web, default 3000)
  2. `terminalPort` (default 14800)
  3. `directTerminalPort` (default 14801)
  4. `readyThresholdMs` (default 300000 = 5min)
  5. `power.preventIdleSleep` (bool; macOS caffeinate; default true on darwin)
  6. `defaults.{runtime, agent, workspace, notifiers, orchestrator.agent, worker.agent}`
  7. `plugins[]` — `{name, source:registry|npm|local, package, version, path, enabled}`
  8. `projects{}` — per-project: `name, repo, path, defaultBranch, sessionPrefix, runtime, agent, workspace, tracker, scm, symlinks, postCreate, agentConfig.{permissions, model}, orchestrator, worker, agentRules, agentRulesFile, reactions`
  9. `notifiers{}` — per-notifier config
  10. `notificationRouting` — by priority (`urgent|action|warning|info` → notifier IDs)
  11. `reactions{}` — per-reaction: `{auto, action:send-to-agent|notify|auto-merge, message, priority, retries, escalateAfter, threshold, includeSummary}`
- **Env vars**: `LINEAR_API_KEY`, `SLACK_WEBHOOK_URL`, `GITHUB_WEBHOOK_SECRET`, `AO_SESSION_ID` (set per agent), `AO_ISSUE_ID`, `PATH` (prepended with `~/.ao/bin`). Secrets referenced as `${VAR}` in YAML.
- **Lifecycle hooks**: `postCreate: [...]` (shell commands after workspace creation, e.g. `pnpm install`). In-code: `setupWorkspaceHooks()`, `postLaunchSetup()`. **No pre-spawn, pre-merge, post-merge hooks** in config.
- **Overcode**: TOML config, `~/.config/overcode/`, Python-native hooks via Claude Code `settings.json`.

### 13. Web Dashboard / Remote Access

- **Web UI**: yes, primary interface. Next.js 15 + SSE + WebSocket. Responsive down to mobile (stacked columns).
- **API endpoints** (`packages/web/src/app/api/`): `GET/POST/DELETE /api/sessions[/:id]`, `GET /api/projects[/:id]`, `GET /api/events` (SSE), `ws://localhost:3001` (terminal PTY), `POST /api/webhooks/{provider}`.
- **No public REST API** for external integrations — internal use only.
- **Multi-machine**: single-machine architecture. Remote access via SSH tunnel / Tailscale / VPS. `power.preventIdleSleep:true` keeps macOS awake for Tailscale (`README.md:147-155`). macOS lid-close sleep is a hardware limit; clamshell workaround documented.
- **Mobile**: responsive web; no native app.
- **Overcode**: web dashboard + HTTP API with analytics, Sister integration for cross-machine monitoring.

### 14. Git / VCS Integration

- **Branch management**: tracker plugin generates name from issue; `git worktree add -b` auto-creates.
- **Commit automation**: agents commit themselves. Metadata hook intercepts via Claude `.claude/settings.json` PostToolUse (Claude Code) or `~/.ao/bin/git|gh` **PATH wrappers** (other agents) — captures commit hash, PR URL, branch into session metadata.
- **PR creation**: agent runs `gh pr create`; PATH wrapper parses output, writes PR info to metadata → dashboard flips session to `pr_open`. Agents are source of truth; no periodic GitHub-API scan needed.
- **Merge conflicts**: `getMergeability()` surfaces via `blockers[]`; no auto-resolution; escalate to human.
- **GitHub integration** (`scm-github` plugin): PRs, CI (Actions), reviews via REST + GraphQL; webhooks; batch PR enrichment via GraphQL.
- **GitLab integration** (`scm-gitlab` plugin): MRs, CI, approvals, webhooks.
- **Trackers**: GitHub Issues, Linear, GitLab issues (3 `tracker-*` plugins).
- **Merge methods**: `merge | squash | rebase` (`SCM.mergePR(pr, method?)`).
- **Overcode**: minimal git — "sync to main" reset/pull; no PR creation, no tracker integration.

### 15. Notifications & Attention

- **6 notifier plugins**: `notifier-desktop` (native OS), `notifier-slack` (webhook + action buttons), `notifier-discord` (webhook), `notifier-webhook` (generic POST), `notifier-composio` (Composio platform), `notifier-openclaw` (retries:3, retryDelayMs:1000).
- All implement `Notifier` interface (`types.ts:821-899`): `notify(event, context?)`, `getActions?(event, context?)` for action buttons (merge/review/kill).
- **Priority routing** (`notificationRouting`): `urgent:[desktop,slack]`, `action:[desktop,slack]`, `warning:[slack]`, `info:[slack]`.
- **Priority emoji**: urgent ⛔, action 👉, warning ⚠️, info ℹ️.
- **Sound**: not explicit — relies on OS notification sounds.
- **Visual**: dashboard status pill colors, pulsing activity dot (CSS animation, `DESIGN.md:121`).
- **Overcode**: no native notifications (gap noted in candidates.md).

### 16. Data & Analytics

- **Archival**: completed sessions moved to `archive/{sessionId}_{ISO8601}/`; metadata retained; worktree may be deleted.
- **Export**: `ao session ls --json` machine-readable dump. **No CSV/Parquet export**. No dedicated export endpoint.
- **Metrics dashboard**: none built-in. `Runtime.getMetrics?()` optional `{uptimeMs, memoryMb, cpuPercent}` exists but isn't surfaced in UI.
- **Activity tracking**: per-session JSONL at `{workspace}/.ao/activity.jsonl` with `{ts, state, source:"terminal"|"native", trigger?}`. Read via `readLastJsonlEntry()` helper.
- **Overcode**: Parquet export, analytics dashboard, per-agent cost timeline, presence tracking.

### 17. Extensibility

- **8 plugin slots** (`CLAUDE.md:82-95`): Runtime, Agent, Workspace, Tracker, SCM, Notifier, Terminal, Lifecycle Manager (core, non-pluggable).
- **23 plugin implementations**: 5 agents, 2 runtimes (tmux, process), 2 workspaces (worktree, clone), 3 trackers (github, linear, gitlab), 2 SCMs (github, gitlab), 6 notifiers, 2 terminals (iterm2, web).
- **Plugin loading** (`config.plugins[]`): `source: registry|npm|local`, version pinning, enable/disable.
- **MCP**: mentioned in `Agent.postLaunchSetup()` docstring ("e.g. configure MCP servers", `types.ts:362`) but not a first-class integration — delegated to the underlying agent (Claude Code handles MCP via its own `settings.json`).
- **External tool API**: Not supported for outbound; inbound only via SCM webhooks.
- **Custom agents**: via plugin system — publish as `@aoagents/ao-plugin-agent-{name}`, reference in config.
- **Overcode**: no plugin system; extensibility via Claude Code hooks only.

### 18. Developer Experience

- **Install**: `npm install -g @aoagents/ao` or `npx @aoagents/ao start` or `bash scripts/setup.sh` from source. Auto-detects npm permission issues and offers sudo / npm-global-dir / npx alternatives (`SETUP.md:58-105`).
- **First-run**: `ao start` zero-config auto-detects git remote, default branch, language, framework, test runner, available agents, free port, tmux availability. `ao start <repo-url>` clones + configures. `ao start <path>` adds to existing config.
- **Documentation** (extensive):
  - `README.md` (208 lines) — quick start, remote access, plugins.
  - `SETUP.md` (150+ lines) — install, troubleshooting.
  - `DESIGN.md` (334 lines) — tokens, layout, motion, accessibility, decisions log.
  - `ARCHITECTURE.md` (310 lines) — directories, session naming, metadata storage.
  - `CLAUDE.md` (471 lines) — code conventions, plugin standards.
  - `AGENTS.md`, `CLI.md`, `CONTRIBUTING.md`, `TROUBLESHOOTING.md`, `SECURITY.md`.
  - 5 example configs in `examples/` (simple-github, auto-merge, codex-integration, multi-project, linear-team).
- **Tests**: **3,288 test cases** (README badge). Vitest + @testing-library/react. `pnpm test`, `pnpm test:integration`. `istanbul-lib-coverage`.
- **CI** (`.github/workflows/`): `ci.yml` (lint + typecheck + tests), `integration-tests.yml` (tmux + all agent CLIs installed), `coverage.yml`, `security.yml`, `onboarding-test.yml`, `deploy-vps.yml`. Concurrency cancels in-progress on new push.
- **Overcode**: pip/pipx install; ~1700 pytest tests in ~5min; documentation in-repo but less extensive.

## Unique / Notable Features

1. **Agent-agnostic plugin architecture with strict typing** — 8 slots, 23 impls. New agents drop in as `@aoagents/ao-plugin-agent-{name}` packages with Zod-validated config; none of this exists in Overcode.
2. **Metadata-driven PR tracking via PATH wrappers** (`CLAUDE.md:339-341`, `agent-claude-code/src/index.ts METADATA_UPDATER_SCRIPT`) — Claude uses PostToolUse hooks; other agents get shimmed `~/.ao/bin/git` and `~/.ao/bin/gh` that intercept output and write session metadata. **Agent is the source of truth**, so a 5s poll is enough even without GitHub API calls.
3. **Reaction system with escalation ladders** (`agent-orchestrator.yaml.example:138-157`) — declarative rules mapping events → `{send-to-agent | notify | auto-merge}` with `retries` and `escalateAfter` (duration or count). CI fail auto-retries twice, then escalates to human.
4. **Orchestrator = just another agent** (`defaults.orchestrator.agent`) — meta-agent running in its own tmux session with its own rules, separate from workers. Supervisor is an agent, not hard-coded logic.
5. **Hash-namespaced session prefixes** (`ARCHITECTURE.md:88-135`) — `{SHA256(config-dir)[:12]}-{prefix}-{num}` lets multiple checkouts of the *same* repo coexist with zero tmux-name collisions while keeping user-facing prefixes short (`int-1`).
6. **16 statuses × 6 activity states orthogonal** (`types.ts:28-54`) — separates "what lifecycle stage" from "what is the agent doing right now". Enables states like `pr_open + idle` or `working + waiting_input`.
7. **Webhook-accelerated status detection** — primary is 5s polling, but optional GitHub/GitLab webhooks short-circuit to <1s. Graceful degradation (webhooks optional, not required).
8. **Warm intent-driven design system** (`DESIGN.md:10-91`) — warm charcoal `#121110` + periwinkle accent, amber reserved for "human decision needed" (not error). Status pill color semantics explicitly distinguish **respond (amber) vs error (red)** to prevent false urgency.
9. **Post-launch prompt delivery mode** — elegant fix for Claude Code's `-p` one-shot-exit behaviour: `promptDelivery: "post-launch"` starts the agent interactively, then sends the prompt via `sendMessage()`.
10. **Flat-file state, no DB** — `~/.agent-orchestrator/{hash}-{projectId}/` is grep-able, tail-able, and crash-resistant. Archive preserves ISO8601-stamped session snapshots.

## What This Tool Does Better Than Overcode

- **Agent-agnostic plugin system**. Overcode is Claude-only; AO supports 5 agents today and more via plugins. This is a structural capability gap, not a feature gap.
- **Git worktree isolation**. Every agent has its own branch + PR from spawn; Overcode's shared-repo model means agents can clobber each other.
- **End-to-end PR workflow**. `ao spawn <issue>` → worktree → branch → PR → CI reactions → auto-merge. Overcode has no PR layer.
- **Issue tracker integration**. GitHub Issues / Linear / GitLab pluggable. Overcode ingests no tickets.
- **Reaction-based autonomy**. Declarative `ci-failed: {auto, retries, escalateAfter}` rules + escalation ladders beat Overcode's supervisor-daemon-per-decision model for scale (30 agents).
- **Metadata-driven PR tracking**. PATH-wrapper shims for `git`/`gh` are clever and work across agents. Overcode relies on Claude-specific hooks.
- **Webhook status detection**. Overcode has no webhook path; a CI fail waits for the next poll.
- **6 notifier plugins incl. desktop/Slack/Discord**. Overcode has no native notifications (documented gap).
- **Design system documented in 334-line `DESIGN.md`**. Overcode's TUI has no comparable design token doc.
- **3,288 tests + integration CI with all agent CLIs installed**. Overcode is ~1,700 tests, Claude-only.
- **Zero-config onboarding** (`ao start <url>`). Overcode requires manual host/session setup.

## What Overcode Does Better

- **Richer supervision loop** — Claude-powered supervisor daemon can make judgement calls that AO's declarative reactions can't express. 25 standing-instruction presets vs AO's single `agentRules` string.
- **Per-agent cost budgets with soft enforcement** — AO tracks cost for display only; no budget, no cap.
- **Deeper agent hierarchy** — 5-level parent/child with cascade kill and fork-with-context. AO has implicit 1-level orchestrator→workers only.
- **Heartbeat / periodic instruction delivery** — AO's 5s probe detects state but doesn't proactively re-inject instructions.
- **Hook-based instant status detection for Claude** — AO's 5s polling is slower than Overcode's Claude Code hooks for in-session state changes.
- **Terminal-native UI**. For SSH-only / remote-tmux users, Overcode's TUI works everywhere; AO's web dashboard requires port-forwarding.
- **Configurable TUI columns, sort, timeline view** — AO has zero customization and zero keyboard shortcuts.
- **Data export to Parquet + analytics dashboard**.
- **Python ecosystem** — Textual TUI, pytest, pyproject.toml.
- **Sister integration for cross-machine monitoring**.

## Ideas to Steal

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **Reaction system with escalation ladders** (auto/retries/escalateAfter per event type) | High | Medium | Declarative YAML rules for "when X, do Y, escalate after N". Great fit for Overcode's existing supervisor daemon — augment rather than replace the Claude judge. `agent-orchestrator.yaml.example:138-157`. |
| **PATH-wrapper metadata interception** (shimmed `git` / `gh` in `~/.ao/bin`) | High | Medium | Works across agents without agent-native hooks. Overcode could extend beyond Claude-only by intercepting `gh pr create` / `git commit` this way. |
| **Orthogonal session status × activity state** (16 × 6) | High | Low | Overcode currently conflates lifecycle stage with activity. Splitting them removes impossible states like "running but idle" as an exception. `types.ts:28-54`. |
| **`notificationRouting` by priority** (urgent/action/warning/info → channel list) | High | Low | Closes Overcode's native-notification gap with a clean routing layer. Desktop + Slack + webhook notifiers pluggable. |
| **Hash-namespaced tmux session names** (`SHA256(config-dir)[:12]`) | Medium | Low | Lets users open the same repo twice without tmux-name collisions. Short user-facing prefix (`int-1`) with globally unique backing. `ARCHITECTURE.md:88-135`. |
| **Agent plugin interface** (Agent + Runtime + Workspace slots) | Medium | High | Long-term play to add Codex/Aider support. Start by extracting the Claude-specific pieces into an interface. |
| **Webhook-accelerated status detection** (GitHub/GitLab webhooks) | Medium | Medium | Drops event latency from seconds to <1s. Overcode already has HTTP API — add `/webhooks/{provider}` + signature verification. |
| **Post-launch prompt delivery mode** | Medium | Low | For future non-Claude agents whose `-p` mode exits after one shot. `runtime.sendMessage()` after launch instead of inline `-p`. |
| **Archive with ISO8601-timestamped snapshots** (`{id}_{timestamp}` dir) | Medium | Low | Cleaner than Overcode's in-place session retirement; supports history replay. |
| **Semantic status colors** (amber = respond, not error) | Low | Low | Cheap TUI polish — use amber for "human decision" and red for "broken system", never mix. `DESIGN.md:228`. |
| **Zero-config onboarding** (`ao start <repo-url>`) | Medium | Medium | Huge first-run experience win. Auto-detect remote, branch, framework, test runner, free port. |
| **`agentRulesFile` markdown path** (in addition to inline string) | Low | Low | Better than stuffing 2000-word rules into a TOML string. |

---

*Analysis complete. Source: `~/Code/agent-orchestrator` at commit `ba77929c` (2026-04-15).*
