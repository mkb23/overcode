# Overcode vs Claude Squad: Feature Bakeoff

## Overview

| | **Claude Squad** | **Overcode** |
|---|---|---|
| **Repo** | [smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad) | This project |
| **Language** | Go (Bubbletea/Lipgloss TUI) | Python (Textual TUI) |
| **Stars** | ~6,100 | N/A (private) |
| **License** | AGPL-3.0 | Proprietary |
| **First Commit** | 2025-03-09 | 2025 |
| **Last Commit** | 2026-03-28 (210 commits, very active) | Active |
| **Purpose** | Terminal app to manage multiple Claude Code / Codex / Gemini / Aider sessions in isolated git worktrees | Claude Code supervisor/monitor with instruction delivery, hierarchy, budgets, and dashboard |

## Core Philosophy

Claude Squad treats every agent as an **isolated worker on its own branch**. The unit of work is "an instance," which is a tuple of (tmux session + git worktree + branch). The mental model is closer to "spin up N parallel feature branches, each with its own terminal where Claude is doing the work, and at the end commit/push or merge." The TUI is a thin orchestration layer; the real work happens when the user attaches (`enter`/`o`) to a tmux session and interacts with Claude directly.

The workflow loop is: **(1)** press `n` to create an instance, name it; **(2)** Claude Squad auto-creates a git worktree under `~/.claude-squad/worktrees/<branch>_<nanos>` on a new branch `<user>/<title>`; **(3)** spawns a detached tmux session running `claude` (or whatever `--program` is configured) inside that worktree; **(4)** user attaches, prompts, observes diff in the side pane; **(5)** when satisfied, `p` to commit + push, or `c` to commit and pause (worktree removed, branch preserved).

The product's two opinionated bets are: **worktree isolation per agent** (no two agents ever conflict on disk) and **autoyes daemon** (a detached background process that runs autonomously, polling sessions and pressing enter on confirmation prompts so agents can finish overnight without human attention). Everything else — the diff pane, branch picker, profiles — is in service of these two bets.

## Feature Inventory

### 1. Agent Support
- **Supported agents**: Claude Code (default), Codex, Gemini CLI, Aider. Agent identity is detected purely by suffix-matching the `Program` string (`session/tmux/tmux.go:163-178`, `session/instance.go:338-345`): `ProgramClaude = "claude"`, `ProgramAider = "aider"`, `ProgramGemini = "gemini"` (no Codex constant — Codex inherits via the generic else-branch).
- **How new agents are added**: Hardcoded constants in `session/tmux/tmux.go:22-25` plus per-program prompt-detection strings in `HasUpdated()` (`tmux.go:243-249`). No plugin system. To add a new agent you'd modify the source. The `--program` flag accepts an arbitrary shell command, but only Claude/Aider/Gemini get prompt-aware autoyes behavior; everything else just runs as a black-box process.
- **Agent-agnostic?** Partially: any program will run in tmux (`-p "any-cmd"`), but autoyes detection, trust-prompt dismissal (`session/instance.go:CheckAndHandleTrustPrompt`), and prompt-presence heuristics are hardcoded per program.
- **Versioning/detection**: Resolves `claude` via shell alias resolution + PATH lookup at startup (`config/config.go:113-153`, sources `~/.zshrc` or `~/.bashrc` and runs `which claude`).

**Overcode comparison**: Overcode is Claude Code only — no Aider/Codex/Gemini. But its Claude Code integration is much deeper: hook-based status detection, supervisor daemon, heartbeats, cost tracking. Tradeoff: depth vs breadth.

### 2. Agent Launching
- **Creation**: TUI-only (`n` for plain, `N` for "with prompt"). No CLI subcommand to create an instance non-interactively. Limit of 10 instances per app (`app/app.go:24` `GlobalInstanceLimit = 10`).
- **Inputs**: Title (≤32 chars, `app/app.go:446-447`), optional initial prompt (with `N`), optional pre-existing branch via the branch picker overlay, optional profile (program) when multiple profiles configured (`config/config.go:30-83`).
- **Launch with pre-written prompt**: Yes, via `N` keybinding which opens a TextInputOverlay with branch picker (`ui/overlay/textInput.go`). Prompt is sent via `tmuxSession.SendKeys` + `TapEnter` after Claude is ready (`session/instance.go:573-592`).
- **From a file?** Not supported. Prompt is typed into the overlay.
- **Initial-prompt delivery**: PTY write to the tmux session (`session/instance.go:581`, `session/tmux/tmux.go:228-231`). 100ms sleep between text and Enter to avoid carriage-return-as-newline (`session/instance.go:586`).
- **Templates/presets**: **Profiles** — named program configurations in `config.json` (`config/config.go:30-46`). Switchable via ←/→ in the new-session overlay. Each profile is just a `{name, program}` shell command — no per-profile prompts/permissions/models.

**Overcode comparison**: Overcode launches via TUI hotkey (`n`), CLI (`overcode launch`), or HTTP API. Supports prompt-from-file, model selection, permission modes, custom system prompts, and 25 standing-instruction presets. Launch is more parameterized; Claude Squad's launch is dirt-simple by design.

### 3. Session/Agent Lifecycle
- **States** (`session/instance.go:17-28`): exactly four — `Running`, `Ready`, `Loading`, `Paused`.
  - `Running` — Claude is actively working (output changed since last poll).
  - `Ready` — Claude is idle, waiting for user input.
  - `Loading` — instance is being started (covers the gap between `n`/`N` and tmux-session-up).
  - `Paused` — worktree removed, branch preserved, tmux detached but session may persist.
- **Persistence**: JSON file via `config.LoadState()` / `state.SaveInstances()` (`config/state.go`, `session/storage.go`). Stored as `[]InstanceData` in `~/.claude-squad/state.json`. Saves on every successful start, every kill, and on quit.
- **Survives**: TUI restart — yes (instances reload, tmux sessions reattached via `tmuxSession.Restore()` `session/tmux/tmux.go:183-191`). Process restart — yes. Machine reboot — no for tmux (sessions die with tmux server) but worktree + branch survive; on restart, `FromInstanceData` for paused instances skips `Start(false)` and only re-binds.
- **Resume/reattach**: Three mechanisms — (a) TUI auto-reattaches on startup; (b) `enter`/`o` attaches the user's terminal to the tmux session; (c) `r` resumes a Paused instance (recreates worktree + restarts tmux, `session/instance.go:472-525`).
- **Cleanup on kill**: Confirmation modal → close tmux session (`tmux kill-session`) → remove worktree (`git worktree remove -f`) → delete branch (unless it's a pre-existing user branch, `session/git/worktree_ops.go:114-122`) → prune worktrees → delete from storage. `reset` subcommand wipes everything (`cmd.go:78-113`): all instances, all `claudesquad_*` tmux sessions, all worktrees, daemon PID file.

**Overcode comparison**: Overcode has many more states (~20+ via regex patterns + hooks: idle/working/awaiting-permission/compacting/etc.). State persistence is per-session SQLite-like records; supports machine reboot via tmux-resurrect-style reattachment and full event-history replay.

### 4. Isolation Model
- **Isolation primitive**: `git worktree` per instance. No containers, no chroot. Worktrees live in `~/.claude-squad/worktrees/<sanitized-branch>_<unix-nano-hex>` (`session/git/worktree.go:66-67`).
- **Branch management**: Auto-created with prefix `<lowercase-username>/` (`config/config.go:96-103`). Override via `branch_prefix` config key. Title appended → e.g. `mike/fix-auth-bug`. Sanitized via `sanitizeBranchName()` to handle e.g. Windows `DOMAIN\user`.
- **Pre-existing branch start**: Yes — `N` opens a branch picker that `git fetch`-es and lists local + remote branches (`app/app.go:614-619`, `git.SearchBranches`); selected branch has `isExistingBranch=true` and is *not* deleted on cleanup (`session/git/worktree_ops.go:114`).
- **Multiple agents share a workspace?** No — that's the entire point of the worktree model. There's no opt-out for shared workspace.
- **Merge workflow**: Manual. `p` action does `git add . && git commit -m "[claudesquad] update from '<title>' on <RFC822>" && git push -u origin <branch>` via `worktree_git.go`'s `PushChanges(commitMsg, true)`. There is **no PR-creation step** built in — user runs `gh pr create` themselves (the README lists `gh` as a prerequisite, suggesting future intent).
- **Sub-task / sub-worktree support**: Not supported. One instance = one worktree = one branch. No agent hierarchy, no fork.

**Overcode comparison**: Overcode runs all agents in the **shared** repo root — no isolation. This is the most fundamental architectural divergence. Claude Squad's model wins for parallel feature work that would conflict on disk; Overcode's model wins for monitoring and for cases where agents need to see each other's progress live.

### 5. Status Detection
- **Mechanism**: Polling. Every 500ms (`app/app.go:932`) or 1000ms in daemon mode (`config.DaemonPollInterval` default). Captures the entire tmux pane content with `tmux capture-pane -p -e -J`, hashes it (sha256), compares to previous hash to detect "updated" (`session/tmux/tmux.go:235-256`). No hooks, no LLM analysis.
- **Statuses detected**: only the four enum values above. There is no "compacting", "running tool", "needs permission" granularity — it's all "the screen changed" vs "Claude is showing a known prompt string."
- **Prompt detection** (per program, `tmux.go:243-249`):
  - Claude: substring `"No, and tell Claude what to do differently"`
  - Aider: substring `"(Y)es/(N)o/(D)on't ask again"`
  - Gemini: substring `"Yes, allow once"`
  - Codex / others: no prompt detection.
- **Trust-prompt handling** (`tmux.go:155-180`): Single-shot check for `"Do you trust the files in this folder?"` or `"new MCP server"` (Claude) and `"Open documentation url for more info"` (Gemini/Aider) — auto-presses Enter (or `D+Enter` for Gemini).
- **Latency**: 500ms typical (TUI poll), up to 1000ms in daemon. No instant signal.
- **Cost**: zero — pure local polling, no API calls, no tokens.

**Overcode comparison**: Overcode has 442 regex patterns for state detection plus first-class Claude Code hooks (instant, authoritative). Claude Squad's substring match is fragile to Claude Code prompt copy changes. Overcode also distinguishes ~20 finer-grained states.

### 6. Autonomy & Auto-Approval
- **Unattended operation**: Yes — flagship feature. `--autoyes` / `-y` flag (or `auto_yes: true` in config) launches a **detached background daemon** (`daemon/daemon.go:91-127`) that survives the TUI exiting.
- **Auto-accept mechanism**: When a prompt substring is detected, `instance.TapEnter()` writes `0x0D` to the PTY (`tmux.go:211-217`). Daemon polls every `daemon_poll_interval` ms (default 1000) and TapEnter on each detected prompt.
- **Risk assessment**: **None.** It blindly presses Enter on any detected prompt — there's no LLM check, no command preview, no allowlist/denylist. The README warns "[experimental]".
- **Permission/safety modes**: Just on/off via `auto_yes`. No granular "auto-approve reads but ask for writes," no per-tool gating, no rate limit.
- **Daemon lifecycle**: PID stored at `~/.claude-squad/daemon.pid`; main app kills daemon on launch and re-launches it on quit if autoyes is on (`cmd/cmd.go:62-72`).

**Overcode comparison**: Overcode's supervisor daemon is also Claude-powered with standing instructions (25 presets) — it can make judgment calls before approving, intervene, or send corrective messages. Claude Squad's daemon is a dumb keystroke automaton.

### 7. Supervision & Instruction Delivery
- **Send instructions to running agents**: Yes, but only by **attaching** (`enter`/`o`) to the tmux session and typing. The TUI doesn't expose a "send message to agent X" without attaching first. Initial prompts (via `N`) are sent programmatically once at launch.
- **Standing instructions / persistent directives**: Not supported.
- **Heartbeat / periodic instruction delivery**: Not supported.
- **Supervisor daemon / meta-agent**: The autoyes daemon exists but is not LLM-driven — it only TapEnters. There is no Claude-orchestrating-Claude.
- **Intervention history / logging**: No structured log of human interventions. Tmux pane scrollback (10000 lines, `tmux.go:133`) is the only record.

**Overcode comparison**: Overcode has heartbeat-based periodic instruction delivery, 25 standing-instruction presets, supervisor daemon with intervention history. This is one of Overcode's biggest lead areas.

### 8. Cost & Budget Management
- **Token tracking**: **Not supported.** No tokens read, no API stats consumed.
- **Cost calculation**: Not supported.
- **Per-agent budgets**: Not supported.
- **Cost display**: Not supported.

**Overcode comparison**: Overcode has per-agent cost budgets with soft enforcement, dollar/token tracking, and pricing-model awareness. Total feature gap.

### 9. Agent Hierarchy & Coordination
- **Parent/child relationships**: Not supported — flat list of instances only.
- **Agent-to-agent communication**: Not supported.
- **Task decomposition**: Not supported.
- **Cascade operations**: Not supported.
- **Follow/oversight modes**: The TUI lets you watch one agent's preview at a time (`tabbedWindow` shows the selected instance's pane). No multi-watch or pinning.

**Overcode comparison**: Overcode has 5-level deep parent/child trees, cascade kill/budget, fork-with-context. Total feature gap.

### 10. TUI / UI
- **Interface**: TUI (Bubbletea + Lipgloss). Plus a separate marketing/landing-page Next.js site under `web/` that is **not** a runtime dashboard — just a static landing page with install instructions (`web/src/app/page.tsx`).
- **Framework**: `github.com/charmbracelet/bubbletea v1.3.4`, `bubbles v0.20.0`, `lipgloss v1.0.0`.
- **Layout** (`app/app.go:156-181`): horizontal split — instance list (30% width) on the left; tabbed window (70% width) on the right with three tabs (Preview / Diff / Terminal). Bottom: menu (10% height) + 1-row error box. AltScreen mode + mouse-cell-motion enabled.
- **Visible UI features**:
  - Spinner (`MiniDot`) next to Loading instances
  - Live tmux pane preview (refreshed every 100ms via `previewTickMsg`)
  - Diff pane (added/removed counts + content; `git.DiffStats`)
  - Terminal pane — embedded PTY view of the agent (`ui/terminal.go`)
  - Menu auto-changes based on selected instance state and active tab
  - Error box with 3-second auto-clear (`app/app.go:958-969`)
  - Confirmation modals for destructive actions (kill, push)
  - Help overlays shown contextually after instance creation, attach, checkout
- **Keyboard shortcuts** (complete list, `keys/keys.go:34-52`):

| Key | Action |
|---|---|
| `n` | New session |
| `N` | New session with prompt + branch picker |
| `D` | Kill (delete) session — confirmation modal |
| `↑`/`k` | Move selection up |
| `↓`/`j` | Move selection down |
| `shift+↑` | Scroll preview/diff/terminal up |
| `shift+↓` | Scroll preview/diff/terminal down |
| `enter`/`o` | Attach to tmux session |
| `ctrl+q` | Detach from attached session |
| `tab` | Cycle Preview / Diff / Terminal tabs |
| `c` | Checkout — commit + pause |
| `r` | Resume paused session |
| `p` | Push branch — commit + `git push -u origin` |
| `?` | Help overlay |
| `q` | Quit |
| `ctrl+c` | Cancel/quit (context-dependent) |
| `esc` | Exit scroll mode |
| `←`/`→` | (in new-session overlay) cycle profiles |
| Mouse wheel | Scroll preview/diff |

- **Customization**: Theming via Lipgloss adaptive colors only (light/dark). No user-configurable columns, sort order, themes, or column visibility.

**Overcode comparison**: Overcode has ~50+ TUI keybindings, configurable columns, sort, timeline view, multiple modal dialogs, agent hierarchy tree view. Claude Squad's UI is leaner and more focused.

### 11. Terminal Multiplexer Integration
- **Multiplexer**: tmux only. Hardcoded — no zellij/screen alternative. tmux is a hard prerequisite (README "Prerequisites").
- **Pane management**: One detached tmux session per instance, named `claudesquad_<sanitized-title>` (`session/tmux/tmux.go:60-68`). PTY (`creack/pty`) attached to a `tmux attach-session` process for live read.
- **Layout calculation**: Manual — TUI sizes the preview pane via `pty.Setsize`, tmux follows.
- **Live agent output**: Yes — the preview tab continuously captures pane content via `tmux capture-pane -p -e -J` (preserves ANSI colors, joins wrapped lines).
- **Split/zoom/focus**: No tmux-native splits; the entire instance lives in one pane. The TUI's "split" is between the list and preview, not between agents.
- **Scrollback**: `history-limit 10000` set per session (`tmux.go:133-135`). Mouse scrolling enabled (`tmux.go:139-141`). Full history viewable via the Terminal tab (`tmux.go:CapturePaneContentWithOptions("-", "-")`).

**Overcode comparison**: Overcode also uses tmux but is exploring zellij (per recent commit `bcfb3a6`). Both are tmux-locked today.

### 12. Configuration
- **Config file**: `~/.claude-squad/config.json` (`config/config.go:15-27`). Auto-created with defaults on first run.
- **Per-project vs global**: Global only. No per-repo config file. (Worktrees are per-repo because `cs` must be invoked from within a git repo, but config is shared.)
- **State file**: `~/.claude-squad/state.json` for persisted instance data, plus `daemon.pid` for daemon process tracking.
- **All config keys** (`config/config.go:36-47`):
  - `default_program` — name of the program/profile to use (default: resolved `claude` path)
  - `auto_yes` — bool, enable autoyes daemon
  - `daemon_poll_interval` — int ms (default 1000)
  - `branch_prefix` — string (default `<username>/`)
  - `profiles` — array of `{name, program}` objects
- **App state** (`config/state.go`): tracks which help screens have been seen (so they're shown only once), persisted instance JSON, `seen_help_screens` bitmask, etc.
- **Environment variables**: `OPENAI_API_KEY` for Codex (per README); `SHELL` for command resolution. No CS-specific env vars.
- **Lifecycle hooks / event system**: Not supported. No way to register custom callbacks for instance creation, kill, push, etc.

**Overcode comparison**: Overcode has both per-project and global config, env-var overrides, and is moving toward more configurability. Both lack lifecycle hooks.

### 13. Web Dashboard / Remote Access
- **Web UI**: **Not supported as a runtime dashboard.** The `web/` directory is a Next.js marketing site (landing page with install instructions and demo video). No live agent state, no remote control.
- **API endpoints**: Not supported.
- **Remote monitoring**: Not supported.
- **Mobile-friendly**: N/A.

**Overcode comparison**: Overcode has a real web dashboard with HTTP API, analytics, and Sister cross-machine integration. Major Overcode advantage.

### 14. Git / VCS Integration
- **Branch management**: Auto-create per instance, naming `<branch_prefix><title>`, sanitized.
- **Commit automation**: Yes — `p` and `c` actions auto-commit with templated message `[claudesquad] update from '<title>' on <RFC822>` (`session/instance.go:428`, `app/app.go:721`).
- **PR creation**: **Not built in.** README lists `gh` as a prerequisite suggesting future plans, but currently the user runs `gh pr create` after `p`.
- **Merge conflict resolution**: Not supported. Worktree isolation prevents conflicts during work, but merging back is the user's problem.
- **GitHub/GitLab integration**: Pushes via `git push -u origin` only. No API calls to GitHub.
- **Branch picker**: Live `git branch --list` search with 150ms debounce (`app/app.go:860-881`); fetches remote branches when picker opens (`git.FetchBranches`).
- **Diff display**: Inline diff stats (added/removed line counts) plus full diff content in the Diff tab (`session/git/diff.go`).
- **Dirty checking**: `IsDirty()` before pause to decide whether to commit (`session/instance.go:423-435`).
- **Branch-checked-out check**: Prevents kill or resume if the branch is checked out elsewhere (`session/instance.go:481-486`, `app/app.go:687-694`).

**Overcode comparison**: Overcode has no worktree workflow at all — agents commit/push within the shared repo. Claude Squad's git integration is significantly richer for the worktree-isolated workflow.

### 15. Notifications & Attention
- **Alerts**: Visual only — TUI menu redraws + status spinners. No desktop notifications, no sound, no system beep.
- **Attention prioritization**: The instance list shows status icons (Running spinner / Ready / Paused) so the user can scan for "needs attention." No explicit sort-by-attention.

**Overcode comparison**: Both lack native desktop notifications.

### 16. Data & Analytics
- **Session history / archival**: Only the current state is persisted. Killed instances are deleted from `state.json` immediately. No archive of past instances, no event log.
- **Data export**: Not supported.
- **Analytics / metrics dashboards**: Not supported.
- **Presence / activity tracking**: Implicit only — the polling loop tracks "updated since last tick."

**Overcode comparison**: Overcode exports to Parquet, has analytics dashboards. Claude Squad has zero analytics.

### 17. Extensibility
- **Plugin / hook system**: Not supported.
- **MCP server support**: Pass-through only — Claude itself handles MCP; Claude Squad just dismisses the "new MCP server" trust prompt (`tmux.go:165`).
- **API for external tools**: Not supported.
- **Custom agent definitions**: Via `--program` flag or `profiles` config — but only as raw shell commands. No structured agent definition.

**Overcode comparison**: Both are extensibility-poor, but Overcode has an HTTP API.

### 18. Developer Experience
- **Install**:
  - Homebrew: `brew install claude-squad` + manual symlink to `cs`
  - One-line installer: `curl -fsSL .../install.sh | bash` (puts binary in `~/.local/bin`, supports `--name` for custom binary name)
- **First-run experience**: Help screens auto-displayed first time for: general help, instance start, instance attach, instance checkout. Tracked via bitmask in `app_state.json` so they show only once.
- **Documentation**: README is concise and complete for the happy path. Inline help screens cover key flows. No separate docs site beyond the GitHub Pages landing page (smtg-ai.github.io/claude-squad). No reference docs for config schema.
- **Test coverage**: Unit tests for tmux (`session/tmux/tmux_test.go`), preview (`ui/preview_test.go`), terminal (`ui/terminal_test.go`), config (`config/config_test.go`), git (`session/git/util_test.go`), app (`app/app_test.go`). CI via GitHub Actions (`.github/workflows/build.yml` per README badge).
- **CLI subcommands** (`cmd/cmd.go`): `cs` (run TUI), `cs reset` (wipe everything), `cs debug` (print config path + JSON), `cs version`, `cs completion <shell>`, `cs help`. Hidden flag: `--daemon` (internal autoyes daemon entry point).
- **Cross-platform**: Has `daemon_unix.go` and `daemon_windows.go`, `tmux_unix.go` and `tmux_windows.go`. Windows support is at least scaffolded.

**Overcode comparison**: Overcode is Python (pip install), has more CLI surface area (launch, kill, status, supervisor, etc.), more test coverage (~1700 tests).

## Unique / Notable Features

1. **Worktree-per-agent isolation** — the cleanest implementation of this pattern I've seen. Worktrees go to `~/.claude-squad/worktrees/<branch>_<nano>` so they're never inside the user's repo, can be safely deleted, and never pollute the project tree. Repo-root resolution and unique-suffix collision avoidance are handled in `resolveWorktreePaths` (`session/git/worktree.go:49-70`).

2. **Detached autoyes daemon** — the daemon survives the TUI exiting (`daemon/daemon.go:91-127`). `defer daemon.LaunchDaemon()` on TUI quit means agents keep working autonomously even after you close the terminal. PID file at `~/.claude-squad/daemon.pid` for tracking; daemon respawns cleanly on next TUI launch.

3. **Pause/Resume worktree lifecycle** (`session/instance.go:412-525`) — `c` (checkout) commits work locally with a templated message, removes the worktree, preserves the branch, and copies the branch name to the system clipboard. `r` (resume) recreates the worktree and reattaches the tmux session. Lets you free disk space and untangle conflicts without losing context.

4. **Profiles** (`config/config.go:30-83`) — named program configurations selectable in the new-session overlay with ←/→. Lets a user keep multiple agents (claude prod, claude beta with different flags, codex, aider, etc.) and pick at launch time.

5. **Branch-picker with fetch + debounced search** (`app/app.go:860-881`, `session/git/util.go`'s `SearchBranches`/`FetchBranches`) — when starting `N`, it kicks off a background `git fetch` so the picker shows up-to-date remote branches; typing filters with a 150ms debounce. Lets you start an agent on an existing PR branch trivially.

6. **Trust-prompt auto-dismiss** (`session/tmux/tmux.go:155-180`) — Claude Code's "Do you trust the files in this folder?" and "new MCP server" prompts are auto-dismissed on first detection. Removes a ceremony step every new instance would otherwise hit.

7. **Three-tab inspector** (Preview / Diff / Terminal) — Preview is read-only color-preserved tmux capture; Diff shows live `git diff` against base SHA with stats; Terminal is a fully interactive embedded PTY (`ui/terminal.go`) so you can actually type into the agent without leaving the TUI.

8. **Help screens shown once, tracked via bitmask** (`config/state.go`'s `seen_help_screens`) — contextual help for new-instance, attach, checkout flows shows only the first time. Good onboarding without permanent noise.

9. **Hard 10-instance limit** (`app/app.go:24`) — opinionated cap. Forces user to clean up before sprawling.

10. **Reset subcommand wipes everything cleanly** (`cmd/cmd.go:78-113`) — destroys all instances, kills all `claudesquad_*` tmux sessions, cleans worktrees, kills daemon. A real "factory reset" rather than asking the user to clean up by hand.

## What This Tool Does Better Than Overcode

- **Worktree-per-agent isolation.** Overcode runs every agent in the shared repo root; two Overcode agents touching the same files race. Claude Squad never has this problem. For parallel feature-branch work, this is a structural win.
- **Built-in pause/resume with branch preservation.** `c` does commit + worktree-remove + clipboard-copy-branch in one keystroke. Overcode has no equivalent — you'd manually `git stash`/`git commit` and kill the agent.
- **Detached autoyes daemon that survives TUI exit.** Overcode's supervisor is more sophisticated but requires the supervising process to keep running. Claude Squad's daemon decouples agent autonomy from TUI uptime — close your laptop's tmux session, agents keep going.
- **Multi-agent support out of the box** (Claude / Codex / Gemini / Aider). Overcode is Claude-only.
- **Profiles for one-keystroke agent selection.** Overcode requires CLI args or config edits to switch models/programs.
- **Branch picker with live remote fetch.** Overcode has no equivalent — starting an agent on an existing PR branch is manual.
- **Embedded interactive Terminal tab.** Overcode's preview is read-only; Claude Squad lets you type into the agent without `tmux attach`.
- **Single binary, Go.** Faster startup, no Python deps, easier brew install. Overcode's Python stack is heavier.
- **Trust-prompt auto-dismiss.** Saves a ceremonial keystroke every new instance.
- **Help-once onboarding.** Cleaner first-run UX than Overcode's "all keybindings, all the time."

## What Overcode Does Better

- **Hook-based status detection** — instant, authoritative, ~20+ states vs Claude Squad's polled 4-state hash-diff. Less fragile to Claude Code prompt-text changes.
- **Supervisor daemon is Claude-powered** with 25 standing-instruction presets and judgment-driven approval. Claude Squad's daemon blindly TapEnters.
- **Heartbeat / periodic instruction delivery** to running agents. Claude Squad has no out-of-band instruction channel — you must attach.
- **Cost & token tracking** with per-agent budgets and soft enforcement. Claude Squad has none.
- **Agent hierarchy** (5 levels deep, parent/child trees, cascade kill, fork-with-context). Claude Squad is flat.
- **Web dashboard + HTTP API + analytics + Sister cross-machine integration.** Claude Squad has only a static marketing landing page.
- **Data export to Parquet** for downstream analysis. Claude Squad keeps no history.
- **No 10-instance hard cap** — Overcode scales further.
- **More keybindings, configurable columns, timeline view.** Richer power-user surface.
- **Far larger test suite** (~1700 tests vs Claude Squad's small suite).
- **Active session history / archival** beyond the live in-memory list.

## Ideas to Steal

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **Optional worktree mode per agent** | High | High | Add a per-agent flag at launch (`overcode launch --isolate`) that creates a worktree under `~/.overcode/worktrees/<branch>_<nano>` instead of running in the shared repo. Inherits Claude Squad's path scheme (worktrees outside the repo, unique nano suffix). Preserves Overcode's default shared-repo model for users who want it. |
| **Pause/Resume action** | High | Med | One keystroke that: commits dirty changes with a templated message, kills tmux, preserves branch, copies branch name to clipboard. Resume recreates session. Pairs naturally with worktree mode but works without it (just commit + kill + copy). |
| **Detached autonomy daemon** | High | Med | Today Overcode's supervisor dies with the TUI. A `--detach` mode that forks a background daemon (PID-tracked at `~/.overcode/daemon.pid`) so agents survive `cmd-w`. Reuses Overcode's existing supervisor logic; just needs process detachment + signal handling like `daemon/daemon_unix.go`. |
| **Profiles for one-key agent selection** | Med | Low | `~/.overcode/config.json: profiles: [{name, model, system_prompt, permissions}]`, picker overlay with ←/→. Lower friction than CLI args every launch. Generalizes to "preset configurations" beyond just program-name. |
| **Branch picker with live fetch + debounced search** | Med | Med | When launching, open an overlay listing local + remote branches (after a non-blocking `git fetch`), typed filter with 150ms debounce. Makes "start an agent on PR #234's branch" trivial. Helpful even without worktree mode. |
| **Trust-prompt auto-dismiss** | Low-Med | Low | Detect Claude Code's first-run trust prompts on agent startup and auto-Enter. Tiny code change, removes a ceremonial keystroke per new agent. (Overcode may already do this — verify.) |
| **`reset` subcommand** | Med | Low | One command that nukes all instances + tmux sessions + worktrees + daemon PID. Essential for a clean dev loop and bug repros. Inspired by `cs reset` (`cmd/cmd.go:78-113`). |
| **Help-once contextual onboarding** | Low | Low | Bitmask in app state of "screens already shown" — show new-instance / attach / first-launch help exactly once. Better than always-on or never-on docs. |
| **Embedded interactive Terminal tab** | Low-Med | Med | Add a TUI tab where the user can type directly into the selected agent's tmux pane without `tmux attach`. Overcode's preview is read-only today. |
| **Templated commit message on auto-commit** | Low | Low | When the supervisor or auto-actions need to commit, use `[overcode] update from '<title>' on <RFC822>` so the history is greppable. |
| **Hard cap with override** | Low | Low | Default soft-warn at 10 active agents (config-overridable). Forces hygiene without preventing power use. |
