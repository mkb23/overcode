# Overcode vs CCManager: Feature Bakeoff

## Overview

| | **CCManager** | **Overcode** |
|---|---|---|
| **Repo** | [kbwo/ccmanager](https://github.com/kbwo/ccmanager) | This project |
| **Language** | TypeScript (React/Ink, Bun runtime) | Python (Textual TUI) |
| **Stars** | ~940 | N/A (private) |
| **License** | MIT | Proprietary |
| **First Commit** | 2025-06-05 | 2025 |
| **Last Commit** | 2026-04-13 (active, v4.1.7) | Active |
| **Purpose** | Self-contained TUI for managing AI coding CLI sessions across git worktrees with per-tool state detection | Claude Code supervisor/monitor with instruction delivery, hierarchy, and budget management |

## Core Philosophy

CCManager is an **agent-agnostic worktree manager**: its mental model is "git worktree = workspace, session = one CLI process inside it." Each worktree can host multiple named sessions, each running one of eight supported CLIs (Claude Code, Gemini, Codex, Cursor Agent, Copilot CLI, Cline, OpenCode, Kimi). It is deliberately self-contained — no tmux, no daemon, no web server — with all state held in a single Ink (React for terminal) process that spawns PTYs via Bun and emulates them through `@xterm/headless` so it can parse screen contents to infer what each agent is doing.

The core workflow loop is: **pick a worktree → pick or create a session → attach to PTY → return to menu via Ctrl+E**. CCManager stays out of the agent's way once attached (the keystroke pass-through is nearly transparent) but uses its virtual terminal to poll each session every 100ms, classifying output as idle/busy/waiting/pending-auto-approval and optionally firing hooks or auto-approving. This is a "best parts of Claude Squad minus tmux" pitch: simpler install, richer menu status, safer auto-approval.

Compared to Overcode (Claude-only, shared-repo, supervisor-driven with budgets/hierarchy/web dashboard), CCManager is **narrower in scope but broader in agent support** — it won't send instructions, won't track costs, won't coordinate parent/child agents, but it will run Cline inside a devcontainer in a git worktree with an LLM-backed auto-approver and a per-state hook for Slack notifications.

## Feature Inventory

### 1. Agent Support

CCManager supports **eight AI CLI agents** via per-tool state detection strategies (`src/types/index.ts:16-24`):

| Assistant | Command | Strategy file |
|---|---|---|
| Claude Code (default) | `claude` | `src/services/stateDetector/claude.ts` |
| Gemini CLI | `gemini` | `gemini.ts` |
| Codex CLI | `codex` | `codex.ts` |
| Cursor Agent | `cursor-agent` | `cursor.ts` |
| GitHub Copilot CLI | `copilot` | `github-copilot.ts` |
| Cline CLI | `cline` | `cline.ts` |
| OpenCode | `opencode` | `opencode.ts` |
| Kimi CLI | `kimi` | `kimi.ts` |

- **Addition model**: Hardcoded registry. Each strategy extends `StateDetector` (`src/services/stateDetector/base.ts`) with bespoke regex/heuristic logic. Adding a new agent requires editing the TypeScript union type and adding a new file — there is no plugin API.
- **Claude-specific auto-injection** (`README.md:166-173`): CCManager silently appends `--teammate-mode in-process` to any `claude` invocation with the `claude` detector, to prevent Claude Code's agent-teams feature from conflicting with PTY-based session management.
- **Agent-agnostic**: Yes, but each agent requires hand-tuned pattern matching for state detection.

**Overcode comparison**: Claude Code only. No multi-agent support.

### 2. Agent Launching

- **Entry point**: `ccmanager` CLI (`src/cli.tsx`), no subcommands. TUI-driven launch only.
- **Creation flow**: Menu → select worktree → "New session" → (optional preset selector) → PTY spawns.
- **Inputs collected**: worktree path (implicit from selection), command preset (combines command + args + fallback args + detection strategy), optional session name. No prompt-at-launch field.
- **Pre-written prompts**: Not supported via UI. Users would type the prompt after PTY attaches.
- **Prompt delivery**: None; CCManager writes the raw stdin stream to the PTY from user keystrokes only. No `send-keys`-style injection of a pre-written initial prompt.
- **Presets** (`CommandPreset`, `src/types/index.ts:151-164`): Named configs with `command`, `args[]`, `fallbackArgs[]`, `detectionStrategy`. Supports "select preset on start" flag to show picker before every launch (`selectPresetOnStart`). Fallback cascade: primary args → fallback args → bare command.
- **Devcontainer launches**: If `--devc-up-command` / `--devc-exec-command` are provided, the preset command runs inside the container via `<exec> -- <preset-command>` (`docs/devcontainer.md:33-45`).

**Overcode comparison**: Overcode launches via CLI (`overcode launch`) or TUI, supports pre-written prompts from args or files, pushes the initial prompt via tmux send-keys. No preset cascade but supports fork-with-context.

### 3. Session/Agent Lifecycle

- **States** (`src/types/index.ts:10-14`): `idle`, `busy`, `waiting_input`, `pending_auto_approval`. Four total.
- **Persistence model**: Sessions live **only in the running CCManager process**. The `Session` object (`src/types/index.ts:36-67`) holds an in-process `IPty` process handle, xterm `Terminal` instance, and `SerializeAddon` for screen-buffer snapshots. There is no on-disk session state file.
- **Survives process restart?** No — exiting CCManager (SIGINT/SIGTERM) calls `globalSessionOrchestrator.destroyAllSessions()` (`src/cli.tsx:99-109`), killing every PTY.
- **Survives TUI restart?** No. Worktrees persist (git does that), but the running agents die.
- **Survives reboot?** No.
- **Resume/reattach**: Reattach happens **within the same process**: re-entering a session restores the serialized xterm buffer and replays scrollback up to `TERMINAL_RESTORE_SCROLLBACK_LINES` (200, from `src/services/sessionManager.ts`). For cross-process resume users rely on the underlying CLI's `--resume` flag (commonly set as preset args).
- **Cleanup**: SIGINT/SIGTERM destroys all sessions. Explicit "Kill Session" menu action destroys a single session. No graceful shutdown hook is fired on kill.

**Overcode comparison**: Overcode persists full session state in tmux (sessions survive TUI restart) + on-disk `Session` dataclass with JSON serialization. Agents survive reboot if tmux server persists. Resume is first-class.

### 4. Isolation Model

- **Primary isolation: git worktrees.** Each worktree is a distinct checkout; multiple sessions per worktree share the filesystem but are independent PTYs.
- **Branch management** (`src/services/worktreeService.ts:106-184`):
  - `resolveBranchReference`: local branch → single remote match → ambiguous-error prompt.
  - `AmbiguousBranchError` (`src/types/index.ts:316-330`): thrown when the branch exists on multiple remotes; user picks via `RemoteBranchSelector.tsx`.
  - Auto-directory pattern (e.g., `../{branch}`, `{project}-{branch}`) — filesystem-safe slugging of branch names.
  - `autoUseDefaultBranch` flag auto-selects the default branch as base.
- **Multiple agents per workspace**: Yes — multiple sessions can be attached to the same worktree, each a separate PTY. `sessionNumber` auto-increments per worktree and a user-assigned `sessionName` can override.
- **Merge workflow** (`worktreeService.ts`, `src/components/MergeWorktree.tsx`): UI action picks source + target, calls `git merge` with configurable `mergeArgs` (default `['--no-ff']`) or `git rebase` with `rebaseArgs`. No AI-assisted merge, no PR creation, no automated cleanup pipeline.
- **Sub-task / sub-worktree**: Not supported. Worktrees are flat; no parent/child relationships between worktrees or sessions.
- **Devcontainer isolation**: Optional — when devcontainer CLI flags are supplied, the agent runs inside the container while CCManager stays on the host (`docs/devcontainer.md`). This is the "safe `--dangerously-skip-permissions`" use case.

**Overcode comparison**: Overcode agents share a single repo without worktrees, relying on tmux isolation. Overcode does support hierarchical parent/child agents (5 levels deep) which CCManager does not.

### 5. Status Detection

- **Mechanism**: Polling. Each session runs a 100ms `setInterval` (`STATE_CHECK_INTERVAL_MS`) that reads the current xterm buffer (up to 300 visible lines across normal + alternate buffers) and runs the strategy's regex/heuristic logic.
- **No hooks integration**: CCManager does not listen to Claude Code hook events. It is purely screen-scraping, so detection of "stopped" vs "waiting" depends on pattern hits.
- **States detected**: `idle`, `busy`, `waiting_input`, `pending_auto_approval`.
- **Per-strategy detail**:
  - **Claude** (`claude.ts`): Spinner glyphs `✱✲✳`, token counters `\d+\s*tokens`, "esc to interrupt" / "ctrl+c to interrupt" → `busy`. Prompt boxes containing "Do you want"/"Would you like" with "esc to cancel" → `waiting_input`. **Idle debounced 1500ms** (`IDLE_DEBOUNCE_MS`) to avoid flicker during redraws. Also detects `N background task(s)` and `@name` team members in the top 3 lines.
  - **Gemini**: box-drawing `│` + "Apply/Allow/Do you want", "waiting for user confirmation".
  - **Codex**: "press enter to confirm or esc to cancel", "[y/n]", allow/yes/submit patterns.
  - **Cursor**: spinner `[⬡⬢]`, "(y) (enter)", "keep (n)", "ctrl+c to stop".
  - **GitHub Copilot**: "confirm with [key] enter", "│ do you want".
  - **Cline**: `[act|plan] mode` markers, "let cline use this tool", "cline is ready for your message".
  - **OpenCode**: "△ Permission required", "esc interrupt".
  - **Kimi**: "allow?/confirm?/approve?/proceed?", thinking/processing/generating.
- **Latency**: 100ms polling + 1500ms idle debounce for Claude. Waiting-state detection is near-instant on pattern hit.
- **Cost**: Zero API calls — detection is pure regex over terminal buffer contents. Only auto-approval invokes an LLM.

**Overcode comparison**: Overcode runs a 442-pattern regex polling layer **plus** Claude Code hook integration for instant, authoritative state transitions. CCManager has neither hooks nor the per-session cross-pattern taxonomy Overcode uses — but covers 8 agents vs Overcode's 1.

### 6. Autonomy & Auto-Approval

- **Auto Approval (experimental)**: Optional LLM-backed verifier that automatically answers "safe" confirmation prompts. Off by default.
- **Decision flow** (`src/services/autoApprovalVerifier.ts`, `docs/auto-approval.md`):
  1. Session transitions to `waiting_input` → state becomes `pending_auto_approval`.
  2. Terminal content (up to 300 lines) is sent to the verifier.
  3. **Hardcoded dangerous-command blocklist runs first** (regex patterns in `autoApprovalVerifier.ts:63-220`): blocks `rm -rf /`, `mkfs`, `dd of=`, `shred`, fork bombs, `sudo rm`, `sudo chmod 777`, `/dev/sd*` writes, credential exfiltration patterns. Flags `pathSensitive` (allows project-scoped `rm`) and `localhostExempt` (allows localhost network ops).
  4. If the blocklist is clear, the verifier (default: `claude --model haiku -p <prompt>`) is invoked and must return JSON `{"needsPermission": true|false, "reason"?: string}`.
  5. `needsPermission: false` → CCManager writes `\r` to the PTY (presses Enter).
  6. Any error, timeout, or non-zero exit → require manual approval (conservative fallback).
- **Interruptible**: Pressing any key while in `pending_auto_approval` cancels the verification via the session's `AbortController` (`SessionStateData.autoApprovalAbortController`).
- **Custom verifier**: `autoApproval.customCommand` replaces the default. Receives `DEFAULT_PROMPT` and `TERMINAL_OUTPUT` env vars; must output the JSON schema.
- **Timeout**: `autoApproval.timeout` (seconds), default `DEFAULT_TIMEOUT_SECONDS = 120` (`src/constants/autoApproval.ts`).
- **No permission/safety modes beyond this**: Agents otherwise run with whatever flags the preset specifies. CCManager does not wrap agents in its own sandbox (devcontainer is the only sandboxing story).

**Overcode comparison**: Overcode has no LLM-backed auto-approval. Its supervisor daemon can intervene via instruction delivery, but the hardcoded blocklist + Haiku-verifier pattern is a genuinely novel approach.

### 7. Supervision & Instruction Delivery

- **Send instructions to running agents**: **Not supported as a distinct feature.** The only way to push input is to attach to the session and type. There is no CLI command or UI action to inject a prompt into a running session without re-attaching.
- **Standing instructions / persistent directives**: Not supported.
- **Heartbeat / periodic instruction delivery**: Not supported.
- **Supervisor daemon / meta-agent**: Not supported. Every decision is either user-driven or handled by the auto-approval verifier (per prompt, not persistent).
- **Intervention history / logging**: `logger` utility (`src/utils/logger.ts`) writes to a log file, but there is no intervention-history concept.

**Overcode comparison**: This is an entire dimension CCManager does not occupy. Overcode's supervisor daemon, heartbeat, 25 standing-instruction presets, and intervention logging have no analogue here.

### 8. Cost & Budget Management

- **Token tracking**: **Not supported.** No field on `Session` for tokens/cost; no hook, no parser.
- **Cost calculation**: Not supported.
- **Per-agent budgets**: Not supported.
- **Budget enforcement**: N/A.
- **Cost display**: N/A. (Token counters that appear in the terminal are parsed only as a heuristic *signal of "busy"* — their values are not retained.)

**Overcode comparison**: Overcode has full per-agent cost budgets with soft enforcement, pricing-model-aware calculation, and aggregate cost display.

### 9. Agent Hierarchy & Coordination

- **Parent/child relationships**: Not supported.
- **Agent-to-agent communication**: Not supported.
- **Task decomposition**: Not supported.
- **Cascade operations** (cascade kill, cascade budget): Not supported. Killing a session kills only that PTY.
- **Follow/oversight modes**: Not supported.
- **Closest feature**: The status-icon `[Team:N]` tag (`src/constants/statusIcons.ts`) visually reports when Claude Code's internal `@teammate` agents are running inside a single session, but CCManager does not manage that hierarchy — it just displays the count.

**Overcode comparison**: Overcode has 5-deep parent/child trees, cascade kill, fork-with-context. Not comparable.

### 10. TUI / UI

- **Form factor**: Pure TUI, single process, full-screen, no detached panes.
- **Framework**: **Ink** (React for terminal, v6.6.0) + React 19.
- **Layout model**: Stack of screens. Main screens:
  - `Menu` (worktree + session list)
  - `Dashboard` (multi-project overview, shows session counts per project)
  - `Session` (full-screen PTY passthrough)
  - `Configuration` (settings UI)
  - `NewWorktree`, `DeleteWorktree`, `MergeWorktree`, `SessionRename`, `SessionActions`, `PresetSelector`, `RemoteBranchSelector`, `Confirmation`, `DeleteConfirmation`, `CustomCommandSummary`
- **Visible features**:
  - Worktree list with git status (`+10 -5`, `↑3 ↓1`) if `git config extensions.worktreeConfig true` is set (`docs/git-worktree-config.md`).
  - Session state icons: `● Busy` / `◐ Waiting` / `○ Idle` / `○ Pending Auto Approval`.
  - Background task tags: `[BG]` or `[BG:N]`. Team member tags: `[Team:N]`.
  - Menu action icons: `⊕` (new), `✎` (rename), `⇄` (merge), `✕` (delete/kill), `⌨` (shortcuts), `⏻` (exit).
  - Vi-style search: `/` filters lists via `useSearchMode` hook and `SearchableList` component.
- **Default keyboard shortcuts** (`src/types/index.ts:111-114`):
  - **Ctrl+E**: Return to menu from an attached session.
  - **Escape**: Cancel / go back in dialogs.
  - Shortcuts are **only 2 user-rebindable actions**. Everything else (menu number hotkeys 0–9, `/` for search, `B` for back in multi-project, arrow-key navigation, Enter for select) is hardcoded.
  - **Reserved** (`src/services/shortcutManager.ts`): Ctrl+C, Ctrl+D, Ctrl+[ cannot be rebound.
  - **Validation rule**: Shortcuts must include a modifier (Ctrl/Alt/Shift) except for `escape`.
- **Customization**: Shortcuts via UI or `~/.config/ccmanager/config.json`. No column customization, no sort customization beyond the `sortByLastSession` toggle. No themes.

**Overcode comparison**: Overcode has ~50+ TUI keybindings, a timeline view, configurable columns, and is Textual-based. CCManager's UI is simpler but cleaner; the "minimal, no-manual-needed" philosophy (README:44-46) is an explicit design goal.

### 11. Terminal Multiplexer Integration

- **Multiplexer**: **None.** This is CCManager's marquee differentiator vs Claude Squad (README:32-34).
- **Panes/windows**: PTYs are managed directly via Bun's built-in PTY spawn (`src/services/bunTerminal.ts`), fed into `@xterm/headless` virtual terminals for state detection and screen capture.
- **Layout calculation**: No splits/panes — only one session visible at a time via stack-based screen navigation.
- **Live output**: Yes, when attached to a session (full PTY passthrough).
- **Split/zoom/focus**: Not supported. You cannot see two sessions simultaneously.
- **Scrollback**: 5000-line xterm scrollback (`TERMINAL_SCROLLBACK_LINES`), 200-line restore replay on re-attach.

**Overcode comparison**: Overcode is tmux-native. CCManager takes the opposite bet — no tmux dependency. Both have tradeoffs: tmux means sessions survive TUI exit (Overcode advantage); no-tmux means simpler install, no second keyboard-shortcut layer (CCManager advantage).

### 12. Configuration

- **Config file format**: JSON.
- **Locations**:
  - **Global**: `~/.config/ccmanager/config.json` (Linux/macOS) or `%APPDATA%\ccmanager\config.json` (Windows).
  - **Project**: `.ccmanager.json` at git repo root (disabled in multi-project mode).
  - **Recent projects cache**: `~/.config/ccmanager/recent-projects.json`.
  - **Migration**: Legacy `shortcuts.json` is auto-migrated into `config.json` on first run.
- **Precedence**: Project overrides global (merged, not replaced).
- **Full config schema** (`ConfigurationData`, `src/types/index.ts:171-183`):

| Section | Key | Type | Default |
|---|---|---|---|
| `shortcuts` | `returnToMenu`, `cancel` | `{ctrl?, alt?, shift?, key}` | `{ctrl:true, key:'e'}`, `{key:'escape'}` |
| `worktree` | `autoDirectory` | bool | false |
| | `autoDirectoryPattern` | string | `'../{branch}'` |
| | `copySessionData` | bool | true |
| | `sortByLastSession` | bool | false |
| | `autoUseDefaultBranch` | bool | false |
| `statusHooks` | `idle`, `busy`, `waiting_input`, `pending_auto_approval` | `{command, enabled}` | unset |
| `worktreeHooks` | `pre_creation`, `post_creation` | `{command, enabled}` | unset |
| `commandPresets` | `presets[]`, `defaultPresetId`, `selectPresetOnStart` | — | single default preset |
| `mergeConfig` | `mergeArgs`, `rebaseArgs` | `string[]` | `['--no-ff']`, `[]` |
| `autoApproval` | `enabled`, `customCommand`, `timeout` | — | `false`, unset, 120s |

- **Environment variables**:
  - `CCMANAGER_MULTI_PROJECT_ROOT`: required when running `--multi-project`.
  - Hook env vars (passed to spawned hook commands): `CCMANAGER_WORKTREE_PATH`, `CCMANAGER_WORKTREE_BRANCH`, `CCMANAGER_GIT_ROOT`, `CCMANAGER_BASE_BRANCH`, `CCMANAGER_OLD_STATE`, `CCMANAGER_NEW_STATE`, `CCMANAGER_WORKTREE_DIR`, `CCMANAGER_SESSION_ID`.
- **Lifecycle hooks / event system**: Yes — see Hooks section below (#12).

**Overcode comparison**: Overcode's config is TOML + dataclass-based; similar per-project vs global split but richer knobs (supervisor, budgets, heartbeat, web).

**Lifecycle hooks (subsection of config)**:

| Hook | Trigger | Working dir | Failure behavior | Env vars |
|---|---|---|---|---|
| `worktreeHooks.pre_creation` | Before worktree is created | Git root | Non-zero exit **aborts creation** | `CCMANAGER_WORKTREE_PATH`, `_BRANCH`, `_GIT_ROOT`, `_BASE_BRANCH` |
| `worktreeHooks.post_creation` | After worktree succeeds | New worktree | Logged only, non-blocking | Same as pre_creation |
| `statusHooks.idle/busy/waiting_input/pending_auto_approval` | On state transition | Worktree | Logged only, async | `_OLD_STATE`, `_NEW_STATE`, `_WORKTREE_*`, `_SESSION_ID` |

Hooks run via `spawn(cmd, [], {cwd, shell: true})` in `src/utils/hookExecutor.ts`.

### 13. Web Dashboard / Remote Access

- **Web UI**: **Not supported.**
- **API endpoints**: **Not supported.**
- **Remote monitoring** (multi-machine): **Not supported.** CCManager is a local-only TUI.
- **Mobile-friendly**: N/A.

**Overcode comparison**: Overcode has a full web dashboard, HTTP API with analytics, and Sister integration for cross-machine monitoring. CCManager has none of this.

### 14. Git / VCS Integration

- **Branch management**: Worktree creation with branch+base branch picker, ambiguous-remote resolution, auto-directory naming, `autoUseDefaultBranch`.
- **Commit automation**: Not supported. CCManager does not commit on behalf of the agent.
- **PR creation**: Not supported.
- **Merge conflict resolution**: Not supported beyond invoking `git merge` — conflicts surface to the user in the session.
- **GitHub/GitLab integration**: Not supported.
- **Enhanced status** (opt-in, `docs/git-worktree-config.md`): When `git config extensions.worktreeConfig true` is set, CCManager displays per-worktree `+add -del`, `↑ahead ↓behind`, and parent-branch context in the menu via `useGitStatus` hook.
- **Submodule awareness** (`src/utils/gitUtils.ts:45-68`): Detects `.git/modules` paths and uses `git rev-parse --show-toplevel` to find the correct root, so worktree ops inside submodules work.

**Overcode comparison**: Overcode doesn't do worktrees at all, so most of CCManager's git integration is in a different category. Neither does PR creation out of the box.

### 15. Notifications & Attention

- **Built-in notifications**: **None.** No bundled desktop-notification code, no terminal bell, no sound.
- **Notification mechanism**: Delegated to `statusHooks`. The canonical example (`docs/status-hooks.md`) invokes `noti` or a Slack webhook on state change.
- **Attention prioritization**: Visual only — `◐ Waiting` icon in the menu list. No sort-by-needs-attention, no audible cue, no pinning.
- **Menu badge**: `[active/busy/waiting]` counts per project on multi-project dashboard.

**Overcode comparison**: Overcode also has no native desktop notifications but offers richer in-TUI attention signals (timeline, color-coded activity, supervisor flags).

### 16. Data & Analytics

- **Session history / archival**: Not supported. Sessions are in-process only; exiting destroys them.
- **Data export formats**: None.
- **Analytics / metrics dashboards**: None.
- **Presence / activity tracking**: Only live state per session — no historical log of state transitions (unless the user wires `statusHooks` to log).
- **Logger** (`src/utils/logger.ts`): Writes diagnostic logs but not session telemetry.

**Overcode comparison**: Overcode exports to Parquet, has historical analytics, and tracks per-session activity over time.

### 17. Extensibility

- **Plugin / hook system**: Only the shell-command hook system (worktree + status). No TypeScript plugin API, no in-process extension points.
- **MCP server support**: Not supported by CCManager itself. Individual agents (Claude, Gemini, etc.) retain their own MCP support; CCManager is just a process wrapper.
- **API for external tools**: None.
- **Custom agent definitions**: Not possible without editing TypeScript source — state-detection strategies are a closed enum (`StateDetectionStrategy` union type).
- **Custom verifier command**: Auto-approval verifier can be any shell command returning JSON (`autoApproval.customCommand`).

**Overcode comparison**: Overcode's extensibility story is also limited (Python-level; no plugin API) but its HTTP API provides an external surface CCManager lacks.

### 18. Developer Experience

- **Install**: `npm install -g ccmanager` or `npx ccmanager`. Prebuilt platform binaries published as optional deps (`ccmanager-darwin-arm64`, etc.). **No tmux** to configure — this is the stated first-run win.
- **First-run UX**: `ccmanager` detects no TTY and exits with a clear message; otherwise drops into menu. No onboarding wizard — the menu is the onboarding.
- **Documentation**: Strong. README covers every major feature with links to per-feature docs under `docs/`: `auto-approval.md`, `command-config.md`, `devcontainer.md`, `gemini-support.md`, `git-worktree-config.md`, `multi-project.md`, `project-config.md`, `status-hooks.md`, `worktree-auto-directory.md`, `worktree-hooks.md`, `effect-error-handling-migration.md`.
- **Tests / CI**: Vitest unit tests co-located with source (`*.test.ts`, `*.test.tsx`) using `ink-testing-library`. `prepublishOnly` pipeline runs lint + typecheck + test + build. Type-strict TypeScript (no `any`), ESLint + Prettier enforced.
- **Conventions** (`AGENTS.md`, `CLAUDE.md`): Conventional commits, PascalCase components, `logger` class instead of `console`, tests mirror folder structure.

**Overcode comparison**: Overcode ships ~1700 tests with a ~5 minute suite, Python/pytest, Textual-specific tooling. CCManager's install experience is arguably simpler (single npm package vs. Python + tmux + venv).

## Unique / Notable Features

1. **LLM-verified auto-approval with hardcoded blocklist** (`src/services/autoApprovalVerifier.ts`). Two-stage defense: regex blocklist of dangerous commands runs first (can't be bypassed by the LLM), then Haiku is asked to classify the prompt, then optional custom verifier command. Configurable timeout, interruptible via any keypress. Genuinely thoughtful design — the blocklist-before-LLM ordering is the kind of detail most auto-approval systems skip.

2. **Eight-agent state detection** (`src/services/stateDetector/`). Each CLI has its own hand-tuned strategy — Claude's spinner glyphs, Gemini's box-drawing chars, Cline's `[act|plan] mode` markers, OpenCode's `△ Permission required`. This is a library of forensic knowledge about how these tools render their UIs.

3. **Devcontainer-on-host management** (`docs/devcontainer.md`). CCManager stays on the host while the agent runs inside a container via `devcontainer exec -- <agent>`. This gives you network isolation + host-side notifications + safe `--dangerously-skip-permissions` in one package.

4. **Session data copying between worktrees** (`src/utils/claudeDir.ts`, README:176-208). On worktree creation, optionally copies `~/.claude/projects/<source>/` to `~/.claude/projects/<target>/` so a new branch inherits the Claude conversation history. Novel approach to "continue this conversation on a different branch."

5. **Command preset fallback cascade** (`src/types/index.ts:151-158`). `args` fails → try `fallbackArgs` → try bare command. Lets you define an aspirational command (`claude --resume --model opus`) with a graceful degradation path — useful when `--resume` fails because there's no session yet.

6. **Teammate-mode auto-injection** (`README.md:166-173`). Silently appends `--teammate-mode in-process` to Claude invocations to prevent conflict with Claude Code's agent teams. Small, invisible, but exactly the kind of environment-aware adjustment a wrapper should handle.

7. **xterm-headless for state detection** (`@xterm/headless` dependency). Every session gets a full virtual terminal emulator so the detector reads logical screen content, not raw bytes — this correctly handles cursor addressing, alternate screen buffers, and redraws. Most tmux-based competitors grep `capture-pane` output, which is noisier.

8. **Effect-ts for typed error handling** (`effect` dependency, `docs/effect-error-handling-migration.md`). `IWorktreeService.createWorktreeEffect` returns `Effect<Worktree, GitError | FileSystemError | ProcessError, never>`. Every failure mode is typed. Overkill for most TUIs; an interesting bet here.

9. **Vi-style search in lists** (`src/hooks/useSearchMode.ts`). Press `/`, type, Enter exits search while keeping filter, Esc clears. Consistent across worktree, project, and session lists.

10. **Multi-project session aggregation** (`src/services/globalSessionOrchestrator.ts`). One `GlobalSessionOrchestrator` manages a `SessionManager` per project, switching between projects without losing sessions. `getAllActiveSessions()` gives a cross-project view.

## Strengths Relative to Overcode

- **Agent-agnostic.** Eight CLIs supported with per-tool state detection. Overcode is Claude-only. If you want to run Cursor Agent in one worktree and Cline in another, CCManager is the only option.
- **No tmux dependency.** Single `npm install`, no second shortcut layer to fight with, no tmux session management quirks. For users who don't already live in tmux, this is a meaningful onboarding reduction.
- **LLM-verified auto-approval with a command blocklist.** Overcode has no auto-approval at all. The blocklist-before-LLM architecture is safer than most approaches.
- **Devcontainer integration as a first-class feature.** Host-side CCManager + containerized agent is a clean story for sandboxing. Overcode has no equivalent.
- **Session-data copying between worktrees.** Branching a conversation to a new worktree while keeping context is a genuine workflow enabler Overcode doesn't have.
- **Git worktree isolation.** Overcode shares a repo; CCManager gives each agent its own checkout. Fewer collisions when two agents touch the same files.
- **Per-CLI state detection strategies.** Claude vs Cline vs OpenCode all render prompts differently; CCManager's 8 hand-tuned strategies handle this, while Overcode's 442-pattern library is Claude-specific.
- **Pre/post-creation worktree hooks.** Ran `npm install` automatically on every new worktree? Covered. Overcode has no equivalent.

## Overcode's Relative Strengths

- **Status detection via Claude Code hooks.** CCManager polls screen contents every 100ms and debounces idle over 1500ms. Overcode's `HookStatusDetector` (`overcode/hook_status_detector.py`) reads authoritative state transitions from Claude Code hooks as they happen — no polling latency, no pattern-miss risk, no debounce needed.
- **Supervisor daemon + instruction delivery.** Overcode can push prompts into running agents, run a heartbeat, apply 25 standing-instruction presets, and run a Claude-powered supervisor. CCManager has none of this.
- **Per-agent cost budgets.** Overcode tracks token usage and enforces per-session budgets with soft kill. CCManager does not read the token counts it parses as heuristics.
- **Agent hierarchy.** Parent/child trees 5 levels deep, cascade kill, fork-with-context. CCManager has flat sessions with no relationships.
- **Persistence across restarts.** Overcode sessions live in tmux + on disk; they survive TUI exit and often reboot. CCManager kills every PTY on SIGINT.
- **Web dashboard + HTTP API.** Remote monitoring, analytics, cross-machine via Sister. CCManager is local TUI only.
- **Data export (Parquet) + analytics.** Historical session data for post-hoc analysis. CCManager stores nothing between runs.
- **Richer TUI** with ~50+ keybindings, timeline view, and configurable columns vs CCManager's 2 rebindable shortcuts.

## Adoption Candidates

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **LLM-verified auto-approval with hardcoded dangerous-command blocklist** | High | Medium | Two-stage: regex blocklist (`autoApprovalVerifier.ts:63-220`) runs first, then Haiku classifies. Overcode could add this as an optional supervisor action. The blocklist-before-LLM ordering is the key insight — don't trust the LLM for lethal commands. |
| **Multi-agent support with per-agent state detectors** | High | High | CCManager's `stateDetector/` strategy pattern is the cleanest way to support more than Claude Code. Overcode's regex library could be generalised into a `StatusDetectorProtocol` per agent. Aligns with the existing `protocols.py` pattern (see `memory/MEMORY.md`). |
| **Session data copying between worktrees / forks** | Med | Low | When forking an agent, optionally copy `~/.claude/projects/<source>/` into the child's context dir. Overcode already has fork-with-context; this would extend it to copy Claude Code's persistent conversation files. |
| **Devcontainer-as-isolation mode** | Med | Med | For `--dangerously-skip-permissions` and sandboxing. CCManager's `--devc-up-command`/`--devc-exec-command` pattern is clean: two CLI flags, preset command appended after `--`. Maps directly onto Overcode's tmux-command construction. |
| **Command preset fallback cascade** | Med | Low | Primary args → fallback args → bare command. Useful for `--resume` (which fails on first launch). A couple of lines in Overcode's `launch` path. |
| **Pre/post worktree hooks with typed env vars** | Med | Low | Even without worktree support, Overcode could expose pre/post-launch hooks (`OVERCODE_SESSION_ID`, `OVERCODE_WORKTREE_PATH`, etc.) for users who want to run `npm install` or set up env per session. |
| **Teammate-mode auto-injection** | Low | Low | Overcode should consider auto-appending `--teammate-mode in-process` when spawning Claude inside tmux to avoid conflicts with Claude Code agent teams (same root cause as CCManager's fix). |
| **xterm-headless-style virtual terminal for detection** | Low | High | Expensive to port to Python, but would give authoritative "logical screen content" for regex matching instead of tmux `capture-pane` noise. Probably not worth it given Overcode's hook-based detection is already better. |
| **Vi-style `/` search across list views** | Low | Low | Simple UX upgrade for Overcode's session list. Press `/`, type to filter, Esc clears. |
| **"No tmux required" distribution option** | Low | High | CCManager's biggest sales pitch. Probably not actionable for Overcode (tmux is foundational), but worth knowing it's a real market segment. |