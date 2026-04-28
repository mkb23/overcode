# Overcode vs cmux: Feature Bakeoff

## Overview

| | **cmux** | **Overcode** |
|---|---|---|
| **Repo** | [manaflow-ai/cmux](https://github.com/manaflow-ai/cmux) | This project |
| **Language** | Swift / AppKit (+ Zig for Ghostty, TS for web) | Python (Textual TUI) |
| **Stars** | ~7,700 (per candidates.md) | N/A (private) |
| **License** | GPL-3.0-or-later (LICENSE) | Proprietary |
| **First Commit** | Shallow clone; project dates to 2024 per LICENSE `Copyright (c) 2024-present Manaflow, Inc.` | 2025 |
| **Last Commit** | 2026-04-14 `c5f2e8c` | Active |
| **Purpose** | Native macOS Ghostty-based terminal with vertical tabs, scriptable in-app browser, and notifications for AI coding agents | Claude Code supervisor/monitor with instruction delivery |

## Core Philosophy

cmux is explicitly positioned as a **primitive, not a solution** (see `README.md:131-139` "The Zen of cmux"). It is a native macOS terminal app built on libghostty with an AppKit sidebar, a scriptable in-app browser (ported from vercel-labs/agent-browser), a unified socket/CLI control surface, and a notification system that picks up OSC 9/99/777 from terminal programs. cmux does not orchestrate agents, track cost, or decide what agents do — it gives you fast panes, splits, browser surfaces, notification rings, and a CLI, and the user assembles their own workflow on top.

The mental model is **workspaces → surfaces → panes**. A workspace is a named container with a CWD, color, optional git metadata, and a bonsplit layout tree. Surfaces are either `terminal` or `browser`. Panes can stack multiple surfaces (tabs inside a pane). The sidebar is a "vertical tabs" rail that shows git branch, linked PR status, working directory, listening ports, and the latest notification body per workspace (`README.md:58-66`, `Sources/Workspace.swift`).

Overcode, by contrast, is **supervision-first, Claude-Code-only**. It runs Claude Code agents in tmux, classifies their state via 442 regex patterns and Claude Code hooks, delivers standing instructions and heartbeats, enforces per-agent cost budgets, and organizes agents into a 5-level parent/child tree. Where cmux stops at "I gave you primitives, go build," Overcode ships the opinionated orchestrator on top.

## Feature Inventory

### 1. Agent Support

- **Which AI CLI agents are supported?** cmux ships **first-class hooks for 7+ agents**: Claude Code, Codex, Cursor, Gemini, GitHub Copilot, CodeBuddy, Factory, Qoder. Each has an `install-hooks`/`uninstall-hooks` subcommand plus a matching hook-receiver subcommand (`claude-hook`, `codex-hook`, `cursor-hook`, `gemini-hook`, `copilot-hook`, `codebuddy-hook`, `factory-hook`, `qoder-hook` in `CLI/cmux.swift:2706-2750`, installers at `CLI/cmux.swift:1806-1892`). The `cmux claude-teams` command (`CLI/cmux.swift:1773`) natively runs Claude Code's teammate mode with splits + sidebar metadata + notifications — **no tmux required** (`README.md:77-84`).
- **How are new agents added?** Hardcoded per-agent installer/handler pairs in the Swift CLI; there is no plugin registry. Adding a new agent means patching the CLI.
- **Locked to one agent?** Agent-agnostic — cmux treats agents as terminal programs that emit OSC 9/99/777 or call `cmux notify`/`cmux set-status` via the socket.

**Overcode:** Claude Code only. Deeply integrated with Claude's hooks, JSON session logs, and pricing.

### 2. Agent Launching

- **How are agents created?** Workspaces are created via `cmux new-workspace` (`CLI/cmux.swift:2104`) with flags `--command`, `--cwd`, `--name`, `--description`. Agents are normal shell commands inside a terminal surface; cmux doesn't have a concept of "an agent" as a first-class object — it has surfaces that happen to run agent CLIs. Layout presets declared in `cmux.json` (`Sources/CmuxConfig.swift`) can declare multi-pane/browser layouts that launch specific commands per surface.
- **Inputs required:** none beyond the shell command; a `cmux.json` command definition can pin name, cwd, color, full split layout, and per-surface env (`Sources/CmuxConfig.swift:244-252`).
- **Launch with pre-written prompt?** Yes — pass the prompt as part of `--command`, or use `cmux send --surface <id> <text>` / `cmux send-key` (`CLI/cmux.swift:2486-2535`) to deliver text/keys to an existing surface after launch.
- **Prompt delivery mechanism:** Socket RPC → `input` command to the ghostty pty (analogous to tmux send-keys). Separate `send-panel`/`send-key-panel` variants address by panel id.
- **Templates/presets?** Yes. `cmux.json` supports named "custom commands" (`Sources/CmuxConfig.swift:9-85`) that appear in the Cmd+Shift+P command palette. A command can either be a plain shell command OR a full workspace definition with a nested `layout` tree of splits/panes/surfaces (terminal or browser), each with its own cwd, env, url, focus flag, and name (`Sources/CmuxConfig.swift:93-252`). `restart` behavior is `recreate | ignore | confirm`.

**Overcode:** agents are launched via the TUI (`n` hotkey) or `overcode launch` CLI with a prompt, model (haiku/sonnet/opus), permission mode, and optional parent. Prompts are delivered via tmux send-keys. No equivalent of cmux's declarative layout-as-config.

### 3. Session/Agent Lifecycle

- **States an agent can be in:** cmux itself doesn't model agent states. It models *surface* states and *notification* states. Per `Sources/TerminalNotificationStore.swift` and `Sources/Workspace.swift`, the visible per-surface/workspace signals are: **has unread notification**, **has status entry** (any `key=value` set via `cmux set-status`), **has progress** (`set-progress`), **listening ports detected**, **git branch dirty/clean**, **linked PR state (open/merged/closed)**, and **notification ring active**. There are no explicit enum values like "running/waiting/error" — agents report their own state via OSC sequences and `cmux set-status <key> <value>`.
- **How are sessions persisted?** cmux autosaves layout + metadata snapshots every 8 seconds (`Sources/SessionPersistence.swift` — `autosaveInterval = 8.0`). Snapshots include window/workspace/pane layout, working directories, scrollback (best-effort, capped at 4000 lines / 400k chars per terminal), browser URL & navigation history, sidebar visibility/selection/width.
- **Survive restarts?** App restart: yes (auto-restore unless `CMUX_DISABLE_SESSION_RESTORE=1` or under test). **Live process state is NOT restored** — active Claude Code / tmux / vim sessions die on relaunch (`README.md:239-247`).
- **Resume/reattach?** For SSH workspaces, explicit session attach/detach RPCs exist (`session.open`, `session.attach`, `session.resize`, `session.detach`, `session.status`, `session.close` in `docs/remote-daemon-spec.md`). Local terminals cannot reattach across app restart.
- **Cleanup on close/kill:** `cmux close-workspace`, `cmux close-surface`, `cmux close-window` (`CLI/cmux.swift:2030, 2244, 2402`). There's no agent-specific cleanup because there's no agent object.

**Overcode:** persistent `Session` dataclass serialized to disk per-agent; session lists survive TUI restart and machine reboot; `overcode resume` reattaches to a Claude Code session by id. Rich status taxonomy (10+ states: running, waiting_user, waiting_approval, idle, error, done, sleeping, terminated, etc.).

### 4. Isolation Model

- **How are agents isolated?** **They aren't.** cmux is a terminal; all surfaces in a workspace share the same filesystem and (by default) the same cwd. There is no worktree creation, no containerization, no branch-per-agent.
- **Branch management:** cmux *reads* git branch state per workspace/panel and displays it in the sidebar with linked PR metadata (`Sources/Workspace.swift` fields `gitBranch`, `pullRequest`, `panelGitBranches`, `panelPullRequests`). It does not create, switch, or merge branches.
- **Multiple agents share a workspace?** Yes, trivially — they share the cwd and filesystem. Multiple panes in one workspace is the canonical use case (e.g., `claude-teams` splits).
- **Merge workflow:** Not supported.
- **Sub-task / sub-worktree support:** Not supported.

**Overcode:** also no worktree isolation (agents share the repo). Both tools trail dmux and Claude Squad here.

### 5. Status Detection

- **How does cmux know what an agent is doing?** Agents actively *tell* cmux via terminal escape sequences or the CLI. Three channels:
  1. **OSC 9 / 99 / 777 sequences** parsed from the terminal stream → routed to `TerminalNotificationStore` (`Sources/TerminalNotificationStore.swift`). OSC 9 is the classic "notify" sequence.
  2. **`cmux notify` CLI** (`CLI/cmux.swift:2553`) sends title/subtitle/body + target workspace/surface/tab/panel over the socket → creates a notification, rings the pane, lights the sidebar tab.
  3. **`cmux set-status <key> <value>`, `cmux set-progress`, `cmux log`** (`CLI/cmux.swift:2625-2688`) populate sidebar metadata blocks and progress bars.
- **Agent hook installers:** `cmux setup-hooks` (Claude) and the per-agent equivalents write hook configs into each agent's config file (`CLI/cmux.swift:1806-1892`). The hooks typically invoke `cmux <agent>-hook` which bridges agent events to the socket.
- **Polling? LLM analysis?** **Neither.** Detection is push-only from the agent side. `PortScanner.swift` does run active `ps`/`lsof` polling (coalesced 200ms, burst offsets `[0.5, 1.5, 3, 5, 7.5, 10]s`) but that's for listening-port detection, not agent state.
- **Statuses detected:** there is no fixed taxonomy. Each agent can `set-status <key> <value>` for any key. Known conventional keys include progress (via `set-progress`), arbitrary log lines (`log`), and a single free-form "latest notification text" per workspace displayed in the sidebar.
- **Latency:** near-zero — push from the agent; the OSC bytes are consumed in the ghostty terminal pipeline and the socket commands are dispatched off-main (`CLAUDE.md:174-186`).
- **Cost of detection:** free (no LLM calls). PostHog analytics capture `cmux_daily_active`/`cmux_hourly_active` only (`Sources/PostHogAnalytics.swift:14-15`), not agent activity.

**Overcode:** regex polling (442 patterns) + Claude Code hooks for authoritative instant transitions; richer status enum but requires Claude-specific log-file parsing.

### 6. Autonomy & Auto-Approval

- **Can agents run unattended?** cmux doesn't intervene — whatever autonomy your agent has (Claude's `--dangerously-skip-permissions`, Codex's YOLO mode, etc.) is what you get. cmux just watches and notifies.
- **Auto-accept / auto-approve:** Not supported at the cmux layer.
- **Risk assessment:** Not supported.
- **Permission/safety modes:** Not supported (delegated to the underlying agent).

**Overcode:** supervisor daemon (Claude-powered) can auto-approve with standing instructions; per-agent permission modes (normal / permissive / bypass).

### 7. Supervision & Instruction Delivery

- **Send instructions to running agents?** Yes — `cmux send`, `cmux send-key`, `cmux send-panel`, `cmux send-key-panel` (`CLI/cmux.swift:2486-2535`). This is raw keystroke delivery; no awareness of what the agent is doing.
- **Standing instructions / persistent directives:** Not supported.
- **Heartbeat / periodic delivery:** Not supported.
- **Supervisor daemon / meta-agent:** Not supported.
- **Intervention history / logging:** `cmux log` / `cmux list-log` / `cmux clear-log` (`CLI/cmux.swift:2670-2688`) gives a per-workspace log stream that scripts can populate, but it's a user-authored log, not an automatic intervention trail.

**Overcode wins decisively here.** Standing instructions (25 presets), heartbeat, supervisor daemon, intervention history, oversight timeouts are Overcode's signature layer and have no cmux equivalent.

### 8. Cost & Budget Management

- Token tracking: **Not supported.**
- Cost calculation: **Not supported.**
- Per-agent budgets: **Not supported.**
- Budget enforcement: **Not supported.**
- Cost display: **Not supported.**

cmux has zero visibility into agent token/dollar consumption. Overcode tracks tokens, dollars (haiku/sonnet/opus pricing), and joules per agent with soft-enforced budgets and budget transfer between agents.

### 9. Agent Hierarchy & Coordination

- **Parent/child relationships:** Not modelled. Workspaces and panes are flat peers; there's no "agent A spawned agent B" edge.
- **Agent-to-agent comms:** Indirect only — agents can call each other via the CLI/socket (one agent can `cmux send --surface other` to another).
- **Task decomposition:** Not supported in cmux. `claude-teams` leans on Claude Code's built-in teammate mode for decomposition (`README.md:77-84`).
- **Cascade operations (kill, budget):** Not supported.
- **Follow / oversight modes:** Not supported. (The browser has a "follow" semantic for tabs but that's unrelated.)

**Overcode:** 5-level parent/child trees, cascade kill, budget inheritance, fork-with-context, follow mode with stuck detection, oversight timeout.

### 10. TUI / UI

- **Interface type:** Native macOS GUI (AppKit + SwiftUI), not a TUI. Ghostty-based terminal rendering (libghostty / zig) with GPU acceleration.
- **Framework:** Swift / AppKit for shell, SwiftUI for panels, libghostty for terminal, WKWebView for browser, Bonsplit (vendored) for split-pane layout.
- **Layout model:** Window → vertical-tabs sidebar (toggleable, 180–600px wide, `Sources/SessionPersistence.swift:9-26`) → workspace → bonsplit tree of panes → each pane holds one or more surfaces (terminal or browser).
- **Key UI features:**
  - Vertical tabs sidebar with per-workspace metadata: git branch, linked PR status/number, cwd, listening ports, latest notification text (`README.md:58-66`, `Sources/Workspace.swift` sidebar fields).
  - Horizontal + vertical splits with Cmd+D / Cmd+Shift+D.
  - Browser panes with a fully scriptable WKWebView (ported from vercel-labs/agent-browser).
  - Notification ring (blue outline on panes awaiting attention) + sidebar tab badge + system NSUserNotification + custom sounds (17 system sounds incl. Basso/Blow/Bottle/Frog/Funk/Glass/Hero/Morse/Ping/Pop/Purr/Sosumi/Submarine/Tink, `Sources/TerminalNotificationStore.swift:90-108`).
  - Notifications panel (Cmd+I) showing all pending notifications; "jump to latest unread" (Cmd+Shift+U) — the README explicitly calls this out as the attention killer-feature.
  - File Explorer panel (`Sources/FileExplorerStore.swift`, `Sources/FileExplorerView.swift`).
  - Find bar with Cmd+F / Cmd+G / Cmd+Shift+F, "use selection for find" (Cmd+E).
  - Debug menu in DEBUG builds only (`CLAUDE.md:144-150`).
  - Ghostty config compatibility — reads `~/.config/ghostty/config` for themes/fonts/colors (`README.md:91`).
- **Keyboard shortcuts (complete list from `Sources/KeyboardShortcutSettings.swift:17-276`, all rebindable via `~/.config/cmux/settings.json`):**

  **App/Window:** `openSettings` ⌘, · `reloadConfiguration` ⌘⇧, · `showHideAllWindows` ⌃⌥⌘. · `newWindow` ⌘⇧N · `closeWindow` ⌃⌘W · `toggleFullScreen` ⌃⌘F · `quit` ⌘Q

  **Titlebar/UI:** `toggleSidebar` ⌘B · `newTab` ⌘N · `openFolder` ⌘O · `goToWorkspace` ⌘P · `commandPalette` ⌘⇧P · `sendFeedback` ⌘⌥F · `showNotifications` ⌘I · `jumpToUnread` ⌘⇧U · `triggerFlash` ⌘⇧H

  **Navigation:** `nextSidebarTab` ⌃⌘] · `prevSidebarTab` ⌃⌘[ · `renameTab` ⌘R · `renameWorkspace` ⌘⇧R · `editWorkspaceDescription` ⌘⇧E · `closeTab` ⌘W · `closeOtherTabsInPane` ⌘⌥T · `closeWorkspace` ⌘⇧W · `reopenClosedBrowserPanel` ⌘⇧T · `nextSurface` ⌘⇧] · `prevSurface` ⌘⇧[ · `selectSurfaceByNumber` ⌃1–9 · `selectWorkspaceByNumber` ⌘1–9 · `newSurface` ⌘T · `toggleTerminalCopyMode` ⌘⇧M

  **Pane focus/splits:** `focusLeft` ⌘⌥← · `focusRight` ⌘⌥→ · `focusUp` ⌘⌥↑ · `focusDown` ⌘⌥↓ · `splitRight` ⌘D · `splitDown` ⌘⇧D · `toggleSplitZoom` ⌘⇧↩ · `splitBrowserRight` ⌘⌥D · `splitBrowserDown` ⌘⇧⌥D

  **File explorer:** `toggleFileExplorer` ⌘⌥B

  **Browser:** `openBrowser` ⌘⇧L · `focusBrowserAddressBar` ⌘L · `browserBack` ⌘[ · `browserForward` ⌘] · `browserReload` ⌘R · `browserZoomIn` ⌘= · `browserZoomOut` ⌘- · `browserZoomReset` ⌘0 · `toggleBrowserDeveloperTools` ⌘⌥I · `showBrowserJavaScriptConsole` ⌘⌥C · `toggleReactGrab` ⌘⇧G

  **Find:** `find` ⌘F · `findNext` ⌘G · `findPrevious` ⌘⌥G · `hideFind` ⌘⇧F · `useSelectionForFind` ⌘E

  **Terminal (from README.md:213-219):** `⌘K` clear scrollback · `⌘C` copy · `⌘V` paste · `⌘+ / ⌘-` font size · `⌘0` reset font

- **Customization:** Shortcuts are fully rebindable via Settings UI or `~/.config/cmux/settings.json` (`Sources/KeyboardShortcutSettingsFileStore.swift`). Colors per workspace via `customColor` hex. Themes/fonts inherit from Ghostty config.

**Overcode:** full-screen Textual dashboard, ~50+ keybindings, configurable columns, 4 sort modes, timeline view with color-coded status history bars, command bar with history. Much richer *information density* than cmux's sidebar; much worse *visual fidelity* than a native AppKit app.

### 11. Terminal Multiplexer Integration

- **Which multiplexer?** **None — cmux replaces it.** Terminals are native ghostty (libghostty + AppKit portal) directly in the app; there is no tmux/zellij/screen underneath. The `claude-teams` feature specifically advertises "No tmux required" (`README.md:82`).
- **Panes/windows managed:** By Bonsplit (vendored at `vendor/bonsplit/`). Each pane is an AppKit view hosting a ghostty surface or a WKWebView browser surface.
- **Layout calculation:** Bonsplit's tree of horizontal/vertical splits with a `split` ratio (0.1–0.9 clamped, `Sources/CmuxConfig.swift:205-208`).
- **Live agent output:** Yes — it's a real terminal; you watch it directly.
- **Split/zoom/focus:** `toggleSplitZoom` ⌘⇧↩ zooms one pane to fill; directional focus ⌘⌥arrows; `drag-surface-to-split`, `move-surface`, `reorder-surface` CLI commands (`CLI/cmux.swift:2052-2264`).
- **tmux compatibility:** there is a hidden `cmux __tmux-compat` and `capture-pane` subcommand (`CLI/cmux.swift:2774-2783`) to make cmux look enough like tmux to satisfy agents that shell out to `tmux capture-pane`.

**Overcode:** runs inside tmux; agents are tmux windows. cmux owns the whole terminal stack natively.

### 12. Configuration

- **Config files:**
  - `~/.config/cmux/cmux.json` — global custom commands + workspace layouts (`Sources/CmuxConfig.swift:268-271`).
  - `<project>/cmux.json` — per-project; local takes precedence over global, discovered by walking up from the workspace cwd (`Sources/CmuxConfig.swift:337-350`). File watchers auto-reload on edit (`Sources/CmuxConfig.swift:404-500`).
  - `~/.config/cmux/settings.json` — keyboard shortcuts + general settings (`Sources/KeyboardShortcutSettingsFileStore.swift`).
  - `~/.config/ghostty/config` — inherited for themes/fonts/colors.
- **Per-project vs global:** Both. Local `cmux.json` wins; global fills remaining commands by name (`Sources/CmuxConfig.swift:352-384`).
- **Key config options in cmux.json:** `commands[]` with `name`, `description`, `keywords[]`, `restart` (`recreate|ignore|confirm`), `command` (string) OR `workspace` (full tree), `confirm`. `workspace`: `name`, `cwd`, `color` (#RRGGBB), `layout`. `layout` is a recursive `CmuxLayoutNode` — either a `pane` (with `surfaces[]`) or a `split` (`direction: horizontal|vertical`, `split: 0.1–0.9`, `children[2]`). Each `surface` has `type: terminal|browser`, `name`, `command`, `cwd`, `env` (dict), `url`, `focus`. (`Sources/CmuxConfig.swift:9-256`)
- **Environment variables:**
  - `CMUX_SOCKET_PATH` — socket path (inherited from the spawning cmux).
  - `CMUX_TAB_ID` / `CMUX_PANEL_ID` / `CMUX_SURFACE_ID` — current surface/panel identifiers, set in each pty environment; used by hooks to self-identify.
  - `CMUX_BUNDLE_ID` — override bundle id for Sentry.
  - `CMUX_DISABLE_SESSION_RESTORE=1` — skip restore.
  - `CMUX_POSTHOG_ENABLE=1` — enable PostHog in debug builds.
  - `CMUX_SOCKET=/tmp/cmux-debug-<tag>.sock` — point CLI at a tagged debug socket (`CLAUDE.md:194-196`).
- **Lifecycle hooks / event system:** Agent-side hooks only (per-agent `install-hooks` for claude/codex/cursor/gemini/copilot/codebuddy/factory/qoder). No generic workspace-create / pane-close / notification-received hooks that users can register.

**Overcode:** YAML global config; Claude Code hook integration; per-session config via supervisor. cmux's layout-as-config is richer than Overcode for initial spawn composition.

### 13. Web Dashboard / Remote Access

- **Web UI available?** There is a `web/` directory (Next.js + TypeScript + i18n for 22+ languages, `web/i18n/`) but it's the **cmux.com marketing + docs site** (`web/app/docs/*`, `web/app/blog/*`), not a control-plane dashboard. The app itself is macOS-only.
- **API endpoints?** **Unix-domain socket API**, not HTTP. CLI subcommands are thin wrappers around socket RPCs. `cmux rpc` (`CLI/cmux.swift:1947`) exposes raw RPC pass-through. `cmux capabilities` (`CLI/cmux.swift:1943`) advertises supported commands. There is *no HTTP API or web dashboard*.
- **Remote monitoring (multi-machine):** Indirect — `cmux ssh user@host` creates a workspace that shells into the remote box and routes browser surfaces through a SOCKS5 + HTTP CONNECT proxy so `localhost:3000` on the remote works in the in-app browser (`docs/remote-daemon-spec.md` sections "Browser Proxy" M-006 through M-010). The remote daemon exposes `session.open/attach/resize/detach/status/close` and `proxy.open/close/write/stream.subscribe` RPCs. You aren't watching N remote cmux instances from one UI — you're SSH-ing into remote machines from one UI.
- **Mobile-friendly:** Founder's Edition lists "Early access: iOS app with terminals synced between desktop and phone" (`README.md:284-286`) as a future feature. Not shipped.

**Overcode:** full HTTP API, Vue/analytics dashboard, sister integration for aggregating agents across N machines into one view, mobile-accessible web dashboard, cloud relay. cmux wins on *SSH ergonomics*, Overcode wins on *many-machine observability*.

### 14. Git / VCS Integration

- **Branch management:** **Read-only.** Per-workspace and per-panel `gitBranch` state surfaced in the sidebar (`Sources/Workspace.swift` fields `gitBranch`, `panelGitBranches`). No create/switch/merge.
- **Commit automation:** Not supported.
- **PR creation:** Not supported, but cmux *displays* linked PR status/number (`pullRequest: SidebarPullRequestState`, `panelPullRequests`). State is fed by an external source (presumably `gh` via `cmux set-status` or similar).
- **Merge conflict resolution:** Not supported.
- **GitHub/GitLab integration:** No direct API; PR metadata can be populated by user scripts / agent hooks via the socket.

**Overcode:** similar passive git awareness; no merge workflow; `sync to main` CLI command.

### 15. Notifications & Attention

**This is cmux's signature feature.** The README (`README.md:119-125`) explicitly says the whole app exists because Claude Code's "Claude is waiting for your input" system notifications were useless without context.

- **Alert channels:**
  - **Blue ring** on the focused pane when the agent is waiting (`README.md:32-37`).
  - **Sidebar tab highlight** on the workspace badge (`README.md:40-45`).
  - **Notifications panel** (⌘I) aggregates all pending; **⌘⇧U jumps to latest unread** across all workspaces (`README.md:196-201`).
  - **Native macOS notifications** via `UNUserNotificationCenter`.
  - **Custom sounds** — 17 system sounds (Basso through Tink) + custom file path (aif/aiff/caf/wav) + custom shell command per notification (`Sources/TerminalNotificationStore.swift:48-108`).
  - **Dock tile updates** via `AppIconDockTilePlugin.swift`.
  - **Flash focused panel** (⌘⇧H and `cmux trigger-flash`).
- **Attention prioritization:** "Latest unread" target is tracked across all workspaces/surfaces so ⌘⇧U always goes to the oldest unread. `cmux jumpToUnread` mirrors this.
- **Focus-stealing discipline:** Explicit policy (`CLAUDE.md:184-188`) that *non-focus* socket commands must not steal macOS focus; only a whitelist of focus-intent commands (e.g., `window.focus`, `workspace.select`, `surface.focus`) may change the foreground.

**Overcode:** no native notifications at all — this is a clear gap.

### 16. Data & Analytics

- **Session history / archival:** Scrollback restored best-effort per terminal (cap 4000 lines / 400k chars, `Sources/SessionPersistence.swift`); notification history via `cmux list-notifications` (`CLI/cmux.swift:2591`); log history via `cmux list-log` (`CLI/cmux.swift:2688`).
- **Data export formats:** Not supported (no Parquet / CSV / JSON session dumps).
- **Analytics / metrics dashboards:** **Not user-facing.** The app emits `cmux_daily_active` and `cmux_hourly_active` events to PostHog (`Sources/PostHogAnalytics.swift:14-15`) for the developers' own telemetry.
- **Presence / activity tracking:** Focus/active-app events go to PostHog; no user-visible presence overlay.

**Overcode:** Parquet export for offline analysis, rich timeline view, per-agent cost/token/joule counters, presence tracking with idle/lock detection, cross-machine sister aggregation.

### 17. Extensibility

- **Plugin / hook system:** cmux has no user-pluggable hook system. The `skills/` directory (`skills/cmux`, `skills/cmux-browser`, `skills/cmux-markdown`, `skills/cmux-debug-windows`, `skills/release`) ships **Claude Code skill markdown files** that teach Claude how to drive cmux — they run in Claude, not in cmux. Each is a `SKILL.md` that references reference docs.
- **MCP server support:** Not built in.
- **API for external tools:** Unix-domain socket is the extension surface. `cmux rpc` + `cmux capabilities` + 60+ subcommands = the public API.
- **Custom agent definitions:** Not supported (agents are just shell commands).

### 18. Developer Experience

- **Install:** `.dmg` download or `brew install --cask cmux` (tap `manaflow-ai/cmux`). Sparkle auto-update (`README.md:94-116`).
- **First-run:** `cmux welcome` command (`CLI/cmux.swift:1740`); directory-trust prompt for unknown paths (`Sources/CmuxDirectoryTrust.swift`).
- **Documentation:** README in 22 languages; full docs site at cmux.com/docs (built from `web/`); CONTRIBUTING.md, PROJECTS.md, CHANGELOG.md, AGENTS.md (agent-author guidance), CLAUDE.md (agent-facing build/test policy), TODO.md.
- **Test coverage / CI:** 101 E2E Python tests in `tests_v2/` hitting the running app over its socket; Swift unit tests in `cmuxTests/`; UI tests in `cmuxUITests/`. Tests run on GitHub Actions; local test execution is explicitly discouraged (`CLAUDE.md:190-197`). Regression-test-then-fix two-commit policy (`CLAUDE.md:136-142`). Separate NIGHTLY build channel with its own Sparkle feed (`README.md:231-237`).
- **Nightly builds:** Yes — separate bundle ID, runs alongside stable.
- **Founder's Edition paid tier** (`README.md:278-289`): prioritized features, early access to cmux AI, iOS sync, cloud VMs, voice mode, founder iMessage access.

## Unique / Notable Features

1. **Notification rings + sidebar flood-fill + `cmux jumpToUnread`.** The entire UX is architected around "I have N agents waiting, which one first?" The blue ring + tab badge + ⌘⇧U jump-to-latest-unread is a genuinely better solve than macOS's native "Claude is waiting" toast.
2. **Scriptable in-app browser with a 125-verb CLI.** `cmux browser --surface <id> click '[data-testid=submit]'`, `cmux browser snapshot --json`, `cmux browser eval 'document.title'`. Ported from vercel-labs/agent-browser. Agents can drive a real Chromium-class browser next to their terminal, snapshot the accessibility tree, upload files, intercept network, save/load state. Covered by 20+ E2E tests (`tests_v2/test_browser_*.py`).
3. **SSH workspaces with transparent localhost.** `cmux ssh user@host` routes the in-app browser through a per-transport SOCKS5/HTTP proxy on the remote so `http://localhost:3000` in the browser surface hits the remote dev server. Full PTY resize coordination ("smallest screen wins") across reconnects. Drag-image-to-upload via scp (`README.md:67-75`, `docs/remote-daemon-spec.md`).
4. **Ghostty compatibility.** Reads `~/.config/ghostty/config` for themes/fonts/colors so users bring their existing setup (`README.md:91`). libghostty renders GPU-accelerated; typing latency is a hot path that has documented no-touch rules (`CLAUDE.md:156-161`).
5. **Layout-as-config.** `cmux.json` can declare an entire workspace as a recursive split tree with per-surface cwd/env/command/url — boot a dev environment in one palette selection (`Sources/CmuxConfig.swift`).
6. **Fully rebindable shortcuts** via `~/.config/cmux/settings.json` with a policy (`CLAUDE.md:162-163`) that every new shortcut must be registered, visible in Settings, file-overridable, and documented.
7. **tmux compatibility shim.** `cmux __tmux-compat`, `cmux capture-pane` let agents that shell out to tmux-specific commands keep working (`CLI/cmux.swift:2774-2783`).
8. **Browser import.** One-click import of cookies/history/sessions from 20+ browsers (Chrome, Firefox, Arc, etc.) so in-app browser panes start authenticated (`README.md:87`).
9. **Per-agent hook installers** for 7+ agents — one command (`cmux setup-hooks`, `cmux cursor install-hooks`, etc.) wires agent events to cmux notifications.
10. **Claude Code Teams native splits.** `cmux claude-teams` replaces Claude's tmux dependency with native panes + sidebar metadata + notifications per teammate.

## What This Tool Does Better Than Overcode

- **Notification system.** Pane rings, sidebar flood, ⌘⇧U jump-to-unread, native macOS notifications, 17 custom sounds, custom shell command per notification. Overcode has no native notifications. This is a concrete adoption candidate.
- **Scriptable browser alongside the terminal.** A 125-verb browser automation API exposed via CLI, drivable from any agent, with cookies imported from the user's real browsers. Overcode has no browser primitive.
- **SSH workspaces with transparent localhost-over-proxy.** Sshing in from the TUI and having `localhost:N` Just Work in a co-located browser is a killer dev workflow. Overcode's sister integration solves different problem (cross-machine *observability*), not cross-machine *work*.
- **Native terminal performance.** GPU-accelerated libghostty with strict typing-latency rules. Textual over SSH will never match this.
- **Layout-as-config.** One JSON entry opens a 4-surface workspace with the right cwds, envs, and browser URLs. Overcode has no equivalent to `cmux.json`'s layout tree.
- **Per-agent hook installers for 7+ agents.** Overcode is Claude-only; cmux's install-hooks model generalizes cleanly.
- **Fully rebindable shortcuts with file-level override.** Overcode's keybindings are largely fixed.
- **Ghostty-config reuse + browser cookie import.** Both are small but meaningful "meet the user where they are" moves.
- **OSC 9/99/777 passive pickup.** Agents that already emit BEL/OSC sequences "just work" with zero config.

## What Overcode Does Better

- **Entire supervision layer.** Supervisor daemon, 25 standing-instruction presets, heartbeat-to-idle-agents, oversight mode with stuck detection and timeouts, intervention history, standing-instruction-per-agent. cmux has *none* of this — it stops at "send keys to a surface."
- **Cost & budget management.** Per-agent tokens, dollars, joules; soft-enforced budgets; budget transfer; pricing model per model. cmux has zero visibility into agent spend.
- **Agent hierarchy.** 5-level parent/child trees, cascade kill, fork-with-context, follow mode. cmux treats workspaces/panes as flat peers.
- **Rich status taxonomy with authoritative transitions.** 10+ states (running, waiting_user, waiting_approval, idle, error, done, sleeping, terminated, etc.) detected via 442 regex patterns + Claude Code hooks. cmux has `has-notification` / `has-status` / `has-progress` only — state is whatever the agent chose to push.
- **Web dashboard + HTTP API.** cmux is socket-only and macOS-only; Overcode's HTTP dashboard is accessible from any browser/machine.
- **Sister multi-machine aggregation.** See N remote machines' agents in one view. cmux's SSH feature lets you work on one remote at a time; it doesn't aggregate.
- **Data export (Parquet) + timeline view.** Offline analysis in Jupyter; color-coded status history bars. cmux's analytics are PostHog-to-devs, not user-facing.
- **Claude-Code-specific deep integration.** JSON session log parsing, token extraction from transcripts, pricing by model, session fork with context inheritance.
- **Runs on Linux/anywhere Python + tmux runs.** cmux is macOS-only.
- **Information density.** A Textual dashboard with configurable columns, 4 sort modes, timeline bars, and ~50+ keybindings surfaces far more per-screen than cmux's 200px sidebar of workspace rows.

## Ideas to Steal

| # | Idea | Value | Complexity | Notes |
|---|---|---|---|---|
| 1 | **OSC 9 / 99 / 777 notification pickup** | High | Low | Agents emit these already (Claude, Codex). Textual can parse OSC from the tmux pipe and raise a notification event. Would give Overcode a notification channel without any agent changes. |
| 2 | **Native desktop notifications with per-agent sounds** | High | Low-Med | pyobjus/pync on macOS, libnotify on Linux. Already the obvious Overcode gap. cmux's 17-sound menu + custom shell command per notification is a nice UX template. |
| 3 | **"Jump to latest unread" global hotkey** | High | Low | Simple LRU of attention-requesting agents + a hotkey that focuses the top one. cmux's ⌘⇧U is the cleanest solve for the "which agent do I look at first?" problem and Overcode can mirror it verbatim. |
| 4 | **Layout-as-config (workspace presets in JSON/YAML)** | Med-High | Medium | Overcode has no equivalent to `cmux.json`'s nested `pane`/`split`/`surface` tree. Users could declare "my dev stack" as one preset and spawn 4 linked agents with one hotkey. |
| 5 | **Per-agent hook installers for Codex / Cursor / Gemini / Copilot** | Medium | Medium | Overcode is Claude-only; even partial multi-agent support (e.g., Codex via hook) is a useful hedge. cmux's install-hooks CLI pattern generalizes well. |
| 6 | **Scriptable browser surface** | Medium | High | Huge ecosystem move but expensive. Could start smaller: an Overcode CLI command that pipes browser automation to Playwright in a separate process, driven from the supervisor daemon. |
| 7 | **Socket/RPC control surface exposing everything the TUI does** | Medium | Medium | cmux's "every UI action has a CLI" discipline (send-keys, focus-pane, split, notify, set-status) is a good model. Overcode already has an HTTP API — extending it to cover every TUI action + a `rpc` pass-through would let users script Overcode as fluidly as cmux. |
| 8 | **`cmux.json`-style lifecycle hooks (`restart: recreate\|ignore\|confirm`)** | Med | Low | Per-preset "what to do on restart" is a small but thoughtful detail worth copying. |
| 9 | **Custom sound per notification + custom shell command per notification** | Low-Med | Low | Small UX win with low cost; useful for power users who want different tones per agent type / priority. |
| 10 | **"Set-status"/"set-progress"/"log" — free-form per-agent sidebar metadata** | Medium | Low-Med | Overcode's status enum is rich but closed. A `overcode status set <agent> <key> <value>` + rendering in the agent row would let agent prompts surface arbitrary state (current task name, test count, build stage). cmux's sidebar is readable because agents push meaningful labels, not because Overcode guesses them. |
| 11 | **Fully rebindable shortcuts via file + Settings UI** | Low-Med | Medium | Overcode's keybindings are mostly fixed. cmux's `settings.json` + Settings panel + "every new shortcut must be registered and documented" policy (`CLAUDE.md:162-163`) is a model. |
| 12 | **Browser cookie import from native browsers** | Low | Med | Only relevant if Overcode adds a browser primitive. But worth noting — "start authenticated" is a meaningful DX win. |
| 13 | **tmux-compat shim for non-tmux tools** | Low | Low | Overcode already uses tmux, so this is largely moot — but the reverse (dispatching tmux commands into Overcode-native panes if Overcode ever moved off tmux) could reuse cmux's approach. |

---

## The Big Takeaway

**cmux and Overcode barely overlap.** cmux is a *native macOS terminal with a browser and a notification system*; Overcode is a *Claude Code supervisor daemon with a TUI front-end*. They compete only in the shared marketing claim of "tool for running AI agents in parallel."

The honest positioning:

- **cmux** owns **the surface** — a native, fast, scriptable container for agents with best-in-class attention management. It has no opinion about what agents should be doing.
- **Overcode** owns **the orchestration** — standing instructions, heartbeats, budgets, hierarchies, supervisor daemons, cross-machine aggregation. Its surface (Textual TUI) is strictly inferior to a native app.

The biggest concrete cmux wins worth stealing for Overcode are, in order: (1) native notifications with jump-to-unread, (2) OSC 9/99/777 passive pickup so Overcode gets a notification channel free, (3) layout-as-config presets, and (4) free-form per-agent `set-status` / `set-progress` / `log` metadata that agents push into the sidebar. None of these require changing Overcode's core orchestration model.

The biggest Overcode advantages cmux *could* adopt (but won't, given its "primitive not solution" stance) are the supervision layer, cost tracking, and agent hierarchy. cmux has explicitly decided not to be that tool.

A user who wants **both** — native terminal UX *and* supervisor-grade orchestration — can run Overcode inside a cmux workspace; the two stack cleanly because cmux is a terminal and Overcode is a terminal app.
