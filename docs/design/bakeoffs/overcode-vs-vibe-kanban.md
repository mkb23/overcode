# Overcode vs Vibe Kanban: Feature Bakeoff

## Overview

| | **Vibe Kanban** | **Overcode** |
|---|---|---|
| **Repo** | [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban) | This project |
| **Language** | Rust (backend, ~30 crates) + TypeScript/React (frontend, Vite/Tailwind) + optional Tauri desktop app | Python 3.12+ (Textual TUI) |
| **Stars** | ~11k (widely referenced commercial product) | N/A (private) |
| **License** | See `LICENSE` file (source-available; commercial by BloopAI — hosted "Vibe Kanban Cloud" also offered) | Proprietary |
| **First Commit** | Unknown (shallow clone; repo predates 2026-03) | 2025 |
| **Last Commit** | 2026-04-13 (`b83a342 ci: replace Blacksmith runners`) | Active |
| **Distribution** | `npx vibe-kanban` (Node CLI that launches the Rust backend + web UI); Tauri desktop app; Docker for self-hosting | Python CLI + tmux |
| **Purpose** | "Kanban for coding agents" — plan tickets on a board, launch agents in isolated git worktrees, review diffs inline, open PRs, preview apps in an embedded browser | Claude Code supervisor/monitor with instruction delivery, cost tracking, and agent hierarchy |

## Core Philosophy

**Vibe Kanban** treats agents as **employees working on tickets**. The central mental model is a Jira-style kanban board (Todo → In Progress → In Review → Done); each "task" is an issue on the board, and each "task attempt" is a session with a specific coding agent against that issue. Every attempt runs in its own git worktree under a configurable branch prefix (default `vk/{shortid}-{slug}`). When the agent finishes, the task moves to **In Review** where the user can read inline diffs, leave line-level review comments that are batched into a single follow-up message, rebase/merge, or open a PR with an AI-generated description. The guiding insight from the README is that "software engineers spend most of their time planning and reviewing coding agents, [so] the most impactful way to ship more is to get faster at planning and review."

**Vibe Kanban is also a full browser IDE.** It ships with a "Workspaces UI" (the new panel-based layout superseding the classic kanban view): a four-panel React app with a conversation panel, diff viewer, process log tabs, and an **embedded preview browser** with devtools (Eruda), click-to-component selection (via an optional `vibe-kanban-web-companion` npm package), device mode emulation (desktop/mobile/responsive), and an integrated xterm.js terminal.

**Overcode**, by contrast, is a **terminal-native supervisor**. Agents share the main repo (no worktrees), are Claude Code only, and the focus is on monitoring status, delivering standing instructions via a supervisor daemon, tracking cost against per-agent budgets, and maintaining agent hierarchies that can fork with conversation context. Vibe Kanban optimises for *review velocity after the agent finishes*; Overcode optimises for *steering multiple agents while they're still running*.

## Feature Inventory

### 1. Agent Support

Vibe Kanban supports **10+ coding agents** via a pluggable executor registry (`crates/executors/src/executors/`). The `BaseCodingAgent` enum (`crates/executors/src/profile.rs`) and per-agent executor files cover:

- **Claude Code** (`claude.rs`, with `claude/` submodule for slash commands, protocol, types)
- **OpenAI Codex** (`codex.rs`, `codex/` submodule)
- **Gemini CLI** (`gemini.rs`)
- **GitHub Copilot CLI** (`copilot.rs`)
- **Amp** (`amp.rs`)
- **Cursor Agent CLI** (`cursor.rs`, `cursor/`)
- **OpenCode** (`opencode.rs`, `opencode/`)
- **Factory Droid** (`droid.rs`, `droid/`)
- **Qwen Code** (`qwen.rs`)
- **Claude Code Router** ("CCR" — multiplexes multiple Claude Code instances)
- **ACP** (`acp/` — Agent Client Protocol support, a generic protocol wrapper)
- `qa_mock.rs` — built-in mock executor for QA mode

Agents are added by implementing `StandardCodingAgentExecutor` (in `executors/mod.rs`) — **not a runtime plugin system**, but a compile-time trait. Default profiles are shipped in `crates/executors/default_profiles.json`.

**Overcode comparison**: Claude Code only. No plugin system for other agents. Deeper Claude-specific integration (hooks, session files) but zero agent diversity.

### 2. Agent Launching

Agents are launched as **task attempts** against **tasks on a kanban board**. Launch paths:

- **Keyboard shortcut `c`** from the project kanban view — opens the task-creation dialog with title/description fields
- **"Create Task" button** (+ icon top right) — adds task to Todo column without starting an agent
- **"Create & Start"** button — creates task and immediately starts it with default agent + current branch
- **Task attempt dialog** (+ icon on an existing task) — choose agent profile, variant, and base branch
- **Workspace creation** (new Workspaces UI, `+` in sidebar) — select project, add repos, pick target branch per repo, type prompt, select agent + variant, click **Create**
- **MCP**: external MCP clients (Claude Desktop, Raycast, agents-within-agents) can call `create_issue` and task-attempt tools (`crates/mcp/src/task_server/tools/task_attempts.rs`)
- **Via agent self-creation**: Vibe Kanban exposes itself as an MCP server so that a running agent can create sibling tasks in the same project

Inputs per attempt:
- **Agent profile** (e.g., `CLAUDE_CODE`, `GEMINI`, `CODEX`) + **variant** (e.g., `DEFAULT`, `PLAN`, `ROUTER`, `OPUS`, `APPROVALS`)
- **Base branch** (defaults to current branch)
- **Title + description** (description becomes initial prompt)

The initial prompt is the **task title + description**, sent via the agent's stdin or as a CLI arg depending on the executor. Each executor owns its launch command. Vibe Kanban also runs a **project-level setup script** before the agent starts (e.g., `npm install`).

**Templates**: Task tags (`@mention`-style reusable snippets, configured in Settings → Tags) can be expanded in the description. Agent profiles themselves act as templates.

**Overcode comparison**: Launched via TUI `n` hotkey (now unified with host selector) or CLI. Accepts prompt + model + permissions. No kanban board; no pre-run setup scripts per project; no task tags.

### 3. Session/Agent Lifecycle

**Task states** (`TaskStatus` enum, `crates/db/src/models/task.rs:14`):
- `Todo` — created but not started
- `InProgress` — task attempt running
- `InReview` — attempt completed (success or failure), awaiting user review
- `Done` — merged (or linked PR merged on GitHub; polled every 60s)
- `Cancelled`

**Execution-process states** (`ExecutionProcessStatus`, `execution_process.rs:43`): `Running`, `Completed`, `Failed`, `Killed`.

**Execution-process run reasons** (`ExecutionProcessRunReason`, `execution_process.rs:53`): `SetupScript`, `CleanupScript`, `ArchiveScript`, `CodingAgent`, `DevServer`.

**Session states** (docs): `Running`, `Idle`, `Needs Attention`.

**Persistence**: **SQLite database** (schemas in `crates/db/src/models/`), tables for `task`, `task_attempt`, `workspace`, `workspace_repo`, `session`, `execution_process`, `execution_process_logs`, `execution_process_repo_state`, `merge`, `pull_request`, `coding_agent_turn`, `file`, `tag`, `requests`, `scratch`. SQLx with offline-prepared queries. For cloud deployments there is also a Postgres-backed remote deployment (`crates/remote/`).

**Restart survival**: Full DB-backed — tasks, attempts, worktrees, logs, and session history survive process restarts. `DISABLE_WORKTREE_CLEANUP` env var disables cleanup for debugging.

**Resume**: Each task can have **multiple attempts** (fresh restart with different agent/variant/branch). Within a workspace there are **multiple sessions** (conversation threads) — each session maintains its own history; switching sessions doesn't kill the other agent.

**Cleanup**: Orphan and expired worktree cleanup runs automatically unless `DISABLE_WORKTREE_CLEANUP` is set. Worktrees are "ephemeral and automatically cleaned up after execution completes" (docs). Merged branches are not auto-deleted.

**Overcode comparison**: Session state via `Session` dataclass (session_manager.py) — JSON/yaml-ish. Sessions persist in tmux; `--resume` flag for Claude session resumption. No DB; no explicit states beyond Overcode's own 10+ statuses.

### 4. Isolation Model

**Git worktrees per task attempt** — the headline isolation feature. Worktree creation and management is dedicated to its own crate (`crates/worktree-manager/`).

- **Branch naming**: `{prefix}/{shortid}-{slug}` where `prefix` defaults to `vk` (configurable in Settings → Git → Branch Prefix). Examples: `vk/1a2b-implement-auth`, `feature/1a2b-implement-auth`, or `1a2b-implement-auth` with empty prefix.
- **Workspace directory**: `.vibe-kanban-workspaces` subfolder under the configured Workspace Directory (defaults to home).
- **Multi-repo workspaces**: A single workspace can span **multiple repositories** (crates/db `workspace_repo.rs`, `repo.rs`; crates/workspace-manager). Each repo gets its own worktree and its own target branch. The command bar exposes **Repo Actions** (Create PR, Merge, Rebase, Change Target Branch) per-repo.
- **Multiple agents sharing a workspace**: Via **sessions** — multiple sessions can run in the same workspace (same files) but with different agents and different conversation contexts. Changes from one session are immediately visible to others.
- **Subtasks / sub-worktrees**: Subtasks are first-class. A subtask is linked to a specific parent task attempt (not just the task) and inherits the parent's base branch. Subtasks produce their own feature branches (e.g. `feature/subtask-1`) that can be merged back into the parent branch, then the parent merges to main. Cascade visualised as a `gitGraph` in the docs.
- **Merge workflow**: In-app **Merge** button (target into working branch — i.e., rebase-style), **Rebase** button (when commits-behind > 0), **Create PR** (with AI-generated description option and Draft-PR support), **Push** (only appears when unpushed commits exist and a PR is open). Conflict resolution dialog lists conflicting files; user resolves in their editor and continues/aborts.

**Overcode comparison**: **No worktree isolation.** Agents share the main repo and can conflict. No per-agent branches, no built-in merge/rebase/PR flow. "Sync to main" (reset + pull) is the closest feature. This is the single largest architectural gap.

### 5. Status Detection

Vibe Kanban's status detection is **process-based**, not content-based:

- Each agent runs as an **ExecutionProcess** with `Running`/`Completed`/`Failed`/`Killed` status tracked in SQLite
- Logs are streamed via `crates/services/src/services/diff_stream.rs` and `events.rs` over WebSockets to the frontend
- **Filesystem watcher** (`services/filesystem_watcher.rs`) detects file changes during a run
- **Approval detection**: Agents that emit approval requests (Claude Code's permission prompts, Codex's approval levels) are surfaced via `crates/executors/src/approvals.rs` + `services/approvals/` — this drives the "Needs Attention" state
- **PR monitor** (`services/pr_monitor.rs`) polls GitHub every **60 seconds** to move tasks to Done when the PR merges
- **Dev server URL detection**: the preview feature sniffs stdout/stderr for `http://localhost:...` patterns to auto-load the preview browser

**What's detected**: `Running`, `Idle`, `Needs Attention` (approval pending), `Completed`, `Failed`, `Killed`, plus GitHub PR states (open/merged/closed).

**No LLM-based classification, no regex pattern library, no Claude hooks integration.** Detection is fundamentally structured (process exit codes + protocol-level approvals) rather than inferring from terminal output.

**Overcode comparison**: Overcode's 442-line regex library and Claude Code hooks give **richer status taxonomy** (`waiting_user`, `waiting_approval`, `running`, `error`, etc., 10+ states) and work by parsing terminal text — useful for any agent but brittle. Vibe Kanban's approach is cleaner because it *owns* the agent process lifecycle, so it knows state authoritatively, not by inference.

### 6. Autonomy & Auto-Approval

Per-agent variant configuration (see `docs/settings/agent-configurations.mdx` and `crates/executors/src/model_selector.rs` → `PermissionPolicy`):

- **Claude Code**: `plan` (planning mode on/off), `claude_code_router` (multi-instance routing), `dangerously_skip_permissions` (skip all permission prompts)
- **Codex**: `sandbox` ∈ {`read-only`, `workspace-write`, `danger-full-access`}; `approval` ∈ {`untrusted`, `on-failure`, `on-request`, `never`}; `model_reasoning_effort` ∈ {`low`, `medium`, `high`}; `model_reasoning_summary` ∈ {`auto`, `concise`, `detailed`, `none`}
- **Gemini**: `model` ∈ {`default`, `flash`}; `yolo` (no confirmations)
- **Amp**: `dangerously_allow_all`
- **Cursor**: `force` (no confirmation), `model`
- **OpenCode**: `model`, `agent`
- **Qwen**: `yolo`
- **Droid**, **CCR**, **GitHub Copilot**: simpler variants

When an agent *does* prompt for approval, Vibe Kanban surfaces it as a first-class "Needs Attention" state with a raised-hand icon and routes the approval through `services/approvals/`. **No LLM risk assessment** — approval is pass-through to the user.

**Overcode comparison**: Overcode has three permission modes (normal/permissive/bypass) and a **Claude-powered supervisor daemon** that can auto-approve based on standing instructions. Overcode's supervisor is more active (it *reasons about* the approval), whereas Vibe Kanban's is a dumb pass-through — but Vibe Kanban's per-agent granularity (Codex `on-failure`, `on-request`, etc.) is richer than Overcode's three-level model.

### 7. Supervision & Instruction Delivery

- **Send follow-up messages**: Chat-interface panel; typing a message queues it as a follow-up turn with the running agent
- **Inline review comments**: On completed attempts, click `+` on any line of the diff to add a comment; all comments are batched and sent as one follow-up message when you click **Send**, which moves the task back to In Progress
- **Queued messages** (`services/queued_message.rs`): Messages sent while an agent is busy are queued and delivered when it becomes idle
- **Slash commands**: Typing `/` in the chat surfaces a typeahead of agent slash commands (e.g., Claude Code's `/compact`, `/init`, etc.); arguments can follow. Implemented per-agent in `crates/executors/src/executors/claude/slash_commands.rs` and schemas in `shared/schemas/`
- **Edit & resend**: User messages have a pencil icon to edit and resend
- **Stop button**: in the navbar to kill the current execution process

**No standing instructions, no heartbeat, no periodic instruction delivery, no supervisor daemon, no intervention history log.** Supervision in Vibe Kanban is **reactive** (the user reviews after completion, then replies) rather than **proactive** (the tool repeatedly nudges the agent).

**Overcode comparison**: This is Overcode's clearest win. 25 preset standing instructions, custom-per-agent directives, a Claude-powered supervisor daemon, a heartbeat system for idle nudges, follow mode with stuck detection and timeouts, and an intervention history log — **none** of this exists in Vibe Kanban.

### 8. Cost & Budget Management

**Not supported.** No token tracking, no cost calculation, no per-agent budgets, no pricing model. Search of `crates/` and docs confirms no cost/token/budget terms in the feature set. Token limits are surfaced only as a UX hint ("context gauge" in the chat interface with orange/red indicators suggesting the user start a new session).

**Overcode comparison**: Overcode has per-agent cost tracking, dollar budgets with soft enforcement, compact/side-question subagent log accounting (see recent commit `4f9be06 Fix token double-counting from compact/side-question subagent logs`). Decisive Overcode win.

### 9. Agent Hierarchy & Coordination

- **Parent-child**: Subtasks link to a specific parent **task attempt** (not task). Each subtask has its own branch off the parent's branch. Shown in a **Task Relationships** panel: "Child Tasks (N)" with links, "Parent Task" link on subtasks. Nesting is allowed (subtasks of subtasks).
- **Sessions within a workspace**: Multiple sessions can run in the same workspace, each with its own agent, for parallel work. Sessions share files but not conversation context.
- **Inter-agent comms**: Agents can call the **Vibe Kanban MCP server** to list/create/update issues, which is effectively how an agent spawns or coordinates peers. No direct agent-to-agent messaging channel.
- **Cascade kill/budget**: Not supported.
- **Fork with context**: A new task attempt is explicitly a *fresh* start — no context inheritance from the previous attempt.

**Overcode comparison**: Overcode's hierarchy is deeper (5 levels, cascade kill, fork-with-context), but Vibe Kanban's **subtask-branch model** is more principled from a git perspective (the parent branch is a true integration point).

### 10. TUI / UI

- **Not a TUI.** Full **React + TypeScript browser app** with Tailwind, served by the Rust backend on a local port. Optional **Tauri desktop app** (`crates/tauri-app/`) wraps it for native feel.
- **Two frontends exist**:
  - **Classic kanban view** (`packages/local-web/src` legacy path) — kanban board with Todo / In Progress / In Review / Done columns
  - **New "Workspaces UI"** — four-panel layout, toggleable via "Open in Old UI" / "Open in New UI"
- **Workspaces UI panels**:
  - **Workspace Sidebar** (left edge): List of workspaces; flat or accordion layout grouped by Running / Idle / Needs Attention; search, pin, archive
  - **Conversation Panel** (left main): Chat history, session switcher, agent/variant dropdown
  - **Context Panel** (right main): **Changes** (file tree + diff viewer with inline comments, side-by-side or unified, wrap toggle, ignore-whitespace toggle), **Logs** (process-tabbed, searchable), or **Preview** (embedded browser with devtools)
  - **Details Sidebar** (right edge): Git status, **integrated xterm.js terminal** (new feature exclusive to Workspaces UI), Notes (per-workspace, auto-save)
- **Floating context bar**: draggable toolbar with IDE-open / copy path / toggle dev server / toggle preview / toggle changes
- **Keyboard shortcuts** (from docs):

| Shortcut | Action |
|---|---|
| `c` | Create task (classic kanban) |
| `Cmd/Ctrl + K` | Open command bar |
| `Escape` | Close command bar or dialog |
| `Cmd/Ctrl + Enter` | Send chat message |
| `Shift + Cmd/Ctrl + Enter` | Alternative send mode |
| `Cmd/Ctrl + B` | Bold (chat input) |
| `Cmd/Ctrl + I` | Italic |
| `Cmd/Ctrl + U` | Underline |

- **Command bar** (Cmd/Ctrl+K) — the "central hub", fuzzy-matched, organised into pages (Root, Workspace Actions, Git Actions, View Options, Diff Options, Repo Actions). Commands include: New Workspace, Open in IDE, Copy Path, Toggle Dev Server, Open in Old UI, Feedback, Workspaces Guide, Settings, Start Review, Rename/Duplicate/Pin/Archive/Delete Workspace, Run Setup Script, Run Cleanup Script, Create Pull Request, Merge, Rebase, Change Target Branch, Push, Toggle Left Sidebar / Chat / Right Sidebar / Changes / Logs / Preview, Toggle Diff View Mode / Wrap Lines / Ignore Whitespace, Expand/Collapse All Diffs, Copy Repo Path, Open Repo in IDE, Repository Settings, Create PR (repo), Merge (repo), Rebase (repo), Change Target Branch (repo).
- **Customization**: Theme (Light/Dark), Language, sidebar layout (flat / accordion), diff view mode (unified / side-by-side), wrap lines, ignore whitespace, per-workspace panel sizing. Device modes for preview (Desktop, Mobile 390×844 with phone frame, Responsive custom size).

**Overcode comparison**: Overcode is terminal-native (Textual TUI + tmux) with richer keyboard UX (~50+ bindings, timeline view, configurable columns, 4 sort modes). Vibe Kanban is a browser app — better for mouse-heavy review flows (diff clicking, inline comments) but requires leaving the terminal.

### 11. Terminal Multiplexer Integration

**Not used.** No tmux, no zellij, no screen. Instead, Vibe Kanban **owns the agent process directly** via `tokio::process` (Rust async child processes). Output is captured via PTY (via the `stdout_dup.rs` duplicator in `crates/executors/src/`) and streamed to the web UI via WebSockets + the logs panel. For interactive user terminal work inside a workspace, the **xterm.js terminal** in the details sidebar runs a shell in the worktree directory.

**Overcode comparison**: Overcode is tmux-first; Vibe Kanban is tmux-free. Different philosophies.

### 12. Configuration

- **Storage**: SQLite for project/workspace/task state; JSON files for profiles (`default_profiles.json`) and user overrides.
- **Settings UI pages** (`docs/settings/`):
  - **General**: Theme, Language, Default Coding Agent (agent + variant), Editor (VS Code / Cursor / Windsurf / Zed / Antigravity / Neovim / Emacs / Sublime / Custom shell command), Remote SSH Host, Remote SSH User, Git (Branch Prefix, Workspace Directory), Notifications (sound_enabled, push_enabled, sound_file picker), Telemetry toggle, Message Input (Enter vs Cmd/Ctrl+Enter), Tags list
  - **Agents**: Create/edit/delete agent profiles and variants (finder-style two-column layout); set default per agent
  - **MCP Servers**: Per-agent JSON editor; one-click popular servers (Vibe Kanban itself, Context7, Playwright, Exa, Chrome DevTools, Dev Manager)
  - **Projects & Repositories**: Setup scripts, cleanup scripts, dev server scripts per repository
  - **Organization Settings** (cloud): Members, roles
  - **Remote Projects** (cloud): Configure cloud-synced projects
  - **Creating Task Tags**: `@mention` reusable snippets
- **Environment variables** (from README):

| Variable | Scope | Default | Purpose |
|---|---|---|---|
| `POSTHOG_API_KEY` | build | empty | PostHog analytics (disabled if empty) |
| `POSTHOG_API_ENDPOINT` | build | empty | PostHog endpoint |
| `PORT` | runtime | auto | Prod: server port; Dev: frontend port |
| `BACKEND_PORT` | runtime | `0` (auto) | Backend port (dev only) |
| `FRONTEND_PORT` | runtime | `3000` | Frontend port (dev only) |
| `HOST` | runtime | `127.0.0.1` | Backend bind host |
| `MCP_HOST` | runtime | = HOST | MCP server host |
| `MCP_PORT` | runtime | = BACKEND_PORT | MCP server port |
| `DISABLE_WORKTREE_CLEANUP` | runtime | unset | Skip worktree cleanup for debugging |
| `VK_ALLOWED_ORIGINS` | runtime | unset | CORS allowlist (reverse proxies / custom domains) |
| `VK_SHARED_API_BASE` | runtime | unset | Cloud API base URL (desktop app) |
| `VK_SHARED_RELAY_API_BASE` | runtime | unset | Relay API base URL |
| `VK_TUNNEL` | runtime | unset | Enable relay tunnel mode |

- **Per-project scripts**: Setup script (e.g., `npm install`) — runs before agent starts; Cleanup script — runs on teardown; Dev Server script (e.g., `npm run dev`) — launched by Toggle Dev Server; Archive script.
- **Lifecycle hooks**: Only the three script types above; no generic Claude-Code-style hook system.

**Overcode comparison**: Overcode uses YAML, has no per-project setup scripts, no editor integration dropdown, no dev-server script. Vibe Kanban's config surface is broader and more integrated with external tooling.

### 13. Web Dashboard / Remote Access

- **Native web UI** — the primary interface is a local web app on `127.0.0.1:{PORT}`
- **Vibe Kanban Cloud** (hosted SaaS) — full remote deployment with GitHub/Google OAuth, organisations, team members, issue management, cloud-synced projects (see `docs/cloud/`)
- **Self-hosting** — Docker + Caddy setup (see `Caddyfile.example`, `Dockerfile`, `docs/self-hosting/deploy-docker.mdx`)
- **Remote crates**: `crates/remote/`, `crates/remote-web/` (PostgreSQL, ElectricSQL for sync, OAuth) and a full relay infrastructure (`crates/relay-client`, `relay-control`, `relay-hosts`, `relay-protocol`, `relay-tunnel`, `relay-tunnel-core`, `relay-types`, `relay-webrtc`, `relay-ws`, `trusted-key-auth`, `ws-bridge`, `embedded-ssh`) — built to let a cloud frontend reach a self-hosted backend through a relay/tunnel, with WebRTC and SSH-based options
- **API**: HTTP API via Axum (`crates/server/`), WebSocket event stream for live logs/diffs, types generated to TypeScript via `ts-rs` (single source of truth in `shared/types.ts`, `shared/remote-types.ts`)
- **Remote Deployment in config**: Remote SSH Host/User fields generate `vscode://vscode-remote/ssh-remote+user@host/path` URLs so "Open in VSCode" opens the remote path over SSH
- **Mobile**: Dedicated `mobile-testing.md` guide; device-mode preview supports mobile viewports. Tauri app supports desktop platforms. No mention of a native mobile app.

**Overcode comparison**: Overcode also ships a web dashboard + HTTP API + Sister integration for multi-machine monitoring, but has nothing like Vibe Kanban's relay/tunnel/WebRTC infrastructure or hosted cloud tier.

### 14. Git / VCS Integration

Deep and central:

- **Worktree per attempt** (see §4)
- **Commit automation**: Agents commit as they work (no automatic Vibe-Kanban-layer commits shown in docs — commits come from the agent itself)
- **PR creation**: `Create PR` button; AI-generated description option; Draft PR mode; pre-filled title (task title) and description
- **PR updates**: After a PR exists, the button becomes `Push` (disabled until new commits)
- **Merge** (in-app): pulls target branch INTO working branch (rebase-direction merge)
- **Rebase** (in-app): replays working branch commits onto target; conflict-resolution dialog lists conflicting files, resolve in editor, mark resolved or abort
- **Change Target Branch**: per workspace or per repo (multi-repo)
- **GitHub integration** (`crates/git-host/`, `docs/integrations/github-integration.mdx`): Uses the `gh` CLI — auto-install offered on macOS via Homebrew; manual on Win/Linux; auto-detects `gh auth login` status
- **Azure Repos integration** (`docs/integrations/azure-repos.mdx`): Uses `az` CLI + `azure-devops` extension; supports both modern (`dev.azure.com/{org}/{project}/_git/{repo}`) and legacy (`{org}.visualstudio.com/{project}/_git/{repo}`) URLs
- **PR monitor** (`services/pr_monitor.rs`): Polls every 60s; task auto-advances to Done when PR merges
- **Git2**: `git2` crate used throughout (`crates/git/`)

**Overcode comparison**: Overcode has no built-in merge/rebase/PR flow, no GitHub/Azure CLI wrappers. Decisive Vibe Kanban win.

### 15. Notifications & Attention

- **Sound effects**: Toggleable (`config.notifications.sound_enabled`); configurable sound file via `SoundFile` enum (`services/config`); plays across macOS/Linux/Windows
- **Push notifications** (desktop via Tauri; browser via Web Push API): Toggleable (`config.notifications.push_enabled`). Platform-specific OS commands in `DefaultPushNotifier` — macOS `osascript`, Linux `notify-send`, Windows PowerShell toast (WSL2 routes to Windows)
- **Tauri notifications**: `set_global_push_notifier()` allows the Tauri app to inject a native notifier (`crates/services/src/services/notification.rs`)
- **"Needs Attention"**: Raised-hand icon on workspace sidebar when an agent pauses for approval
- **Workspace grouping**: Accordion layout groups workspaces into Needs Attention / Idle / Running (attention-prioritized)
- **PR status badges**: Shown on workspace sidebar entries

No Slack / email / custom webhook notifications. No attention-scoring beyond the three-level grouping.

**Overcode comparison**: Overcode currently has no native notifications — this is a clear Vibe Kanban win.

### 16. Data & Analytics

- **Full session history**: All turns (`coding_agent_turn`), process logs (`execution_process_logs`), repo state per turn (`execution_process_repo_state`), merges, PRs persisted in SQLite
- **PostHog analytics**: Opt-in via build-time env vars; Telemetry toggle in General settings
- **No built-in Parquet/CSV export**; data is SQLite so users can query directly
- **No analytics dashboards in the app**; activity tracking is surfaced as workspace status indicators only
- **Notes**: Per-workspace rich-text notes, auto-saved, persisted
- **Scratch** table (`crates/db/src/models/scratch.rs`): Appears to be a general-purpose scratch store — likely for agent-visible context

**Overcode comparison**: Overcode has Parquet export, analytics dashboards, presence/activity tracking. Vibe Kanban has richer raw persistence (full audit trail of turns and repo states) but fewer user-facing analytics.

### 17. Extensibility

- **MCP server (Vibe Kanban as server)**: Exposes `get_context`, `list_organizations`, `list_org_members`, `list_projects`, `list_issues`, `create_issue`, `get_issue`, `update_issue`, `delete_issue`, `list_issue_priorities`, plus assignees/tags/relationships/remote-issues/remote-projects/repos/sessions/task_attempts/workspaces tools (`crates/mcp/src/task_server/tools/`). Launched via `vibe-kanban --mcp` (stdio); `MCP_HOST`/`MCP_PORT` also support network mode. Popular MCP clients supported: Claude Desktop, Raycast, and Vibe-Kanban-hosted agents themselves.
- **MCP client (agents use MCP servers)**: Each agent profile has its own MCP-server JSON config; one-click install for popular servers (Context7, Playwright, Exa, Chrome DevTools, Dev Manager, Vibe Kanban itself).
- **ACP** (`executors/acp/`): Agent Client Protocol — a generic wrapper for agents that implement ACP.
- **Custom agent definitions**: Not supported via plugin; must fork and implement `StandardCodingAgentExecutor` in Rust.
- **HTTP API**: Full REST + WebSocket API (Axum) — not explicitly documented as a public API but types are generated to TypeScript.
- **Tauri desktop bridge** (`crates/desktop-bridge/`, `crates/tauri-app/`): Native wrapper that injects a Tauri-backed notifier and likely handles deep-link/protocol-URL handling for the `vscode://` flow.
- **Web companion** (`vibe-kanban-web-companion` npm package): Lets the preview browser select React/Vue/Svelte/Astro components and send them to the chat as context.

**Overcode comparison**: Overcode is not exposed as an MCP server; no MCP client config per agent; no component-inspector web companion. Vibe Kanban's extensibility is substantially broader.

### 18. Developer Experience

- **Install**: `npx vibe-kanban` — one command. Authenticate your agent of choice first (no built-in auth; delegated to the agent CLI, e.g., `claude login`, `gh auth login`).
- **First-run**: Onboarding guide embedded in the Workspaces UI ("Workspaces Guide" icon in navbar, command bar entry). GitHub CLI install is offered automatically on macOS via Homebrew if missing.
- **Docs**: Extensive Mintlify-hosted docs at vibekanban.com/docs with images, videos, and accordion-based how-tos. Local source under `docs/` with sections: `agents/`, `cloud/`, `core-features/`, `settings/`, `workspaces/`, `integrations/`, `self-hosting/`, `browser-testing.mdx`, `supported-coding-agents.mdx`, etc.
- **Dev setup**: `pnpm i`; `pnpm run dev` (auto-assigns ports via `scripts/setup-dev-environment.js`)
- **Test coverage**: Rust unit tests (`cargo test --workspace`), Vitest where needed on the frontend. Feature-flagged `qa-mode` with `qa_mock.rs` executor for deterministic tests.
- **CI**: GitHub Actions (`.github/workflows/publish.yml`). Recently switched Blacksmith → GitHub runners (commit `b83a342`).
- **Stack**: Rust 2024/resolver 3 workspace with ~30 crates (Axum, SQLx SQLite+Postgres, tokio, ts-rs, git2, tracing), React/TypeScript/Vite/Tailwind, Tauri, ElectricSQL (for remote sync), PostHog.

**Overcode comparison**: Overcode uses Python/uv/Textual with a simpler dev setup. Vibe Kanban is a more ambitious product with a correspondingly larger build/deploy footprint.

## Unique / Notable Features

1. **Embedded preview browser with click-to-component** (`docs/browser-testing.mdx`). The preview panel wraps your dev server (detected automatically from stdout URLs), supports Desktop/Mobile-390×844-with-phone-frame/Responsive device modes, includes full devtools via Eruda, and lets you click a DOM element to send it to the chat as context. The optional `vibe-kanban-web-companion` npm package enables framework-aware component selection for React/Vue/Svelte/Astro/HTML. This is an entire capability class Overcode doesn't touch.

2. **Inline diff-view review comments, batched into a single follow-up message.** Click `+` on any diff line, leave comments across multiple files, hit Send; all comments are merged into one prompt to the agent and the task moves back to In Progress. A far better feedback loop than "type a paragraph into a terminal prompt."

3. **Multi-repo workspaces**. A single workspace can span multiple repositories, each with its own target branch, its own PR flow, and its own Repo Actions page in the command bar. Most tools in this space are single-repo only.

4. **First-class subtasks with git-graph semantics**. Subtasks are attached to a specific parent task attempt, inherit the parent's base branch, and get their own feature branches. The docs show an explicit gitGraph of parent/subtask merge topology — this is the one tool that has actually thought through the git hygiene of agent hierarchies.

5. **Command bar with fuzzy-matched pages**. Cmd/Ctrl+K opens a structured, paged palette (Root → Workspace Actions → Git Actions → View Options → Diff Options → Repo Actions). Context-aware (Push only shown when you have unpushed commits; Rebase only when behind target; Repo Actions only in multi-repo workspaces). This is a keyboard-driven UX well beyond what most browser apps offer.

6. **Vibe Kanban is itself an MCP server**. Agents running inside Vibe Kanban can call `create_issue`, `list_issues`, `get_context` etc. to create peer tasks or inspect their own workspace context. This closes the loop: an agent can decompose its own work into sibling tasks on the kanban board.

7. **10+ coding agents behind a common profile system** with variant-level toggles (plan/approvals/sandbox/reasoning-effort/yolo). Switching from Claude to Codex on a task attempt is a dropdown.

8. **Integrated xterm.js terminal inside the workspace sidebar**. Full shell emulation running in the worktree directory — persists across panel toggles.

9. **Relay/WebRTC/SSH tunnel infrastructure** (`crates/relay-*`, `crates/embedded-ssh`, `crates/ws-bridge`). A self-hosted backend can be reached from the hosted cloud frontend via a relay tunnel. An unusually ambitious piece of networking plumbing for a dev tool.

10. **AI-generated PR descriptions as a default-on option**. One checkbox and the PR description is generated from the diff.

## Strengths Relative to Overcode

Specific, concrete wins:

- **Git-worktree isolation** (§4) — each task attempt is branch-safe; Overcode has zero worktree support.
- **Inline-comment-on-diff review workflow** (§7, §10) — batched review feedback posted as a single follow-up message is strictly better UX than Overcode's terminal-chat interventions.
- **Integrated rebase/merge/PR flow with conflict dialog** (§14) — Overcode has none of this; just "sync to main" (reset + pull).
- **10+ agent support** (§1) vs Claude-Code-only.
- **Embedded preview browser with devtools, device modes, and click-to-component selection** (§10) — unique in this category of tools.
- **Multi-repo workspaces with per-repo target branches and per-repo Repo Actions in the command bar** (§4, §14).
- **Subtasks with git-graph semantics** (§9) — a more principled hierarchy than Overcode's flat-fork model.
- **Command bar (Cmd/Ctrl+K) with structured, context-aware command pages** (§10).
- **Vibe Kanban as an MCP server** so agents can create peer tasks (§17).
- **Kanban board as the primary planning surface** — Todo/InProgress/InReview/Done with auto-transitions on attempt start, completion, and merge (§3). Overcode has no project-planning surface.
- **Native OS notifications across mac/linux/windows, plus sound, plus attention grouping in the sidebar** (§15).
- **Task tags (`@mention` reusable snippets) for prompt templating** (§2).
- **Per-project setup/cleanup/dev-server scripts** (§12) — a clean primitive for repo-specific bootstrapping.
- **Hosted cloud tier with OAuth, orgs, members, and ElectricSQL-synced projects** (§13).
- **Full relay/tunnel infrastructure for reaching self-hosted backends from a cloud frontend** (§13).
- **ts-rs single-source-of-truth types across Rust/TypeScript** — cleaner full-stack DX than Overcode's Python+Textual.

## Overcode's Relative Strengths

- **Active supervision loop**: Standing instructions (25 presets + custom), heartbeat for idle nudges, Claude-powered supervisor daemon, intervention history. Vibe Kanban is *reactive* (user reviews after completion); Overcode is *proactive* (the supervisor keeps poking).
- **Cost tracking and per-agent budgets with soft enforcement** — Vibe Kanban has nothing here.
- **Claude Code hooks + 442-pattern regex library** — instant authoritative status detection for Claude, richer taxonomy (`waiting_user`, `waiting_approval`, `running`, `error`, etc.) than Vibe Kanban's process-level states.
- **Agent hierarchy with fork-with-context** — Vibe Kanban's new attempt is always a fresh start.
- **Cascade kill** across a tree of agents.
- **Terminal-native UX** — for users who live in tmux, leaving the terminal for a browser is friction.
- **~50+ TUI keybindings, timeline view, configurable columns, 4 sort modes** — denser keyboard UX than Vibe Kanban's web UI.
- **Sister integration for cross-machine monitoring** — Vibe Kanban's equivalent (relay tunnels) requires cloud infrastructure.
- **Parquet data export and analytics dashboards** — Vibe Kanban ships raw SQLite but no user-facing analytics.
- **Per-session dispatch of hook vs polling detection** (Overcode-specific `Session.hook_status_detection` flag) — fine-grained control Vibe Kanban doesn't need but also can't offer because detection is process-level.
- **Conversation forking with full context** — transplant a session's state into a new agent. Vibe Kanban's sessions are parallel but independent.

## Adoption Candidates

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **Inline diff-view review comments batched into one follow-up message** | **High** | **Medium** | The single best UX idea in Vibe Kanban. Overcode already has a web dashboard; adding a diff viewer with line-level comments and a "send all comments as one prompt" button would close a real workflow gap. Comments are collected client-side, concatenated with line-number context, injected into the prompt. Reference: `docs/core-features/reviewing-code-changes.mdx`. |
| **Git-worktree isolation as an optional mode** | **High** | **High** | A substantial architectural change for Overcode. Could be introduced incrementally: add a `--worktree` flag on agent launch that creates `vk/{shortid}-{slug}` under a configurable workspace dir, with an explicit merge step back to main. Reuse git2 in Python via PyGit2 or shell out to `git worktree`. Reference: `crates/worktree-manager/`. |
| **Kanban board as a planning surface (Todo / InProgress / InReview / Done) with automatic transitions** | **High** | **Medium** | Overcode lacks any upfront planning surface. Tasks could become first-class Session metadata with a simple three/four-column view; auto-advance on status changes. Reference: `docs/core-features/creating-tasks.mdx`. |
| **Cmd+K command bar with structured pages and context-aware commands** | **High** | **Medium** | Overcode already has a unified command bar (recent commit `634f9f2`); adding fuzzy search and context-aware hiding (e.g., only show "Rebase" when behind target) would close the gap. Reference: `docs/workspaces/command-bar.mdx`. |
| **Integrated rebase/merge/PR flow with conflict dialog** | **High** | **Medium** | The `gh`/`az`-CLI-delegation model is simple and works: shell out, capture output, show in a dialog. A first cut of `Create PR` + `Merge` + `Rebase` commands with a conflict-files list would take Overcode substantially further. Reference: `docs/workspaces/git-operations.mdx`. |
| **Multi-agent profile system with variants (plan/approvals/sandbox/yolo)** | **Medium** | **Medium** | Even while staying Claude-only, having named variants (e.g., `CLAUDE_CODE:PLAN`, `CLAUDE_CODE:YOLO`, `CLAUDE_CODE:REVIEW`) simplifies reuse. Reference: `crates/executors/src/profile.rs`. |
| **Embedded preview browser with dev-server auto-start and URL sniffing** | **Medium** | **High** | Very high value for frontend-heavy projects; huge scope (xterm/iframe/dev-server lifecycle/URL detection). Could start with just the dev-server-script + auto-detect-URL piece and leave the browser iframe to the user. Reference: `docs/browser-testing.mdx`. |
| **First-class subtasks with branch inheritance** | **Medium** | **Medium** | Overcode has parent/child trees; adding explicit `base_branch` propagation and a subtask-creation UI would make the hierarchy more git-aware. Reference: `docs/core-features/subtasks.mdx`. |
| **Task tags (`@mention` reusable prompt snippets)** | **Medium** | **Low** | Simple and compounds well: a tags table, autocomplete in the prompt box, expansion on send. Reference: `docs/settings/creating-task-tags.mdx`. |
| **Per-project setup/cleanup/dev-server scripts** | **Medium** | **Low** | Small, composable, useful — particularly if Overcode adds worktrees. Reference: `docs/core-features/monitoring-task-execution.mdx` §1. |
| **AI-generated PR descriptions** | **Medium** | **Low** | Overcode already has Claude access; generating a PR description from the diff is a one-prompt feature. Reference: `docs/workspaces/git-operations.mdx`. |
| **Native OS notifications (mac `osascript` / linux `notify-send` / windows PS toast)** | **Medium** | **Low** | Overcode has no native notifications. Straight port of `services/notification.rs`. |
| **Vibe Kanban's MCP-server-self pattern: expose Overcode's own API as an MCP server so running agents can spawn/coordinate peers** | **Medium** | **Medium** | Currently Overcode's hierarchy is created only from the TUI; letting an agent create children via MCP would enable self-decomposing flows. Reference: `crates/mcp/src/task_server/`. |
| **"New session vs new attempt" distinction** (shared worktree, fresh context window) | **Medium** | **Low** | A clean mental model Overcode could adopt for its fork-with-context feature. Reference: `docs/workspaces/sessions.mdx`. |
| **Relay/tunnel/WebRTC infrastructure for reaching a self-hosted backend from a cloud frontend** | **Low** | **High** | Enormous engineering investment for a benefit Overcode's Sister integration partially solves. File under "aspirational, probably not worth it." Reference: `crates/relay-*`. |

---

**Bakeoff sources**: `~/Code/vibe-kanban` (cloned 2026-04-15 at commit `b83a342`), primarily:
- `README.md`, `AGENTS.md`, `CLAUDE.md`
- `Cargo.toml` workspace
- `crates/executors/src/` (executor registry, profiles, approvals)
- `crates/db/src/models/` (task.rs, execution_process.rs, session, workspace)
- `crates/mcp/src/task_server/tools/` (MCP tool surface)
- `crates/services/src/services/` (notification.rs, pr_monitor.rs, approvals.rs, queued_message.rs, filesystem_watcher.rs)
- `crates/worktree-manager/`, `crates/git/`, `crates/git-host/`
- `docs/` (core-features/, workspaces/, settings/, integrations/, cloud/, browser-testing.mdx, supported-coding-agents.mdx)

Overcode comparison facts sourced from `docs/design/bakeoffs/candidates.md` "Notes on Overcode" section and the existing `overcode-vs-dmux.md` bakeoff.
