# Overcode vs Superset: Feature Bakeoff

## Overview

| | **Superset** | **Overcode** |
|---|---|---|
| **Repo** | [superset-sh/superset](https://github.com/superset-sh/superset) | This project |
| **Language** | TypeScript (Electron + React + Next.js) | Python (Textual TUI) |
| **Stars** | Not retrieved (listed in `candidates.md` as having "corporate adoption at Amazon, Google") | N/A (private) |
| **License** | Elastic License 2.0 (ELv2) — source-available, no SaaS resale | Proprietary |
| **First Commit** | Shallow-cloned; only commit `1b7cb9c` visible, dated 2026-04-15 | 2025 |
| **Last Commit** | 2026-04-15 15:04:42 -0700 | Active |
| **Purpose** | Electron desktop "Code Editor for AI Agents": worktree-isolated, multi-agent orchestration with terminal, editor, diff viewer, browser, and chat panes | Claude Code supervisor/monitor with instruction delivery |

## Core Philosophy

Superset positions itself as **"The Code Editor for AI Agents"** — a full-blown **Electron desktop IDE** purpose-built around CLI agents rather than a supervisory control surface. The mental model is: you have a **project**, each task runs in its own **workspace** (= git worktree on its own branch), and inside each workspace you arrange a flexible **tab-and-pane layout** built from five primitives — terminal, chat, file-viewer, browser, devtools. Agents are launched into terminal panes via their native CLI invocation (`claude --dangerously-skip-permissions`, `codex`, `gemini --yolo`, etc.) and the IDE wraps around them with diff views, PR links, commit timelines, and OSC-133 shell-readiness detection.

The workflow loop is **IDE-centric**: open project → quick-create workspace (`⌘⇧N`) → Superset runs `git worktree add`, executes `.superset/setup.sh`, and launches a configured agent → human watches/edits in parallel panes → GitHub PR opens from the worktree branch → merge externally. There is no "supervisor" layer — the human is the supervisor and the IDE is the instrument panel. Where Overcode treats agents as *processes to watch and steer*, Superset treats agents as *subprocesses inside a pane-based editor*.

The target user is a developer already running 3–10 concurrent agents who needs real GUI affordances (diff viewer, syntax highlighting, browser preview of a dev server) rather than a dashboard. Agent-agnosticism is a first-class value: 9 CLIs ship builtin (Claude, Amp, Codex, Gemini, Mastracode, OpenCode, Pi, Copilot, Cursor Agent) and custom CLIs are user-addable via a JSON settings field.

## Feature Inventory

### 1. Agent Support

- **9 built-in agents** defined in `packages/shared/src/builtin-terminal-agents.ts`:
  - **Claude** — `claude --dangerously-skip-permissions`, argv prompt transport (default)
  - **Amp** — `amp`, stdin prompt transport
  - **Codex** (OpenAI) — `codex -c model_reasoning_effort="high" ...`, argv
  - **Gemini** (Google) — `gemini --yolo`, argv
  - **Mastracode** — `mastracode`, argv
  - **OpenCode** — `opencode`, argv (also configured as MCP client via `opencode.json`)
  - **Pi** — `pi`, argv
  - **Copilot** (GitHub) — `copilot --allow-all`, argv
  - **Cursor Agent** — `cursor-agent`, argv (limited support)
- **How agents are added**: Not a plugin system. Builtins are hardcoded; custom agents are stored as JSON rows in the local SQLite `settings.agentCustomDefinitions` field (`packages/local-db/src/schema/schema.ts`), each with `id`, `label`, `command`, `promptTransport`, and `taskPromptTemplate`. There is no runtime registration protocol — you edit settings and the new agent appears in the launch dropdown.
- **Lock-in**: Agent-agnostic. Any CLI that speaks stdin/argv works; Superset just wraps it in a PTY and watches exit codes + hooks.
- **Per-project agent overrides** via `settings.agentPresetOverrides`.

### 2. Agent Launching

- **Entry points**:
  - **Quick-create dialog** (`⌘⇧N`) — modal that takes name, agent type, initial prompt, base branch
  - **New Workspace** (`⌘N`) — full workspace form
  - **MCP tool** `start-agent-session` (`packages/mcp/src/tools/devices/start-agent-session/`) for agent-driven launches
  - **Command watcher** — file-system triggers that auto-launch
- **Launch schema** (Zod-validated, `packages/shared/src/agent-launch.ts`): discriminated union of `{kind: "terminal", terminal: {...}}` and `{kind: "chat", chat: {...}}`.
- **Required inputs**: `workspaceId`, `command` or `initialPrompt`, `agentType` (default `"claude"`). Optional: `name`, `taskPromptContent`, `taskPromptFileName`.
- **Prompt delivery**: Three mechanisms.
  - **argv** — prompt appended to command string
  - **stdin** — prompt piped (Amp-style)
  - **taskPromptFileName** — prompt written to a file inside `.superset/` and the agent is told to read it (avoids argv size limits)
- **Launch sources** tracked for telemetry: `new-workspace`, `open-in-workspace`, `workspace-init`, `command-watcher`, `mcp`, `unknown`.
- **Presets**: `terminalPresets` array in settings allows 9 keyboard-accelerated launch configurations (`⌘⇧1` through `⌘⇧9`).
- **Launch result states**: `queued`, `launching`, `running`, `failed` — returned with `tabId`, `paneId`, `sessionId` for UI routing.

### 3. Session/Agent Lifecycle

- **Pane statuses** (`apps/desktop/src/renderer/stores/tabs/types.ts`): `idle`, `working`, `permission`, `error`, `success`, `warning`.
- **Lifecycle hook events** (`apps/desktop/src/shared/notification-types.ts`): `Start`, `PermissionRequest`, `Complete`, `Failed`, `Idle`, `Working`, `Permission`.
- **Task statuses** (cloud DB, `packages/db/src/schema/schema.ts`): `backlog`, `todo`, `planning`, `working`, `needs-feedback`, `ready-to-merge`, `completed`, `canceled`.
- **Task priorities**: `urgent`, `high`, `medium`, `low`, `none`.
- **Persistence** (two stores):
  - **Cloud (Neon Postgres)** via Drizzle — organizations, users, tasks, taskStatuses, commits, PR URLs
  - **Local (SQLite)** via `packages/local-db` — projects, workspaces, worktrees, settings, layouts
- **Survives restarts**: Workspaces, worktree layout, tab/pane layout (stored as react-mosaic JSON tree in `workspaces.layout`), and terminal contents (if `settings.terminalPersistence = true`) all survive restart. **Agent processes do not auto-resume** — killed by Electron exit; user must re-launch.
- **Resume**: Workspace reopens to last layout. No native "agent --resume" wiring beyond whatever the underlying CLI supports.
- **Cleanup**: `workspaces.deletingAt` timestamp marks soft-deleted workspaces; worktrees with `createdBySuperset=true` can be removed, others are protected.

### 4. Isolation Model

- **Worktree per workspace** — the canonical isolation unit (`packages/local-db/src/schema/schema.ts`, `worktrees` table). Each workspace record holds `worktreeId`, `branch`, `baseBranch`, plus cached `gitStatus` and `githubStatus` JSON.
- **Branch naming**: Configurable `branchPrefixMode` — values include `none`, user initial, or `custom` with `branchPrefixCustom` string.
- **Port allocation**: Each workspace reserves a port range (`portBase + [0..9]`) so parallel dev servers don't collide.
- **Setup/teardown scripts**: `.superset/setup.sh` runs on workspace create (dependency install, env copy, DB migrations), `.superset/teardown.sh` on deletion. Env vars injected: `SUPERSET_WORKSPACE_NAME`, `SUPERSET_ROOT_PATH`.
- **Shared workspace**: `workspaces.type` union `"worktree" | "branch"` — "branch" type means agent works against an existing branch in the main checkout (shared-repo mode).
- **Multiple agents per workspace**: Yes — any workspace can host multiple terminal panes, each running a different agent, all inside the same worktree.
- **Merge workflow**: No built-in merge UI. Merges go through GitHub PR flow; agents invoke `gh pr create` and the user reviews/merges externally. `prUrl` tracked on the task row.
- **Sub-worktrees / nested isolation**: Not supported.

### 5. Status Detection

- **Primary method**: **Agent lifecycle hooks over Electron IPC** (`apps/desktop/src/renderer/stores/tabs/useAgentHookListener.ts`). When the agent CLI emits a hook (Claude Code's notification hook, for example), Superset receives it and updates pane status instantly.
- **Secondary method**: **Git-status polling** (`apps/desktop/src/lib/trpc/routers/changes/status.ts`) with in-memory caching + promise coalescing (45 s default timeout), refreshed on workspace focus.
- **Shell-readiness detection**: OSC 133 FinalTerm semantic-prompt markers (`packages/host-service/src/terminal/terminal.ts:51-65`) with a 15 s timeout tolerance for heavy setups like direnv/Nix. States: `pending`, `ready`, `timed_out`, `unsupported`.
- **Detected statuses**: `idle`, `working`, `permission`, `error`, `success`, `warning` (pane level); plus the 8-way task status taxonomy from section 3.
- **Latency**: Near-zero for hooks, ~poll interval for git status, 15 s max for shell-ready.
- **Cost**: Zero API calls. **No LLM-based classification** found in codebase — Superset does not send pane output to an LLM for interpretation (contrast with dmux's OpenRouter classifier).

### 6. Autonomy & Auto-Approval

- **Default**: Agents pause on permission prompts. Red pulsing badge + desktop notification + "Input Needed" alert.
- **Auto-approval**: No global auto-approve flag. Bypass is per-agent, via the agent's own flag:
  - Claude: `--dangerously-skip-permissions` (default in builtin config)
  - Gemini: `--yolo`
  - Copilot: `--allow-all`
- **Risk assessment**: Not implemented. No LLM-driven risk scoring. No soft-kill on high-risk operations.
- **Safety modes**: No Superset-level permission modes. The only "safety" is the worktree boundary — agent can't overwrite main checkout.
- **Settings > Permissions** page exists, but it's OS-level (disk access, microphone, local network), not agent-level.

### 7. Supervision & Instruction Delivery

- **Mid-session messaging**: Possible via **chat pane** (`kind: "chat"` launch) with history persisted per session, or by typing into the agent's terminal pane.
- **Standing instructions**: **Not supported** as a first-class concept. Agent presets can define default CLI arguments but there is no per-agent rules engine.
- **Heartbeat / periodic directives**: **Not supported**.
- **Supervisor agent / meta-agent**: **Not supported**. No daemon evaluates agent state and issues corrections. MCP tools let an agent invoke other agents (`start-agent-session`) but there is no hierarchical oversight.
- **Intervention history**: Chat history is logged per session; terminal input is not separately tracked as "interventions."

### 8. Cost & Budget Management

- **Token tracking**: **Not supported**. Grep for token/cost/pricing turned up nothing.
- **Cost calculation**: **Not supported**.
- **Per-agent budgets**: **Not supported**.
- **Budget enforcement**: **Not supported**.
- **Cost display**: **Not supported**.
- Provider credentials (Anthropic, OpenAI keys) are stored via Better Auth but usage is not metered.
- Usage telemetry flows to PostHog (`NEXT_PUBLIC_POSTHOG_KEY`) and Sentry for errors; neither exposes cost back to the user.

### 9. Agent Hierarchy & Coordination

- **Parent/child relationships**: **Not supported**.
- **Agent-to-agent communication**: **Not supported** directly. Agents can invoke `start-agent-session` via MCP to spawn peers, but there is no structured parent-child tree, no follow mode, no cascade operations.
- **Task decomposition**: Manual. Humans (or agents via MCP `create-task` tool in `packages/mcp/src/tools/tasks/`) create tasks; no AI-driven decomposition pipeline.
- **Cascade kill / budget**: **Not supported** (no hierarchy, no budgets).
- **Follow / oversight modes**: **Not supported**.

### 10. TUI / UI

- **Interface type**: **Electron desktop GUI** (primary), with a Next.js web companion (`apps/web` at `app.superset.sh`) and React Native/Expo mobile app (`apps/mobile`).
- **Framework stack**: Electron 40.8.5 + electron-vite + React + TailwindCSS v4 + shadcn/ui. State via Zustand. Server calls via tRPC.
- **Layout model**: **Flexible Mosaic (`react-mosaic-component`)** — arbitrary horizontal/vertical splits, draggable resizing, per-pane types. Tab bar on top for switching layouts. Left sidebar lists workspaces (`⌘B` toggles). Right panel shows git changes (`⌘L` toggles).
- **Five pane types** (`apps/desktop/src/renderer/stores/tabs/types.ts`, `PaneType`):
  - `terminal` — xterm/node-pty PTY
  - `chat` — Claude Harness chat UI
  - `file-viewer` — modes: `raw`, `diff`, `rendered` (side-by-side + inline diff)
  - `browser` — WebView for dev-server preview
  - `devtools` — Chromium DevTools paired with the browser pane
- **Status indicators**: Red pulsing badge (permission), spinner (working), green check (success), red X (failed), gray dot (idle) on pane headers + sidebar.
- **Full keyboard-shortcut list** (from `apps/docs/content/docs/keyboard-shortcuts.mdx` and `apps/desktop/src/renderer/hotkeys/`):

| Category | macOS | Action |
|---|---|---|
| Workspace | `⌘N` | New workspace |
| Workspace | `⌘⇧N` | Quick-create workspace |
| Workspace | `⌘⇧O` | Open project |
| Workspace | `⌘1`–`⌘9` | Switch to workspace 1–9 |
| Workspace | `⌘⌥↑` / `⌘⌥↓` | Previous / next workspace |
| Terminal | `⌘T` | New tab |
| Terminal | `⌘W` | Close pane |
| Terminal | `⌘D` | Split right |
| Terminal | `⌘⇧D` | Split down |
| Terminal | `⌘E` | Split pane (auto) |
| Terminal | `⌘K` | Clear terminal |
| Terminal | `⌘F` | Find in terminal/chat |
| Terminal | `⌘⇧↓` | Scroll to bottom |
| Terminal | `⌘⌥←` / `⌘⌥→` | Previous / next tab |
| Terminal | `⌘⇧←` / `⌘⇧→` | Previous / next pane |
| Terminal | `⌘⇧1`–`⌘⇧9` | Launch preset 1–9 |
| Layout | `⌘B` | Toggle workspaces sidebar |
| Layout | `⌘L` | Toggle changes panel |
| Layout | `⌘⇧L` | Toggle expand sidebar |
| Layout | `⌘P` | Quick-open file |
| Layout | `⌘⇧F` | Keyword search |
| Layout | `⌘⇧B` | New browser tab |
| Window | `⌘O` | Open in external app |
| Window | `⌘⇧C` | Copy path |
| Window | `⌘/` | Show keyboard shortcuts |
| Window | `⌘⇧Q` | Close window |
| Chat | `⌘J` | Focus chat input |

- **Customization**: Keyboard shortcuts re-bindable via Settings → Keyboard Shortcuts (stored in `useHotkeyOverridesStore` Zustand slice; export/import to JSON). Terminal presets editable. Theme follows system (light/dark).
- **No configurable columns** (not a dashboard); no sort order; no timeline view — these are Overcode-specific concepts.

### 11. Terminal Multiplexer Integration

- **No tmux/zellij/screen**. Each pane is a **direct `node-pty` PTY** (`packages/host-service/src/terminal/terminal.ts`), rendered via `@xterm/xterm` and `@xterm/headless` + `@xterm/webgl` addons. Term type `xterm-256color`.
- **Pane management**: Superset's own `react-mosaic`-based Mosaic tree; no external multiplexer involved.
- **Layout calculation**: Mosaic does it — you drag splits and it recomputes flex.
- **Live output**: Yes — xterm renders PTY output in real time over a WebSocket binary stream with 64 KB buffers, with replay on reconnect.
- **Split/zoom/focus**: Split via `⌘D`/`⌘⇧D`/`⌘E`; focus via `⌘⇧←/→`; no first-class "zoom" (but closing siblings effectively zooms).
- **Shell detection**: Auto-detects bash/zsh/fish/sh/ksh.

### 12. Configuration

- **Per-project config**: `.superset/config.json` with `setup` and `teardown` arrays of shell-script paths.
- **Per-project agent MCP config**: `opencode.json` — declares MCP servers (local or remote) with `type`, `url`, `command`.
- **User settings**: SQLite `settings` table (`packages/local-db/src/schema/schema.ts`) — fields include:
  - `lastActiveWorkspaceId`
  - `terminalPresets` (JSON array)
  - `agentPresetOverrides` (JSON)
  - `agentCustomDefinitions` (JSON)
  - `selectedRingtoneId`
  - `confirmOnQuit`
  - `terminalLinkBehavior`
  - `notificationSoundsMuted`
  - `notificationVolume`
  - `terminalPersistence`
  - `branchPrefixMode`, `branchPrefixCustom`
- **Environment variables** (from `.env.example`): `DATABASE_URL`, `NEON_ORG_ID`, `NEON_API_KEY`, `BETTER_AUTH_SECRET`, `GOOGLE_CLIENT_ID`, `GH_CLIENT_ID`, `SUPERSET_MCP_API_KEY`, `POSTHOG_API_KEY`, `NEXT_PUBLIC_POSTHOG_KEY`, `BLOB_READ_WRITE_TOKEN`, `RESEND_API_KEY`, `STRIPE_SECRET_KEY`, `NEXT_PUBLIC_SENTRY_DSN_*`, `NEXT_PUBLIC_WEB_URL`, `SKIP_ENV_VALIDATION` (dev only), plus implicit `TERM=xterm-256color`.
- **Lifecycle hooks**: `.superset/setup.sh` (workspace create) and `.superset/teardown.sh` (workspace delete) — shell scripts, not a structured event bus.

### 13. Web Dashboard / Remote Access

- **Web UI**: Yes — `apps/web` is a Next.js 16 app at `app.superset.sh` with Better Auth login; `apps/admin` is a team/org management dashboard.
- **API**: `apps/api` — Hono + tRPC server (port 3001), plus MCP endpoint at `/api/agent/mcp`.
- **Remote monitoring**: **Partially**. The web UI shows team/workspace/task state but **agents still execute locally** on the user's desktop — no cloud agent runners. `apps/relay` is a WebSocket relay used for cross-machine comms (e.g., mobile → desktop).
- **Mobile**: `apps/mobile` — Expo/React Native, primarily for viewing and triggering tasks.
- **Real-time sync**: Electric SQL via `apps/electric-proxy`; Caddy reverse-proxy required locally (`Caddyfile.example`).

### 14. Git / VCS Integration

- **Branch management**: Every workspace owns a branch; prefix configurable; base branch tracked.
- **Commit automation**: Not automated by Superset itself — agents run `git commit`. Superset displays commit list in the changes panel (`apps/desktop/src/shared/changes-types.ts` — `GitChangesStatus` with `commits`, `staged`, `unstaged`, `untracked`, `ahead`, `behind`, `pushCount`, `pullCount`, `hasUpstream`).
- **PR creation**: Agents use `gh pr create`; `prUrl` stored on the task row. No Superset-native PR button found.
- **Merge conflict resolution**: Diff viewer supports inline editing; no dedicated conflict UI (no "accept ours/theirs" buttons).
- **GitHub integration**: Status checks and PR metadata cached per worktree (`worktrees.githubStatus` JSON). CodeRabbit / status check widgets visible in UI. `gh` CLI is the preferred integration per `AGENTS.md`.

### 15. Notifications & Attention

- **Desktop notifications**: Yes — native OS alerts via Electron `Notification` API (`apps/desktop/src/main/lib/notifications/notification-manager.ts`).
- **Sound**: Configurable ringtone (`selectedRingtoneId`), volume (`notificationVolume`), mute (`notificationSoundsMuted`), upload custom ringtone. Default system sound.
- **Visual**: Red pulsing badge on pane header + sidebar entry, tab bar dot.
- **Click routing**: Notification click focuses the originating pane.
- **TTL**: 10-minute notification timeout, 5-minute sweeper (`apps/desktop/src/main/lib/notifications/notification-manager.ts`).
- **Attention prioritization**: No explicit priority queue — permission requests bubble visually but there is no "most urgent agent" ranking.

### 16. Data & Analytics

- **Session history**: Task history via Postgres; workspace/tab layout via SQLite. No structured "session archive" export.
- **Export formats**: None built in. SQLite file is directly accessible on disk; Postgres via API.
- **Metrics dashboards**: PostHog (external) for usage analytics; Sentry for errors. No in-app analytics.
- **Presence**: Electric SQL sync implies multi-device awareness but a dedicated presence UI wasn't located.

### 17. Extensibility

- **MCP support**: First-class. Superset *is* an MCP server at `/api/agent/mcp`, and it consumes MCP servers (Neon, Linear, Sentry, PostHog, Expo, Maestro, desktop-automation) declared in `opencode.json`. Tools live in `packages/mcp/src/tools/` across subfolders `tasks/`, `devices/`, `organizations/` (~41 tool definitions).
- **Custom agents**: JSON definition in `settings.agentCustomDefinitions`.
- **Plugin system**: None — no npm-based extension loader, no Electron extension API surface, no shell-out hooks beyond setup/teardown.
- **API for external tools**: tRPC (typed), plus the MCP endpoint.

### 18. Developer Experience

- **Install**:
  ```
  git clone https://github.com/superset-sh/superset
  cp .env.example .env             # or SKIP_ENV_VALIDATION=1
  brew install caddy
  cp Caddyfile.example Caddyfile
  bun install
  bun run dev
  ```
- **First-run**: Opens to a blank "create project" screen; user picks a repo directory; Superset inits the SQLite DB on first run.
- **Docs**: `apps/docs` (Fumadocs) with keyboard-shortcuts page, setup guide, MCP docs.
- **Testing**: Vitest co-located (`*.test.ts`/`*.test.tsx`). Examples: `packages/shared/src/agent-launch.test.ts`, `apps/desktop/src/renderer/hotkeys/utils/resolveHotkeyFromEvent.test.ts`, `apps/desktop/src/renderer/stores/tabs/actions/move-pane.test.ts`. Coverage not reported in CI config surface-checked.
- **Lint/format**: Biome 2.4.2 (`bun run lint:fix`, `bun run format:check`).
- **Release**: `bun run release:desktop` (signs + builds macOS `.app`), `bun run release:canary`.

## Unique / Notable Features

1. **Pane-type polyglot layout** — Terminal + chat + diff/raw/rendered file viewer + real WebView browser + DevTools side-by-side in a drag-resizable Mosaic. The browser pane means you can preview a dev server in the same window as the agent that's building it, with real DevTools attached.
2. **OSC 133 shell-readiness gating** (`packages/host-service/src/terminal/terminal.ts:51-65`) — waits for FinalTerm prompt markers up to 15 s before injecting the initial prompt, so agents don't race slow direnv/Nix setups.
3. **Per-workspace port allocation** (`portBase + [0..9]`) — lets parallel dev servers coexist without `EADDRINUSE` collisions. Genuinely useful for "10 agents rebuilding the frontend simultaneously."
4. **Three prompt-transport modes** (argv / stdin / file-path-in-taskPromptFileName) — solves the "prompt is too big for argv" problem cleanly by writing to `.superset/<name>.txt` and telling the agent to read it.
5. **Electric SQL durable sessions + Caddy proxy** — real-time sync across devices (desktop ↔ mobile ↔ web) via CRDT replication, not polling.
6. **`createdBySuperset` flag on worktrees** — explicit guard so cleanup never nukes a user-managed worktree. Small detail, big safety win.
7. **Setup/teardown shell hooks per project** (`.superset/setup.sh`, `.superset/teardown.sh`) — trivially scriptable environment provisioning.
8. **9 builtin agent integrations** out of the box with three different prompt-delivery semantics — the broadest support I've seen in a single tool.
9. **Agent-invokable MCP tool surface** (~41 tools in `packages/mcp/src/tools/`) including `start-agent-session`, `create-task`, `update-task` — lets the agent itself spawn sibling workspaces or file issues.
10. **Mobile companion app** (`apps/mobile`) — genuinely rare; lets you trigger/monitor from a phone.

## Strengths Relative to Overcode

- **GUI diff viewer, syntax highlighting, browser preview, DevTools** — Overcode's TUI can't match any of these. For frontend work especially, the Superset browser pane + DevTools is a killer feature.
- **Worktree isolation** is first-class, with port allocation, branch prefixes, setup/teardown hooks, and `createdBySuperset` safety. Overcode has nothing here.
- **Multi-agent support**: 9 builtin CLIs vs. Overcode's Claude-only posture.
- **Prompt delivery via file** (`taskPromptFileName`) — sidesteps argv limits that Overcode's `tmux send-keys` approach can hit for huge prompts.
- **Shell-readiness via OSC 133** — Overcode's status detection doesn't have this primitive; we could plausibly use it to stop sending input before a shell is live.
- **Per-workspace port allocation** — concrete problem-solver for parallel dev servers.
- **MCP server surface** — agents can call `start-agent-session`, `create-task`, etc. Overcode has an HTTP API but no MCP-native surface that agents can use reflexively.
- **Native desktop notifications with configurable ringtone + volume + mute + TTL**. Overcode has no native notifications.
- **Mobile app** for remote triggering.
- **Rebindable keyboard shortcuts** with export/import. Overcode's keybindings are code-defined.
- **Electric SQL real-time sync** — more robust than Overcode's Sister pull-based cross-machine story.

## Overcode's Relative Strengths

- **Supervisor daemon** (Claude-powered, with 25 standing-instruction presets) — Superset has nothing analogous. The entire supervision layer is absent.
- **Heartbeat system** for periodic instruction delivery — Superset doesn't issue directives to idle agents.
- **Per-agent cost budgets and token tracking** — Superset has zero cost tracking. For teams running dozens of agents, this matters.
- **Agent hierarchy** (parent/child, 5 levels deep, cascade kill, fork-with-context) — Superset is flat.
- **Dual status detection** (442-pattern regex polling + Claude Code hooks) with a richer status taxonomy (running, waiting_user, waiting_approval, error, etc.) — Superset detects fewer distinct states and relies more on hooks alone.
- **Claude Code-deep integration**: session resume, context-window tracking, /compact awareness, plan mode, side-question subagent attribution. Superset treats Claude as just another PTY.
- **Parquet export + analytics dashboard** — Superset has no equivalent local-analytics story; it offloads to PostHog.
- **Timeline view + configurable columns** — Overcode's dashboard ergonomics for supervising many agents beats Superset's IDE-centric layout for pure monitoring.
- **Terminal-native form factor** — runs over SSH, no X server needed, no Electron 300 MB RAM footprint.
- **Sister integration** for cross-machine monitoring without a cloud dependency (Superset requires Neon + Caddy + Electric SQL).
- **Fork with context** — copies conversation state into a new agent; Superset can't do this.
- **Cascade kill / cascade budget** — agent-tree operations. Superset has no tree.

## Adoption Candidates

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **OSC 133 shell-readiness detection** | High | Low | Scan agent pane for FinalTerm prompt markers before sending initial prompt / heartbeat. Directly addresses race conditions with slow shell init (direnv, mise, nix-shell). Could be a status-detector primitive or a launch-time gate. `packages/host-service/src/terminal/terminal.ts:51-65` is the reference. |
| **Prompt delivery via file** (`taskPromptFileName` pattern) | High | Low | For prompts above ~4 KB, write to a tempfile and tell Claude to read it instead of `tmux send-keys`. Avoids argv/paste-buffer limits. Already partially relevant to standing-instruction heartbeats with large bodies. |
| **Per-workspace port allocation** | Med | Low | When/if Overcode adopts worktree mode, pre-allocate `PORT_BASE + [0..9]` env vars per agent so dev servers don't collide. Mechanically simple; hugely useful for frontend-heavy repos. |
| **Native desktop notifications with ringtone + volume + mute + TTL** | High | Med | Overcode has no native notifications. Superset's model (sound, click-to-focus, 10-minute TTL, 5-minute sweeper) is a good blueprint. Could use `plyer` or `desktop-notifier` Python libs. |
| **Browser pane with DevTools** | Med | High | For frontend agent work, embedding a WebView preview (via `textual-web` or spawning an external Chromium with CDP hook) would be transformative — but is a major scope expansion for a TUI. Probably belongs in the web dashboard instead. |
| **Rebindable keyboard shortcuts with JSON export/import** | Med | Med | Overcode's keybindings are hardcoded in `tui_actions/`. Pulling them into a JSON settings file with export/import would make power users happier and support team-shared configs. |
| **Setup/teardown hook scripts per project** (`.overcode/setup.sh`, `.overcode/teardown.sh`) | Med | Low | Structured place for env bootstrapping (install deps, copy `.env`, seed DB). Better than documenting "don't forget to X" in CLAUDE.md. Useful once worktree mode lands. |
| **Worktree safety flag** (`createdBySuperset`) | Med | Low | If/when Overcode supports worktrees, mark ones it created so `sync-to-main` / cleanup never nukes user-managed worktrees. Cheap insurance. |
| **MCP server surface for Overcode** (`start-agent`, `create-task`, `fork-agent`, etc.) | High | Med | Let agents reflexively spawn/fork siblings, record findings as tasks, or update their own budget via MCP calls. Aligns with Claude Code's native MCP integration and opens supervisor-style workflows to agents themselves. |
| **Three prompt-transport modes** (argv / stdin / file) per agent type | Low | Low | Formalize prompt delivery as a strategy so future non-Claude support (even just for sub-agents) is pluggable. |
| **Configurable branch prefix modes** (`none` / user-initial / custom) | Low | Low | Small UX polish if Overcode ever auto-creates branches. |
| **Real-time sync via CRDT** (Electric SQL equivalent) | Med | High | Upgrade Sister from pull-based to push-based using a CRDT replication layer. Large lift — consider only if multi-machine becomes a headline feature. |
| **Mobile companion for remote triggering** | Low | High | Rare but not core. Web dashboard is probably the better investment. |
| **Chat pane as a first-class mid-session communication channel** | Med | Med | Today Overcode delivers instructions via `tmux send-keys`. A structured chat pane with history, model selection, and draft-and-send semantics would be cleaner than raw keystroke injection for multi-paragraph directives. |
