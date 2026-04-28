# Overcode vs Orca: Feature Bakeoff

## Overview

| | **Orca** | **Overcode** |
|---|---|---|
| **Repo** | [stablyai/orca](https://github.com/stablyai/orca) | This project |
| **Language** | TypeScript (Electron + React) | Python (Textual TUI) |
| **Stars** | ~1,118 | N/A (private) |
| **License** | MIT (Lovecast Inc.) | Proprietary |
| **First Commit** | 2026-03-16 | 2025 |
| **Last Commit** | 2026-04-15 (1.2.2-rc.0) | Active |
| **Purpose** | Electron IDE that runs multiple CLI agents (Claude Code, Codex, Gemini, etc.) side-by-side, each in its own git worktree, with built-in editor/diff/source-control/browser. | Claude Code supervisor/monitor with instruction delivery |

## Core Philosophy

Orca is positioned as "the AI orchestrator for 100x builders" — a desktop Electron app (macOS/Windows/Linux) that treats every feature/ticket as a **worktree**, and every worktree as a first-class workspace with its own terminal tabs, editor panes, diff viewer, file explorer, browser tab, and GitHub PR/checks view. The user's mental model is: **one worktree = one unit of work = one agent conversation**. Agents run as arbitrary CLI processes inside a node-pty terminal; Orca never speaks to an agent's API directly — it detects activity by parsing OSC titlebar updates (`src/shared/agent-detection.ts:265`) that the CLIs themselves emit.

The workflow loop is: **add repo → create worktree (`Cmd+N`) → the worktree dialog optionally pre-seeds a Claude/Codex command from `orca.yaml`'s `issueCommand:` hook → terminal spawns in the worktree and runs the agent → user reviews diffs in built-in Source Control tab → merge PR from inside Orca**. The new `orca` CLI + `orca-cli` skill lets agents themselves drive this loop (create sibling worktrees, send input to peer terminals, update per-worktree comments as progress checkpoints).

Orca is agent-agnostic but does **no orchestration, no API accounting, no LLM-based supervision**. It is fundamentally a developer IDE that happens to arrange N PTYs in a git-worktree-aware layout. Its differentiating bets are (1) the IDE/editor surface, (2) a built-in browser with "Design Mode" (click-to-context), and (3) Codex account hot-swapping.

## Feature Inventory

### 1. Agent Support
- **Claude Code, Codex, Gemini, OpenCode, Aider, Pi, Hermes, Goose, Amp, Auggie, Charm, Cline, Codebuff, Continue, Cursor, Droid, GitHub Copilot, Kilocode, Kimi, Kiro, Mistral Vibe, Qwen Code, Rovo Dev** — all advertised in `README.md:35-58`.
- Detection is **agent-agnostic via OSC terminal-title parsing**. The canonical list in code is `AGENT_NAMES = ['claude', 'codex', 'gemini', 'opencode', 'aider']` at `src/shared/agent-detection.ts:19`, with special-case handling for Gemini's ✦/⏲/◇/✋ symbols, Pi's `π - ` prefix and braille spinner, and Claude's `✳ `, `. `, `* ` prefixes (`src/shared/agent-detection.ts:12-18`, `205-233`).
- No plugin system — adding a new agent requires editing `agent-detection.ts`. Any CLI that is shown at a shell prompt will run; Orca just can't attribute status to it.
- **Not locked to one agent**: multiple different agents can run in different panes simultaneously.

### 2. Agent Launching
- Agents launch as PTY processes via the "Create Worktree" dialog (`Cmd+N`, `src/renderer/src/components/sidebar/AddWorktreeDialog.tsx`) or the `orca worktree create` CLI.
- Inputs on dialog: **repo, name, linked GitHub issue number, comment, base branch (optional), "Run GitHub issue command" checkbox**.
- Pre-written prompts come from **`orca.yaml` → `issueCommand:` block scalar** (`src/main/hooks.ts:160-210`). Per-user override at `.orca/issue-command` (auto-added to `.gitignore`). The template is substituted with the linked issue number, then pasted into the new terminal.
- The initial prompt is delivered by the **shell-ready protocol**: the PTY emits an `OSC 133;A` marker once the user's `.zshrc`/`.bashrc` has finished sourcing, then Orca writes the full command as one payload (`src/main/providers/local-pty-shell-ready.ts:22-235`). This avoids the common bug of characters dropping when the shell isn't yet reading.
- Templates/presets: the `orca.yaml` `issueCommand` is the template. No other preset library.
- **Setup hook**: `scripts.setup` from `orca.yaml` runs inside the new worktree before the agent starts; `scripts.archive` runs on worktree delete (`src/main/hooks.ts:264-305`). Setup policy can be `run-by-default`, `ask`, or `never`.

### 3. Session/Agent Lifecycle
- Agent states detected: **`working`, `permission`, `idle`**, plus `unknown`/`agent`/`stopped` as internal PTY-level states (`src/main/stats/agent-detector.ts:5`; `src/shared/agent-detection.ts:10`).
- Persistence: a single JSON file at `userData/orca-data.json` (`src/main/persistence.ts:34`). Schema version 1. Atomic write via temp-file + rename, debounced 300ms (`src/main/persistence.ts:106-146`).
- Persisted data: `repos`, `worktreeMeta` (display name, comment, linked issue/PR, archived, unread, pinned, sort order, lastActivityAt), `settings`, `ui`, `githubCache` (PR/issue responses), `workspaceSession` (active repo/worktree/tab, open files, browser tabs), `sshTargets`.
- **Survives process restart**: yes — full workspace session is restored (open tabs, terminal layouts, browser URLs per worktree).
- **Survives reboot**: yes — but PTY processes do NOT survive process exit. On reopen, panes show a disconnected state and the user re-spawns. Orca does not preserve scrollback across app restart by default beyond a configurable `terminalScrollbackBytes` in-memory (default 10 MB).
- Resume/reattach: only within a single Orca process. No detach/reattach across different Orca launches.
- Cleanup on close/kill: worktree removal via `git worktree remove` → `git worktree prune` → auto-deletes the local branch if no other worktree claims it (`src/main/git/worktree.ts:165-210`). Archive hook runs before.

### 4. Isolation Model
- **Worktree-native**: every unit of work is a git worktree under `<workspaceDir>/<repo>/<name>` (default workspace dir `~/orca/workspaces`; configurable via `settings.workspaceDir`).
- **Branch management**: auto-creates `<branchPrefix>/<name>` where prefix can be `git-username`, `none`, or a custom string (`src/shared/constants.ts:82-84`).
- **Shared workspace**: worktrees on the same repo share the git object store but not the working tree. Tabs/terminals/files are per-worktree.
- **Merge workflow**: built-in `PRActions` component (`src/renderer/src/components/right-sidebar/PRActions.tsx`) offers **Squash-and-merge / Create-merge-commit / Rebase-and-merge** via `gh pr merge`. Conflict summary is computed from `git merge-tree` output (`src/main/github/conflict-summary.ts`).
- **Sub-task / sub-worktree support**: not first-class, but any agent can call `orca worktree create` via CLI to spawn a sibling worktree — effectively a flat peer model, not a tree.

### 5. Status Detection
- **Polling-free, output-driven**: every PTY chunk is scanned for OSC-title-set escape sequences (`ESC ] 0|1|2 ; <title> BEL|ESC \`, regex at `src/shared/agent-detection.ts:23`).
- `createAgentStatusTracker` transitions between `working → idle/permission` fire an **"became idle"** callback (the same trigger used for unread badges and desktop notifications; `src/shared/agent-detection.ts:123-156`).
- `detectAgentStatusFromTitle` uses per-agent symbol heuristics: Claude ✳ prefix = idle, ". " prefix = working, "* " prefix = idle; Gemini ✦/⏲ = working, ◇ = idle, ✋ = permission; Pi `π - ` = idle; braille spinner = working; keyword fallback (`working`/`thinking`/`running`/`ready`/`idle`/`done`/`action required`/`permission`/`waiting`).
- **Latency**: transition is detected on the next PTY chunk — effectively instant (tens of ms).
- **Cost**: zero API calls. Pure regex + string scan on a PTY buffer already being streamed to xterm.js.
- No LLM-based status detection. No classification of work type.

### 6. Autonomy & Auto-Approval
- **Not supported.** Orca does not auto-accept permission prompts, doesn't decide when to intervene, and has no risk-assessment layer. The user clicks into the terminal and answers. Titlebar ✋ (permission) just drives a red badge and optional desktop notification.
- No "safety mode" / "YOLO mode" toggle.

### 7. Supervision & Instruction Delivery
- **Send input to running agents**: yes, via `orca terminal send --terminal <handle> --text "…" --enter` (`src/cli/index.ts:271`) and `orca terminal wait --for exit` (`src/cli/index.ts:281`) — this is the agent-to-agent / script-to-agent surface.
- **Standing instructions / persistent directives**: Not supported. The only persisted "instruction" concept is the per-worktree `comment` field (a user-written or agent-updated markdown note).
- **Heartbeat / periodic instruction delivery**: Not supported.
- **Supervisor daemon or meta-agent**: Not supported. There is a runtime RPC layer (`src/main/runtime/orca-runtime.ts`) but it serves the CLI, not a supervising LLM.
- **Intervention history / logging**: terminals buffer `terminalScrollbackBytes` (default 10 MB) in xterm serialization, but there is no structured "events sent by the supervisor" log.

### 8. Cost & Budget Management
- **Token tracking**: yes, for Claude and Codex. `src/main/claude-usage/scanner.ts` scans `~/.claude/projects/*/ *.jsonl` session logs; `src/main/codex-usage/scanner.ts` does the same for Codex.
- **Cost calculation**: Claude and Codex panes show daily/cumulative token usage charts (`ClaudeUsageDailyChart.tsx`, `CodexUsageDailyChart.tsx`). Dollar conversion is **not** shown — units are raw input/output/cache tokens.
- **Per-agent budgets**: Not supported.
- **Budget enforcement**: Not supported.
- **Rate-limit display**: Claude and Codex rate-limit windows/resets are fetched and surfaced in the status bar (`src/main/rate-limits/service.ts`, `src/main/rate-limits/claude-fetcher.ts`, `src/main/rate-limits/codex-fetcher.ts`).

### 9. Agent Hierarchy & Coordination
- **Parent/child relationships**: Not modeled. Worktrees are a flat list.
- **Agent-to-agent communication**: indirect, via the runtime CLI — one agent can `orca terminal send` to another agent's pane. There is no message-queue or mailbox.
- **Task decomposition**: Not supported.
- **Cascade operations**: removing a worktree kills its terminals and deletes the branch, but there is no "kill all children" because there are no children.
- **Follow/oversight modes**: Not supported.

### 10. TUI / UI
- **GUI**, not TUI. Electron 41 + React 19 + Tailwind v4 + Radix UI + shadcn; terminals rendered via `@xterm/xterm` with webgl/serialize/search/unicode11/web-links addons.
- Layout: classic IDE — left sidebar (worktree list grouped by repo), center tab bar with terminal + editor + browser tabs, right sidebar with three tabs (File Explorer / Search / Source Control), optional status bar.
- Features visible in UI: worktree cards (status dot, unread, CI check, issue badge, PR badge, comment), titlebar "active agents" popover showing every working agent across all worktrees with click-to-focus (`src/renderer/src/App.tsx:515-598`), Codex account switcher chip, Claude/Codex usage panes, stats pane.
- **Worktree card columns** (configurable): `status`, `unread`, `ci`, `issue`, `pr`, `comment` (`src/shared/constants.ts:46-53`).
- **Sort/group**: group by `none` or `repo`; sort by `name`, `recent`, or `repo`. Pinned worktrees float to top. `showActiveOnly` hides inactive worktrees. Filter by repo-id list.
- **Themes**: terminal themes include `Ghostty Default Style Dark`, `Builtin Tango Light` and ~many others (`TerminalThemeSections.tsx`); separate light/dark theme toggle; follow system.

**Keyboard shortcuts** (from `src/renderer/src/components/settings/ShortcutsPane.tsx:28-173`, `src/renderer/src/App.tsx:407-476`, `src/main/menu/register-app-menu.ts`, `src/renderer/src/components/terminal/useTerminalShortcuts.ts`):

| Group | Action | Shortcut (Mac / Win-Linux) |
|---|---|---|
| Global | Go to File (Quick Open) | `Cmd+P` / `Ctrl+P` |
| Global | Switch worktree (Jump Palette) | `Cmd+J` / `Ctrl+Shift+J` |
| Global | Create worktree | `Cmd+N` / `Ctrl+N` |
| Global | Toggle left sidebar | `Cmd+B` / `Ctrl+B` |
| Global | Toggle right sidebar | `Cmd+L` / `Ctrl+L` |
| Global | Move up worktree | `Cmd+Shift+↑` / `Ctrl+Shift+↑` |
| Global | Move down worktree | `Cmd+Shift+↓` / `Ctrl+Shift+↓` |
| Global | Toggle File Explorer tab | `Cmd+Shift+E` / `Ctrl+Shift+E` |
| Global | Toggle Search tab | `Cmd+Shift+F` / `Ctrl+Shift+F` |
| Global | Toggle Source Control tab | `Cmd+Shift+G` / `Ctrl+Shift+G` |
| Global | Settings | `Cmd+,` / `Ctrl+,` |
| Global | Zoom In | `Cmd+=` / `Ctrl+Shift++` |
| Global | Zoom Out | `Cmd+-` / `Ctrl+Shift+-` |
| Global | Reset Size | `Cmd+0` / `Ctrl+0` |
| Global | Force Reload | `Cmd+Shift+R` / `Ctrl+Shift+R` |
| Global | Jump to worktree 1-9 | `Cmd+1..9` / `Ctrl+1..9` |
| Terminal Tabs | New tab | `Cmd+T` / `Ctrl+T` |
| Terminal Tabs | Close tab / pane | `Cmd+W` / `Ctrl+W` |
| Terminal Tabs | Next tab | `Cmd+Shift+]` / `Ctrl+Shift+]` |
| Terminal Tabs | Previous tab | `Cmd+Shift+[` / `Ctrl+Shift+[` |
| Terminal Panes | Split right | `Cmd+D` / `Ctrl+Shift+D` |
| Terminal Panes | Split down | `Cmd+Shift+D` / `Alt+Shift+D` |
| Terminal Panes | Close pane (EOF) | `Ctrl+D` |
| Terminal Panes | Focus next pane | `Cmd+]` / `Ctrl+]` |
| Terminal Panes | Focus previous pane | `Cmd+[` / `Ctrl+[` |
| Terminal Panes | Clear pane | `Cmd+K` / `Ctrl+K` |
| Terminal Panes | Expand/collapse pane | `Cmd+Shift+↵` / `Ctrl+Shift+Enter` |
| Terminal Search | Find | `Cmd+F` |
| Terminal Search | Find next/prev | `Cmd+G` / `Cmd+Shift+G` |
| Browser (Grab) | Toggle Design Mode | keyboard shortcut in `useGrabMode.ts` |

Shortcuts customization is **not supported** ("Shortcuts customization is not currently supported." — `ShortcutsPane.tsx:230`).

### 11. Terminal Multiplexer Integration
- **No tmux / zellij**. Orca ships `node-pty` (patched, `patches/node-pty@1.1.0.patch`) and draws its own split layout on top of `@xterm/xterm`.
- **Split layout** is a custom serialized tree (`src/renderer/src/components/terminal-pane/layout-serialization.ts`, `TabGroupSplitLayout.tsx`) — N-way binary splits per tab, serialized to `workspaceSession.terminalLayoutsByTabId`.
- **Live output**: always live — every pane is a webgl-rendered xterm. Focus-follows-mouse is opt-in (`settings.terminalFocusFollowsMouse`).
- **Zoom/focus**: a pane can be "expanded" (fullscreen within the tab group) with `Cmd+Shift+Enter`.

### 12. Configuration
- **Config files**:
  - Global: `orca-data.json` in `app.getPath('userData')` (Electron userData dir; `~/Library/Application Support/Orca/` on macOS).
  - Per-project (checked in): **`orca.yaml`** at repo root with keys `scripts.setup`, `scripts.archive`, `issueCommand` (`src/main/hooks.ts:30-108`).
  - Per-user local override: **`.orca/issue-command`** — auto-added to `.gitignore`.
- **Settings** (from `src/shared/constants.ts:77-122` and `getDefaultUIState()`):
  - `workspaceDir` (where worktrees live)
  - `nestWorkspaces` (nest under `<workspaceDir>/<repo>/` vs flat)
  - `refreshLocalBaseRefOnWorktreeCreate` (fast-forward local `main` to `origin/main` when creating a worktree)
  - `branchPrefix`: `git-username` | `none` | `custom`, `branchPrefixCustom`
  - `theme`: `system` | `light` | `dark`
  - `editorAutoSave`, `editorAutoSaveDelayMs` (250-10000)
  - `terminalFontSize`, `terminalFontFamily`, `terminalFontWeight`
  - `terminalCursorStyle`: `bar` | `block` | `underline`; `terminalCursorBlink`
  - `terminalThemeDark`, `terminalThemeLight`, `terminalUseSeparateLightTheme`
  - `terminalDividerColorDark`, `terminalDividerColorLight`, `terminalDividerThicknessPx`
  - `terminalInactivePaneOpacity`, `terminalActivePaneOpacity`, `terminalPaneOpacityTransitionMs`
  - `terminalRightClickToPaste`, `terminalFocusFollowsMouse`
  - `terminalScrollbackBytes` (default 10 MB)
  - `terminalScopeHistoryByWorktree`
  - `openLinksInApp`, `rightSidebarOpenByDefault`, `showTitlebarAgentActivity`
  - `diffDefaultView`: `inline` | `split`
  - `promptCacheTimerEnabled`, `promptCacheTtlMs` (default 300s for Claude cache warning)
  - `codexManagedAccounts`, `activeCodexManagedAccountId`
  - **Notifications**: `enabled`, `agentTaskComplete`, `terminalBell`, `suppressWhenFocused`
  - Per-repo `hookSettings`: `mode`, `setupRunPolicy`, `scripts.{setup,archive}`
- **Environment variables** (set for hook scripts): `ORCA_ROOT_PATH`, `ORCA_WORKTREE_PATH`; compat aliases `CONDUCTOR_ROOT_PATH`, `GHOSTX_ROOT_PATH` (`src/main/hooks.ts:317-325`).
- **Lifecycle hooks**: `setup` (on worktree create) and `archive` (on worktree delete) — defined in `orca.yaml` or repo settings.

### 13. Web Dashboard / Remote Access
- **No web dashboard.** Orca is a desktop Electron app only.
- **API**: the `orca` CLI talks to the running Electron runtime via a local RPC on a runtime socket (`src/main/runtime/runtime-rpc.ts`, `src/cli/runtime-client.ts`). This is a local IPC, not a network API.
- **Remote monitoring (multi-machine)**: **yes, via SSH** — Orca ships an SSH relay (`src/main/ssh/`, `src/relay/relay.ts`) that deploys a helper binary on a remote host and multiplexes PTY + filesystem + git operations over an ssh2 channel. A worktree can live on a remote machine and still show up in the same Orca sidebar.
- **Mobile-friendly**: No.

### 14. Git / VCS Integration
- **Branch management**: auto-created on worktree create with configurable prefix; auto-deleted on worktree remove when no other worktree claims the branch (`src/main/git/worktree.ts:184-209`).
- **Commit automation**: Source Control panel supports stage/unstage, commit with message, amend, discard hunks. Commits happen through `simple-git`.
- **PR creation**: indirectly — the `PRActions` panel surfaces an existing PR once `gh` finds one for the branch; the "create PR" affordance calls `gh pr create`. If no PR exists yet, the panel prompts the user to open one.
- **PR merge**: in-app squash / merge / rebase via `gh pr merge` (`src/renderer/src/components/right-sidebar/PRActions.tsx:9-55`).
- **Merge conflict resolution**: conflicts are surfaced in Source Control with special `ConflictComponents.tsx`; detailed conflict summary pre-merge via `git merge-tree` (`src/main/github/conflict-summary.ts`).
- **GitHub integration** via `gh` CLI: PR status, check runs, review status, issue fetch (`src/main/github/client.ts`, `issues.ts`). Authenticated viewer detection (`getAuthenticatedViewer`) and an "star the Orca repo" prompt (`checkOrcaStarred`/`starOrca`).
- **Remote support**: SSH-hosted git repos work transparently via the SSH relay and `ssh-git-provider.ts`.
- **GitLab**: Not supported (only `gh`).

### 15. Notifications & Attention
- **Native OS notifications** via Electron's `Notification` API (`src/main/ipc/notifications.ts:1-187`).
- Sources: `agent-task-complete` (triggered by became-idle transition), `terminal-bell` (BEL character from PTY), `test`.
- **Cooldown**: 5 s per-worktree dedupe so agent-complete + terminal-bell don't double-fire.
- **Suppress-when-focused**: if the worktree is active and the window is focused, the notification is skipped (configurable).
- **Click behavior**: clicking a notification raises Orca, activates the worktree, and reveals it in the sidebar (`notifications.ts:92-119`).
- **Unread badge**: worktree cards show an unread dot when an agent in that worktree transitions to idle while not focused. The user can manually "mark unread" to come back later (per-worktree `isUnread` in `WorktreeMeta`).
- **Titlebar agent badge**: shows live count of working agents across all worktrees; click expands a hovercard listing every working agent with jump-to-pane.
- **First-run notification prompt** on macOS to trigger the system permission dialog with a useful message (`notifications.ts:136-187`).
- **Attention prioritization**: just "is idle" + "is unread"; no urgency ranking.

### 16. Data & Analytics
- **Stats collector** (`src/main/stats/collector.ts`) logs events to `userData/stats.json` (schema v1, ≤10,000 events, ≤2,000 deduplicated counted-PRs). Debounce 5 s.
- Event types include worktree-create, PR-open, session-start/end — exposed via a **Stats pane** (`StatsPane.tsx`, `StatCard.tsx`).
- **Claude usage**: scans `~/.claude/projects/` jsonl logs, shows daily token chart (`ClaudeUsageDailyChart.tsx`).
- **Codex usage**: same for `~/.codex/`.
- **Data export**: Not supported (no Parquet/CSV/JSON export button).
- **Presence / activity**: `lastActivityAt` per worktree; "active agents" titlebar badge.

### 17. Extensibility
- **Plugin / hook system**: `orca.yaml` hooks only (`setup`, `archive`, `issueCommand`). No general plugin API.
- **MCP server support**: Not supported directly. Users can run MCP servers inside their agent's CLI, but Orca has no MCP awareness.
- **API for external tools**: the **`orca` CLI** is the external API (`orca open|status|repo …|worktree …|terminal …`). Command list at `src/cli/index.ts:42-173`:
  - `orca open`, `orca status`
  - `orca repo list|add|show|set-base-ref|search-refs`
  - `orca worktree list|show|current|create|set|rm|ps`
  - `orca terminal list|show|read|send|wait|stop`
- **Selectors** uniform across CLI: `id:<id>`, `name:<name>`, `path:<path>`, `branch:<branch>`, `issue:<number>`, or `active`/`current` (resolves enclosing worktree, `src/cli/index.ts:484-505`).
- **Skill** (`skills/orca-cli/SKILL.md`): a `SKILL.md` instructing AI agents to drive the CLI; installed via `npx skills add https://github.com/stablyai/orca --skill orca-cli`.
- **Custom agent definitions**: add agent-name match in `src/shared/agent-detection.ts` — no config-driven way.

### 18. Developer Experience
- **Install**: download prebuilt installer from `onOrca.dev` or GitHub Releases (`.dmg`, `.exe`, `.AppImage`/`.deb`). No `pip`/`npm install -g`; the `orca` CLI is installed by the app itself under Settings → CLI.
- **First-run**: shows a **Landing** screen prompting "Add a repository to get started" with a preflight banner (missing `gh`, `git`, etc.) and keyboard-shortcut hints (`src/renderer/src/components/Landing.tsx:190-240`). A startup macOS notification prompt appears once (`triggerStartupNotificationRegistration`).
- **Docs**: README + `docs/README.zh-CN.md` + `docs/README.ja.md` + many in-repo design docs (`docs/design-*.md`, `docs/performance-*.md`, `docs/split-groups-rollout-*.md`). Discord + X links.
- **Tests**: extensive vitest suite — >180 `*.test.ts` files across `src/main/`, `src/relay/`, `src/cli/`, `src/renderer/src/`. Husky + lint-staged with `oxlint` + `oxfmt`. Three-project typecheck (`tc:node`, `tc:cli`, `tc:web`).
- **CI**: GitHub Actions (see `.github/`), release-candidate scheduler commits (e.g. `release: 1.2.2-rc.0 [rc-slot:2026-04-15-15]` — daily ship cadence).

## Unique / Notable Features

1. **OSC-title-based agent detection** — zero-cost, zero-API, instant status transitions for ~8 different agent CLIs with per-agent symbol heuristics (`src/shared/agent-detection.ts:265-318`). No LLM, no polling loop, no hooks.
2. **Shell-ready protocol for startup commands** — Orca writes a custom `.zshrc`/`.bashrc` wrapper into `userData/shell-ready/` that emits `OSC 133;A` after user rc files finish. The startup command is written exactly once, as a single payload, eliminating the dropped-chars race that plagues naive tmux-send-keys approaches (`src/main/providers/local-pty-shell-ready.ts:77-235`).
3. **Design Mode / Grab** — built-in Electron browser (`src/main/browser/browser-manager.ts`, `src/renderer/src/components/browser-pane/`) lets the user click any rendered DOM element in their local dev-server preview and drop the selector+screenshot as a context chip into the agent chat. Implemented via a guest-frame script injected into the webview.
4. **Codex account hot-swap** — managed Codex accounts stored in `codex-accounts` userData subfolder with per-account `$HOME` overlay; switching is one click and doesn't touch the real home directory (`src/main/codex-accounts/service.ts`).
5. **`orca` CLI as the agent-to-agent bus** — an agent can spawn peer worktrees, read another agent's terminal output, send input, and wait for its exit. Paired with a `SKILL.md`, this is a realistic "N agents coordinate through Orca as the orchestrator" pattern without any LLM supervisor.
6. **SSH-native remote worktrees** — dedicated relay binary (`src/relay/relay.ts`) deploys to a remote host and multiplexes PTY + filesystem + git over a single ssh2 channel. Remote worktrees appear in the same sidebar.
7. **Per-worktree comment as agent progress note** — the `SKILL.md` explicitly tells agents to update the worktree's `comment` field on every meaningful checkpoint. Gives the user a glanceable "what is this worktree currently doing" column without any parsing.
8. **Titlebar active-agent popover** — a single hovercard lists every working agent across every worktree with click-to-focus-exact-pane (`App.tsx:515-598`). Solves "which agent is waiting on me" in one glance.
9. **`gh` rate-limit-aware GitHub layer** — the github client uses `acquire()`/`release()` concurrency gating (`gh-utils.ts`) and AGENTS.md explicitly instructs batching. The `checkOrcaStarred`/`starOrca` loop also uses the user's existing `gh` auth — no OAuth, no PAT.
10. **Cross-platform hardening evident throughout** — WSL path translation (`src/main/wsl.ts`), Windows batch-script runner for setup hooks (`src/main/hooks.ts:333-380`), `CmdOrCtrl` accelerator policy enforced by AGENTS.md. Real multi-OS discipline, not just "we set `process.platform`".

## What This Tool Does Better Than Overcode

- **Built-in source control + PR merge flow**: Orca has Monaco-powered inline/split diff, stage/unstage hunks, and one-click squash/merge/rebase-merge via `gh pr merge` from inside the app. Overcode has no merge workflow, no diff viewer, no VCS panel.
- **Worktree isolation**: every agent gets its own working tree + branch by default, with auto-branch-create, auto-branch-delete, and configurable base-ref refresh. Overcode uses a shared repo.
- **Agent-agnostic**: Orca detects 8+ CLIs via OSC titles with zero config; Overcode is Claude-only.
- **Native desktop notifications with click-to-jump**: Electron `Notification` API with 5 s dedupe, focus-aware suppression, and click-to-activate-worktree. Overcode has no native notifications.
- **Design Mode (browser element grab)**: drop a clicked UI element (screenshot + selector) directly into the agent chat. Completely unique.
- **Built-in IDE editor**: Monaco editor + tiptap rich markdown + mermaid rendering inside the same app as the agent pane, so the user never switches context to VS Code.
- **SSH-native remote agents**: one sidebar, some worktrees local, others on a remote machine, with a shared PTY/git/fs relay. Overcode's "sister integration" is cross-machine monitoring, not full remote-PTY hosting.
- **`orca.yaml` as checked-in issue-command template**: repo-wide default prompt with per-user override, auto-gitignored. This is the same role Overcode's standing instructions play but project-scoped and version-controlled.
- **Codex account hot-swap**: genuinely novel — managed home dirs avoid re-login friction.
- **Shell-ready protocol**: robust startup-command injection; Overcode's tmux send-keys has historically had its own race classes.
- **Cross-platform (macOS + Windows + Linux)**: Overcode is macOS-leaning (tmux everywhere but Windows is a second-class citizen).
- **Custom terminal split layout engine** (no tmux): lower overhead, no tmux config skew, consistent UX.
- **Per-worktree browser + file explorer + search** in one window.

## What Overcode Does Better

- **LLM-based status detection and supervision**: Overcode's `HookStatusDetector` (reads Claude Code hook JSON state files) + regex polling (442 patterns) is authoritative for Claude. Orca's OSC-title detection works across 8 agents but can't tell *what* the agent is doing, only `working`/`idle`/`permission`.
- **Supervisor daemon with standing instructions (25 presets) + heartbeat**: Overcode has a Claude-powered supervisor that periodically nudges agents. Orca has nothing comparable.
- **Per-agent cost budgets with enforcement**: Overcode tracks $ spend per agent and can kill/soft-skip on budget. Orca shows raw token charts but no budget.
- **Agent hierarchy**: parent/child trees 5 levels deep with cascade-kill and fork-with-context. Orca worktrees are flat.
- **Web dashboard + HTTP API + analytics**: Overcode has a browser UI; Orca is desktop-only.
- **Session history archival + Parquet export**: Orca has neither.
- **Hook-based instant status**: Overcode consumes Claude Code's hook JSON state directly (`hook_status_detector.py`) — faster and more truthful than OSC scraping for Claude specifically.
- **Shared-repo model**: simpler mental model when the user doesn't want a worktree per feature (doc spelunking, quick fixes, shared test runs).
- **Fork with context**: clone an agent's conversation state into a new agent. Orca has no concept of agent conversation state.
- **Textual TUI = SSH-friendly**: Overcode runs inside a terminal, so it's usable from a thin client or `ssh` alone. Orca needs a GUI / X-forwarding / an Electron install.
- **Token accounting that matches compact/subagent flows** (recent commit `4f9be06`): Overcode dedupes subagent tokens out of parent totals; Orca's usage scanner is per-session-file only.
- **Timeline view + configurable columns + 50+ keybindings**: Orca exposes ~30 keyboard shortcuts and has no timeline.

## Ideas to Steal

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **Per-worktree/per-agent "comment" field as live progress note, plus a skill that tells agents to update it on checkpoints** | High | Low | Implements a cheap, user-visible "what is agent X doing right now" column without polling or LLM. Orca: `src/shared/types.ts WorktreeMeta.comment`, `skills/orca-cli/SKILL.md`. Overcode already has standing instructions — add a dedicated `comment` field to `Session` and a preset that tells Claude to use an `overcode comment` CLI. |
| **`orca.yaml` repo-checked-in `issueCommand` template with per-user `.orca/issue-command` override (auto-gitignored)** | High | Low | Project-scoped agent prompts. Avoids every user maintaining their own snippets. See `src/main/hooks.ts:160-262`. Maps cleanly onto Overcode's standing-instruction presets. |
| **OSC 133;A shell-ready protocol for startup-command injection** | High | Medium | Kills dropped-characters races when sending an initial prompt. Overcode currently relies on tmux send-keys + delays. Orca's implementation (`src/main/providers/local-pty-shell-ready.ts`) is portable to any PTY — write a wrapper `.zshrc`/`.bashrc` in `~/.overcode/shell-ready/`, launch the PTY with `ZDOTDIR` pointing at it, and block startup-command writes until the marker arrives. |
| **Titlebar "active agents" global popover with click-to-focus-exact-pane** | High | Low | One hovercard lists every working agent across every session with direct activation. Overcode's session list shows status but the user still has to hunt. See `App.tsx:515-598`. |
| **Native desktop notifications with 5-second per-session dedupe, focus-aware suppression, and click-to-activate** | High | Low-Med | Electron has `Notification`; Overcode is Textual but `osascript -e 'display notification …'` / `notify-send` via a small daemon process can do the same. Critical UX win for unattended runs. Reference: `src/main/ipc/notifications.ts`. |
| **`overcode` CLI as agent-to-agent bus — `terminal send`, `terminal read`, `terminal wait --for exit`, `worktree create --comment`** | High | Medium | Lets one agent spawn a peer and poll it. Orca's command surface (`src/cli/index.ts:42-173`) is a good template — uniform selectors (`id:`/`name:`/`path:`/`active`), `--json` everywhere, `--help` per subcommand. |
| **Codex/Claude account hot-swap via managed `$HOME` overlays** | Medium | Medium | Users who juggle multiple subscription tiers get instant switching without re-login. See `src/main/codex-accounts/service.ts`. |
| **Worktree-native isolation as an optional mode (not forced)** | Medium-High | High | Biggest structural gap. Add a `session.worktree` optional field that makes Overcode `git worktree add` a branch on launch. Gate behind a per-session toggle so the shared-repo default stays. Orca shows it's tractable: `src/main/git/worktree.ts` is ~210 LOC. |
| **`orca.yaml` `setup`/`archive` lifecycle hooks with `run-by-default`/`ask`/`never` policy and a per-worktree "Run setup?" dialog** | Medium | Low-Med | Useful for `npm install`/`uv sync` per worktree. Matches Overcode's per-session launch story. See `src/main/hooks.ts:264-305`. |
| **`orca worktree ps` — dense one-line-per-worktree status output** | Medium | Low | Great for agents that need a quick "who's alive" check. Already how Orca agents peer-monitor. `src/cli/index.ts:677-690`. |
| **Per-repo color badge + configurable worktree card columns (`status/unread/ci/issue/pr/comment`)** | Low-Med | Low | Cheap polish. Overcode already has configurable columns — mirror Orca's compact card layout. |
| **Worktree `isUnread` flag that persists until user opens the worktree** | Medium | Low | Unlike a transient notification, this is durable across app restarts. `WorktreeMeta.isUnread`, `notifications.ts:92-119`. |

## Quality Checklist
- [x] Every section filled (used "Not supported" where absent)
- [x] All CLI commands listed (§17, §18)
- [x] All keyboard shortcuts listed (§10 table + `useTerminalShortcuts.ts`)
- [x] All config options listed (§12)
- [x] All agent states listed (§3, §5)
- [x] "Ideas to Steal" table has 12 entries
- [x] Claims backed by file paths and line numbers from `~/Code/orca`
- [x] Overcode comparison cells reference actual Overcode features from `candidates.md`
