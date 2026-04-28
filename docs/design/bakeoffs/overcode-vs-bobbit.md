# Overcode vs Bobbit: Feature Bakeoff

## Overview

| | **Bobbit** | **Overcode** |
|---|---|---|
| **Repo** | [SuuBro/bobbit](https://github.com/SuuBro/bobbit) | This project |
| **Language** | TypeScript (Node.js gateway + Lit web UI) | Python (Textual TUI) |
| **Stars** | N/A (small project, MIT) | N/A (private) |
| **License** | MIT (Copyright 2025 Joshua Subramaniam) | Proprietary |
| **First Commit** | 2026-04-21 (shallow clone horizon — project is older) | 2025 |
| **Last Commit** | 2026-04-28 `2d39a37` | Active |
| **Version** | 0.7.1 | 0.4.2 |
| **Form factor** | Gateway + browser UI (desktop/mobile/tablet, any device over LAN / NordVPN meshnet) | tmux-embedded Textual TUI |
| **Agent backend** | `@mariozechner/pi-coding-agent` (JSONL over stdin/stdout; not Claude Code) | Claude Code in tmux windows |
| **Purpose** | Browser-controlled AI dev team with goals, workflows, gates, roles, and team orchestration | Claude Code supervisor/monitor with instruction delivery |

## Core Philosophy

Bobbit positions itself as a **command centre for AI coding agents**, reachable from any device with a browser. The gateway (`src/server/`) runs locally, spawns `pi-coding-agent` child processes, and streams every file read, shell command, tool call, and edit to a Lit-based browser UI over WebSocket. The pitch is "full shell power of a terminal agent, but usable from your phone while you're away from the desk," with first-class support for multiple devices connecting to the same session.

The mental model is **projects → goals → tasks → sessions → gates**. A *project* is a registered directory. A *goal* is a unit of work with a spec, optional worktree, and optional *workflow* (a DAG of quality gates — design, implement, test, review — with enforced dependency ordering). Gates carry verification steps that run automatically on signal: shell commands, LLM review prompts, or agent-QA sessions that drive browser-based smoke tests. *Tasks* are dependency-aware work items within a goal. *Teams* are lead + role-agents (coder/reviewer/tester/custom) spawning into parallel git worktrees under a team-lead agent that partitions work to avoid file conflicts. *Roles* and *personalities* are composable prompt fragments with per-role tool-access policies (`allow`/`ask`/`never`). *Skills* are Claude-Code-compatible `SKILL.md` files auto-discovered from `.claude/skills/`.

Overcode is by contrast **supervision-first, Claude-Code-only, tmux-embedded**. It doesn't model goals, gates, teams, or quality workflows. It does model rich per-agent status (10+ states via 442 regex patterns + Claude Code hooks), standing instructions with heartbeats, supervisor-daemon-driven approval, per-agent cost budgets, 5-level parent/child hierarchies, and cross-machine aggregation via sister. Where Bobbit says "here is a polished multi-device product that orchestrates a process-quality-enforced dev team," Overcode says "here is a supervisor for a pile of Claude Code agents."

## Feature Inventory

### 1. Agent Support

- **Which AI CLI agents are supported?** Bobbit runs **`@mariozechner/pi-coding-agent`** (pi-ai stack) as its single underlying agent, communicating over JSONL on stdin/stdout via the `pi-agent-core` RPC layer. It is not Claude Code. Provider support is whatever `pi-ai` supports (Anthropic, OpenAI, Google, Ollama via `@lmstudio/sdk`/`ollama` SDKs — see `package.json` deps). Models are selected per session via `setModel` RPC, with review/naming model overrides configurable in project YAML.
- **How are new agents added?** Not a plugin system; the agent binary is resolved via `--agent-cli <path>`. Swapping agents means swapping which stdin/stdout JSONL process the gateway spawns. In practice Bobbit is married to pi-coding-agent.
- **Locked to one agent?** Yes — one RPC protocol (`rpc-bridge.ts`), one process shape. Unlike cmux or dmux, there is no multi-CLI fan-out.

**Overcode:** Claude Code only. Deeply integrated with Claude's hooks, JSON session logs, and pricing. Both tools are single-agent but bet on different stacks (pi-coding-agent vs Claude Code).

### 2. Agent Launching

- **How are agents created?** `POST /api/sessions` with `{ projectId|cwd, role?, personalities?, model?, assistantType?, goalId?, traits? }`. Also via WebSocket `start_session` command, sidebar "New Session" button, or Cmd/Ctrl-T (the global shortcut `new-session` in `src/app/main.ts`).
- **Inputs required:** `projectId` or a `cwd` matching a registered project's `rootPath` is required — there is no default-project fallback; a request with neither returns **400** (see [rest-api.md — Project resolution contract](https://github.com/SuuBro/bobbit/blob/master/docs/rest-api.md)). Everything else (role, personalities, model, worktree) is optional.
- **Launch with pre-written prompt?** Yes — the first user message after session creation is delivered via the WebSocket `user_message` command. The server also supports "continue-archived": `POST /api/sessions/:archivedId/continue` clones settings from an archived session and seeds the transcript as read-only context (see [internals.md — Continue-Archived sessions](https://github.com/SuuBro/bobbit/blob/master/docs/internals.md#continue-archived-sessions)).
- **Prompt delivery mechanism:** JSONL RPC to the pi-coding-agent subprocess via `rpc-bridge.ts` / `session-manager.ts`. Queued while the agent is busy (`prompt-queue.ts`); user can steer queued messages to the front, edit, remove, or drag-reorder.
- **Templates/presets?** Yes, at several layers:
  - **Roles** (`defaults/roles/*.yaml` + `.bobbit/config/roles/`): `architect`, `code-reviewer`, `coder`, `docs-writer`, `general`, `qa-tester`, `reviewer`, `security-reviewer`, `spec-auditor`, `team-lead`, `test-engineer`, `ux-designer`, plus `assistant/*` subtypes. Each has a prompt template, tool allowlist, accessory (mascot hat), and tool policies.
  - **Personalities** (`defaults/personalities/*.yaml`): `creative`, `critical`, `direct`, `explorative`, `pragmatic`, `quick-worker`, `rigid`, `terse`, `thorough`, `verbose`. Injected as prompt fragments; multiple per session.
  - **Workflows** (`defaults/workflows/*.yaml`): `bug-fix`, `feature`, `general`, `quick-fix`, `test-fast`. Declarative gate DAG snapshotted into a goal at creation.
  - **Assistant types** (`defaults/roles/assistant/`): `goal`, `role`, `tool`, `personality`, `staff`, `setup` — conversational wizards for creating the objects of their name.

**Overcode:** `overcode launch` CLI or `n` hotkey with prompt, model (haiku/sonnet/opus), permission mode, and optional parent. Standing-instruction presets (25) are the closest analogue, but Overcode has no equivalent to Bobbit's layered role + personality + workflow + goal stack, nor to role-based tool-access policies or assistant-type wizards.

### 3. Session/Agent Lifecycle

- **States an agent can be in:** `starting`, `preparing`, `idle`, `streaming`, `aborting`, `terminated` (`src/server/agent/session-manager.ts` — `SessionStatus` type, 6 states). Plus goal-level states (`todo`/`in-progress`/`complete`/`shelved`), task states (`todo`/`in-progress`/`complete`/`skipped`/`blocked`), gate states (`pending`/`passed`/`failed`), staff-agent states (`active`/`paused`/`retired`).
- **How are sessions persisted?** Session metadata (id, title, cwd, agent session file, `wasStreaming` flag) in `<project>/.bobbit/state/sessions.json`. Agent transcript in `.jsonl` files owned by pi-coding-agent. Gateway restarts re-spawn agents and call `switch_session` RPC to resume from the `.jsonl` ([features.md](https://github.com/SuuBro/bobbit/blob/master/docs/features.md#sessions)). Atomic write (`.tmp` + rename) for persistence integrity.
- **Survive restarts?** Yes. Gateway restart, browser close, network drop — all survive. If the agent was mid-turn when the gateway died, it is automatically re-prompted on restore.
- **Resume/reattach?** Multi-device: multiple browser tabs/devices connect to the same session simultaneously; events broadcast to all clients. `RemoteAgent` auto-reconnects with exponential backoff (1s base, 30s max), re-authenticates, and the server replays the latest `tool_execution_update` per tool-call ID from `EventBuffer`. Events carry `seq` + `ts`; reconnect sends `{type:"resume", fromSeq}`; on gap the client falls back to `get_messages` ([internals.md — Event stream ordering & dedup](https://github.com/SuuBro/bobbit/blob/master/docs/internals.md#event-stream-ordering--dedup)).
- **Cleanup on close/kill:** `DELETE /api/sessions/:id` terminates; worktree + branch removed by `cleanupWorktree()` (host) or `ProjectSandbox.removeWorktree()` (sandbox). Force-abort kicks in after 3s if graceful abort doesn't return to idle — the process is killed, a synthetic `agent_end` is emitted, queued/steered messages are recovered via `resetDispatched()` + `drainQueue()`, and a fresh agent is spawned ([prompt-queue.md — Abort and force-kill recovery](https://github.com/SuuBro/bobbit/blob/master/docs/prompt-queue.md#abort-and-force-kill-recovery)).
- **Archived sessions:** archived sessions can be reopened with fresh runtime via `POST /api/sessions/:archivedId/continue` — copies `projectId`, `modelProvider`, `modelId`, `role`, `personalities`, `sandboxed`, `worktreePath` presence but NOT cwd/branch/uncommitted changes/container identity. Seed transcript rendered to 128 KB (`full` mode) or summarised via naming model (`summary` mode), then injected under `## Prior Session Transcript` in the system prompt.

**Overcode:** persistent `Session` dataclass serialized to disk per-agent; 10+ states (running, waiting_user, waiting_approval, idle, error, done, sleeping, terminated, etc.); `overcode resume` reattaches to a Claude Code session by id. Bobbit's state model is coarser (6 session states) but spans goal/task/gate/staff states as well, which Overcode lacks entirely.

### 4. Isolation Model

- **How are agents isolated?** **Git worktree per non-assistant session**, by default. Regular host sessions land on `session/new-session-{uuid8}` branches; sandbox sessions on `session/s-{uuid8}`; goal sessions on `goal/<slug>`; team-agent sessions get per-agent branches within the goal. Assistant sessions (goal/project/tool/personality/staff wizards) do NOT get worktrees — they don't edit code ([internals.md — Session worktrees](https://github.com/SuuBro/bobbit/blob/master/docs/internals.md#session-worktrees)).
- **Docker sandbox:** optional second isolation layer. `sandbox: "docker"` in `project.yaml` runs each session inside a per-project Docker container (label `bobbit-project=<projectId>`) with scoped tokens, resource limits, and GITHUB_TOKEN passthrough for git auth. `project-sandbox.ts` / `sandbox-manager.ts` / `docker-args.ts`.
- **Branch management:** auto-generated slugs. Subdirectory projects (e.g. `/repo/packages/my-app`) create worktrees at the git repo root level but `cwd` offsets into the subdirectory — `path.relative(repoRoot, project.rootPath)` computed consistently across goal creation, executeWorktreeAsync, pool claims, and team member spawning.
- **Multiple agents share a workspace?** Yes — a team lead + N coder/reviewer/tester agents each get their own worktree within the same goal; the lead merges branches after agents push. Cross-agent file locking is avoided by workload partitioning at the task/file level (team-lead prompt: "Each task should touch a distinct set of files. Two agents should never edit the same file").
- **Merge workflow:** Team lead merges member branches via `git fetch origin && git merge origin/<member-branch>`. "All contributions go via pull requests. Never merge to master directly. ... Never push to master, merge into master, or force-push to any branch" ([`defaults/roles/team-lead.yaml`](https://github.com/SuuBro/bobbit/blob/master/defaults/roles/team-lead.yaml)). `POST /api/goals/:id/pr-merge` merges the goal PR. `POST /api/git-commit`, `/api/git-pull`, `/api/git-push` for session-level ops.
- **Sub-task / sub-worktree support:** Tasks can declare `dependsOn` relationships. Delegate tool (`delegate`) spawns a child session inheriting the parent cwd (no new worktree). No explicit sub-worktrees-of-worktrees like dmux has.
- **Worktree setup command:** `worktree_setup_command` in `project.yaml` runs in each new worktree with `SOURCE_REPO` env var (2-min timeout, non-fatal, `sh -c`). Staff agents re-run it on each wake cycle after rebasing onto primary.
- **Orphan worktree cleanup:** Settings → Maintenance tab lists orphaned `session/*` worktrees from ungraceful shutdowns; user manually cleans up. `GET /api/maintenance/orphaned-worktrees`, `POST /api/maintenance/cleanup-worktrees`.

**Overcode:** no worktree isolation (agents share the repo). This is a clear Bobbit win — it ships the worktree-per-session model as default, with an optional Docker second layer. Overcode lacks both.

### 5. Status Detection

- **How does Bobbit know what an agent is doing?** Direct RPC from the pi-coding-agent subprocess. Every tool call, tool result, message, cost update, streaming chunk, and state transition flows through a WebSocket `event_` stream sourced from the JSONL RPC protocol (`rpc-bridge.ts`, `ws/protocol.ts`, `ws/handler.ts`). There is no polling, no regex scraping, no LLM analysis — the agent emits structured events.
- **Polling? LLM analysis?** Neither for session state. LLM *is* used for title generation (Claude Haiku, "immediately" on first prompt), automatic verification (`llm-review` and `agent-qa` gate steps), and review-model overrides for code review. These are explicit LLM calls for specific features, not status inference.
- **Statuses detected:** the 6 `SessionStatus` values plus every tool-call phase (started/progress/complete), cost deltas (`cost_update` WS events), errors, compaction events (`compact` command + auto-triggered), streaming seq/ts. Granularity is per-event, not per-state-enum.
- **Latency:** near-zero — structured events pushed from the agent process through the gateway's WebSocket hub with `seq`/`ts` dedup.
- **Cost of detection:** free (no LLM calls for state). Token cost for title auto-generation and LLM-review verification is visible in per-session cost tracking.
- **Verification statuses:** gates have `running` / `passed` / `failed`; verification phases emit `gate_verification_phase_started`, step-level `gate_verification_step_started`/`_complete`, and final `gate_verification_complete` WS events.

**Overcode:** regex polling (442 patterns) + Claude Code hooks for authoritative instant transitions; richer status enum but requires Claude-specific log-file parsing. Bobbit's advantage is that owning the agent process gives it structured events for free; its disadvantage is that the whole design collapses if you can't run pi-coding-agent.

### 6. Autonomy & Auto-Approval

- **Can agents run unattended?** Yes, with role-based tool-access policies. Each tool group (shell, filesystem, browser, web, etc.) has a policy of `allow` / `ask` / `never` declared in `defaults/tool-group-policies.yaml` (overridable per project in `.bobbit/config/tool-group-policies.yaml` and per role in role YAML). `allow` runs without prompting; `ask` surfaces a confirmation dialog in the UI; `never` blocks the tool and surfaces it via `tool-guard-extension.ts` generated code.
- **Auto-accept / auto-approve:** Tool-group policies are the mechanism. Also per-session `always-allow` grants via the UI, persisted so a single agent can be given elevated trust without affecting siblings.
- **Risk assessment:** No LLM-based risk assessment before tool execution (unlike dmux's autopilot). The model is policy-based, not inference-based.
- **Permission/safety modes:** Per role via `toolPolicies` map in YAML. e.g. `team-lead` has `edit: always-allow`, `gate_list: always-allow`, `gate_signal: always-allow` etc. `coder` inherits defaults. Skills with `disable-model-invocation: true` or roles with `Skills: never` stay user-invocable only.
- **Blocking vs non-blocking tools:** Bobbit distinguishes two shapes. *Blocking* tools park a Promise keyed by `(sessionId, toolUseId)` until a later REST call resolves it (see [blocking-tools.md](https://github.com/SuuBro/bobbit/blob/master/docs/blocking-tools.md) — verification gates use this). *Non-blocking* tools like `ask_user_choices` end the agent's turn immediately and wake the agent when the user submits, so idle agents don't hold a slot ([non-blocking-ask.md](https://github.com/SuuBro/bobbit/blob/master/docs/non-blocking-ask.md)).

**Overcode:** supervisor daemon (Claude-powered) can auto-approve with standing instructions; per-agent permission modes (normal / permissive / bypass). The supervision axis is richer in Overcode; the tool-policy axis is richer in Bobbit.

### 7. Supervision & Instruction Delivery

- **Send instructions to running agents?** Yes — WebSocket `user_message` for normal input, `steer` for priority-interrupt. `SessionManager.deliverLiveSteer()` persists to `promptQueue` before calling `rpcClient.steer()` so a Stop-then-steer sequence doesn't lose the message ([internals.md](https://github.com/SuuBro/bobbit/blob/master/docs/internals.md)).
- **Standing instructions / persistent directives:** Via personalities (prompt fragments), roles (full prompt templates), AGENTS.md inline inclusion (with `@FILENAME.md` recursive/circular-safe expansion), and global/server/project `system-prompt.md`. Assembled per session into `.bobbit/state/session-prompts/{sessionId}.md`. However, these are all **at launch**; there is no Overcode-style "inject this directive into running agents every 5 min" heartbeat.
- **Heartbeat / periodic delivery:** Not supported at the session level. Staff agents have trigger-based wakes (`schedule` / `git` / `manual`) via `staff-trigger-engine.ts` — closest analogue, but at the agent level, not the instruction level.
- **Supervisor daemon / meta-agent:** Yes, sort of — the **team lead** is a first-class meta-agent with a dedicated role YAML, team-orchestration tools (`team_spawn`, `team_list`, `team_dismiss`, `team_complete`, task/gate management), and auto-nudge via TeamManager (`nudgePending` guard prevents flooding). Role and goal assistants are conversational wizards that help users define goals/roles/personalities via `propose_*` tools. These are narrower and more task-shaped than Overcode's supervisor daemon (which monitors ANY session and applies ANY standing instruction).
- **Intervention history / logging:** Full conversation history is the intervention log. Gate signals carry signal history (ID, timestamp, session). No separate "supervisor intervention trail."
- **Staff agents:** persistent recurring agents with their own worktrees, triggered on schedule / git event / manual. Wake cycle re-runs `worktree_setup_command`. Approximates "agents that run every night" — Overcode has no direct equivalent.

**Overcode wins on the supervision axis** (heartbeat, standing-instruction presets, oversight mode with stuck detection/timeout, intervention history). Bobbit wins on the **orchestration axis** (team lead spawning many parallel coders, workflow gates with enforced DAG, verification harness, staff agents).

### 8. Cost & Budget Management

- **Token tracking:** Per-session input/output/cache-read/cache-write tokens + total cost via `cost-tracker.ts`. Updated via `cost_update` WebSocket events. Persists to `<project>/.bobbit/state/costs/`.
- **Cost calculation:** Via `pi-ai` provider pricing (whatever pi-ai ships — Anthropic, OpenAI, Google, etc.). Review model override and naming model picker (`pickFallbackAigwNamingModel` — haiku→sonnet→opus tiers) keep secondary LLM calls cheap.
- **Per-agent budgets:** **Not supported.** No budget caps, no soft/hard enforcement, no transfer. Cost is display-only.
- **Budget enforcement:** Not supported.
- **Cost display:** Per-session via `GET /api/sessions/:id/cost`. **Aggregated** to goal level (`GET /api/goals/:id/cost`) and task level (`GET /api/tasks/:id/cost`). UI shows per-session cost in session cards and headers.

**Overcode:** tracks tokens, dollars, AND joules per agent with soft-enforced budgets, budget transfer between agents, and pricing per model. Bobbit has cleaner aggregation (goal/task roll-ups) but no enforcement. This is a meaningful gap on both sides — Overcode could adopt Bobbit's goal/task aggregation pattern, and Bobbit could consider the budget enforcement approach in return.

### 9. Agent Hierarchy & Coordination

- **Parent/child relationships:** `delegateOf` on sessions (one level of delegation from parent to child via the `delegate` tool). `teamGoalId` + `teamLeadSessionId` + role edges model a team structure. Goals own sessions (`goalId`). Not a recursive n-level tree like Overcode's 5-level parent/child; Bobbit's tree is mostly 2 levels deep (lead → members) or 1 level (session → delegate).
- **Agent-to-agent comms:** Team lead spawns agents via `team_spawn`, assigns tasks, and monitors `task_update` state transitions. Members push branches but DON'T merge; the team lead does. Gate signals and task-completion events auto-nudge the team lead (`TeamManager.nudgePending`). Cross-agent messages happen via the task/gate system, not direct message passing.
- **Task decomposition:** Team-lead prompt is explicit: "divide work into distinct, non-overlapping tasks and spawn many agents in parallel ... you can run up to 12 agents concurrently." Decomposition is model-driven (the lead decides), with hard constraints (no merge-to-master, PR-only, file-level partitioning).
- **Cascade operations:** Goal-level PR merge, goal-level git status, workflow gate cascade-reset (when a passed gate is re-signaled, all transitive downstream gates reset to `pending`). No cascade-kill or cascade-budget.
- **Follow / oversight modes:** Not a first-class concept. The browser UI inherently shows multiple sessions live (multi-device broadcast). The team-manager auto-nudge is the supervisor analogue.
- **Git handoff between agents:** Tasks carry `baseSha`, `headSha`, `branch` fields so a coder's output is a well-defined commit range the next agent in the chain can consume. Verification harness runs in the **goal's worktree** (the lead's branch), not the individual coder's, so the lead merges before signaling verification gates.

**Overcode:** 5-level parent/child trees, cascade kill, budget inheritance, fork-with-context, follow mode with stuck detection, oversight timeout. Bobbit's "team lead with explicit partition + merge-back" is a different and arguably better model for parallel *coding* specifically; Overcode's hierarchy is more general-purpose.

### 10. TUI / UI

- **Interface type:** Web (Lit + TypeScript), forked from `pi-web-ui`. No TUI, no native app, no CLI-only mode — the gateway serves an SPA at `http://localhost:3001/` (or over TLS to `yourname.dedyn.io:3001` via NordVPN meshnet for remote devices).
- **Framework:** Lit (web components), `@mariozechner/mini-lit` primitives, `lucide` icons, Tailwind 4 via Vite plugin. Playwright-driven tests for both `file://` fixtures and spawned-gateway E2E.
- **Layout model:** Desktop: **sidebar + chat panel + optional preview panel**. Sidebar groups sessions under collapsible project folders, always-visible. Mobile: **landing page with session cards** (no persistent sidebar), full-screen chat view, review-pane bottom-sheet for gate reviews ([review-pane-mobile.md](https://github.com/SuuBro/bobbit/blob/master/docs/review-pane-mobile.md)).
- **Key UI features:**
  - **Session sidebar** grouped by project, with goals + tasks + archived sessions. Always shows project folders (even with one project, defaults to expanded). Child auto-loading on click; orphan filtering for stale results.
  - **Chat panel** (`AgentInterface.ts`) with streaming message rendering, specialised tool-call renderers (`src/ui/tools/renderers/` — Write, Read, Preview, Bash, Proposal, ActivateSkill, etc.), scroll-lock invariant with user-intent detection (wheel/touchstart/keydown) and 5px stick-to-bottom tail.
  - **Git status widget** with 750ms-TTL single-flight cache, tri-state `gitRepoKnown`, 30s visibility-gated safety poll + retry ladder [0, 500, 2000, 5000]ms, yellow partial-dot warning, dropdown with untracked re-scan.
  - **Preview panel** for HTML/SVG/image/proposal previews — `.html` files render in a sandboxed iframe; `POST /api/preview` with per-session UUID-validated scoping, Vite `fs.deny` for `.bobbit` traversal prevention.
  - **Goal dashboard** showing gates with real-time phase progression, verification logs (lazy-loaded via `GET /api/sessions/:id/tool-content/:mi/:bi` for >32KB content).
  - **Review pane** with text-annotator integration (`@recogito/text-annotator`) for per-session review annotations that persist server-side (`.bobbit/state/review-annotations-{sessionId}.json`).
  - **Maintenance panel** for orphan worktree cleanup, search-index rebuild.
  - **Settings modal** (`#/settings` route) with General / Models / Config Directories / Shortcuts / Maintenance tabs.
  - **Bobbit mascot:** a squishy pixel-art blob rendered with CSS box-shadows (`src/ui/bobbit-sprite-data.ts`, `src/ui/bobbit-render.ts`, 580+ lines of [docs/bobbit-sprites.md](https://github.com/SuuBro/bobbit/blob/master/docs/bobbit-sprites.md)). Each session gets its own colour identity. Role accessories (crown for team-lead, magnifying-glass for reviewer, bandana for coder, etc.) show current activity at a glance.
  - **PWA installable** (with installed CA cert) for native-app-like launcher on iOS/Android/desktop.
- **Keyboard shortcuts** — data-driven registry (`src/app/shortcut-registry.ts`) with rebinding, persistence to `.bobbit/state/preferences.json`, conflict detection, browser-reserved combo blocking (Cmd+W/N/Tab/L/D/Q/R/P/F), input-focus guard with shadow-DOM traversal, and `allowInInput` flag:
  - `Cmd+T` / `Alt+N` — New session
  - `Cmd+Shift+T` / `Alt+Shift+N` — New session with options
  - `Cmd+/` — Focus message input
  - `Cmd+[` — Toggle sidebar
  - `Cmd+↑` / `Cmd+↓` — Prev/next session
  - `Cmd+]` — Collapse/expand preview panel
  - `Cmd+#` — Toggle fullscreen preview
  - `Alt+G` — New goal
  - `Cmd+Shift+D` — Terminate session
  - `Cmd+,` — Settings
  - Plus per-modal shortcuts (Esc to close, Enter to submit, `/` to focus autocomplete, etc.) registered locally via `document.addEventListener("keydown")` in each component.
- **Customization:** Shortcuts fully rebindable via Settings → Shortcuts tab. Session colours auto-assigned from `ColorStore`. Accent colours per project. Text zoom via `Cmd+=/-/0`. Mobile vs desktop layouts auto-switched by viewport.

**Overcode:** full-screen Textual dashboard, ~50+ keybindings, configurable columns, 4 sort modes, timeline view with color-coded status history bars, command bar with history. Bobbit's web UI has much higher visual fidelity and multi-device reach; Overcode's TUI has much higher information density per screen.

### 11. Terminal Multiplexer Integration

- **Which multiplexer?** **None.** Bobbit does not use tmux, zellij, screen, or any terminal multiplexer. The agent is a pi-coding-agent subprocess; its output is parsed as JSONL and rendered as structured messages/tool-calls in the browser, not as terminal output.
- **Panes/windows managed:** Browser panels — chat + preview + sidebar. No concept of terminal panes.
- **Layout calculation:** Flex/grid in Lit components. Preview panel collapse/expand/fullscreen states.
- **Live agent output:** Yes — streamed via WebSocket to the browser with specialised per-tool renderers. You don't watch a terminal; you watch a tool-rendered view of tool calls (e.g. `BashRenderer` shows command + output, `WriteRenderer` shows file path + diff, `PreviewRenderer` shows HTML iframe).
- **Split/zoom/focus:** Preview panel has fullscreen toggle (`Cmd+#`) and collapse toggle (`Cmd+]`). No terminal-style splitting.
- **tmux compatibility:** None — this is a philosophically different space from Overcode/cmux/dmux.

**Overcode:** runs inside tmux; agents are tmux windows. Bobbit moves off terminals entirely. Neither is "right"; they're different bets on form factor.

### 12. Configuration

- **Config files (3-tier cascade, `config-resolver.ts`):**
  - `~/.bobbit/` — global, lowest priority
  - `<server-cwd>/.bobbit/` — server-level, middle
  - `<project>/.bobbit/` — project-level, highest (wins)
- **Per-project structure:**
  - `<project>/.bobbit/config/` — roles, personalities, workflows, tools, `project.yaml`, `system-prompt.md`, `tool-group-policies.yaml`, `skills/`, `docs/`
  - `<project>/.bobbit/state/` — goals, sessions, tasks, team-state, gates, staff, search index, costs, session-prompts, review-annotations
- **Global state only** (`<server-cwd>/.bobbit/state/`): `projects.json` (project registry), `preferences.json`, `token` (auth), `gateway-url`, `colors.json`, TLS certs under `tls/`.
- **Key config options:**
  - `project.yaml`: `sandbox` (`docker` / unset), `worktree_setup_command`, `qa_start_command`, `qa_max_duration_minutes`, `typecheck_command`, `test_unit_command`, `config_directories` (additional skill/MCP scan dirs, additive to defaults).
  - `tool-group-policies.yaml`: per-group `allow`/`ask`/`never`.
  - Role YAMLs: `name`, `label`, `accessory`, `toolPolicies`, `promptTemplate` (with `{{AGENT_ID}}`, `{{GOAL_BRANCH}}` substitutions).
  - Workflow YAMLs: `id`, `name`, `description`, `gates[]` with `id`, `depends_on`, `content`, `inject_downstream`, `verify[]` (type: `command` / `llm-review` / `agent-qa`, `run` / `prompt`, `expect`, `timeout`, `phase`, `optional`, `label`, `description`).
  - `.mcp.json` (Claude Code-compatible) — auto-discovered; all MCP tools appear in Tools UI, system prompts, and role-based access control.
  - `.claude/skills/<name>/SKILL.md` — Claude Code skill parity (level-1 auto-expose, level-3 resource manifest for `references/`/`scripts/`/`assets/` subdirs).
  - AGENTS.md in session cwd with `@FILENAME.md` recursive inclusion.
- **Environment variables:**
  - `BOBBIT_E2E=1` — unlocks test-only endpoints (`?force=1` on last-project delete, `__setGitStatusFake` hooks, etc.).
  - `BOBBIT_ROOT` / home overrides for test isolation.
  - `SOURCE_REPO` — set in `worktree_setup_command` to point at the primary repo for `cp -r`/`npm ci --prefer-offline` patterns.
  - `GITHUB_TOKEN` — passed through to sandboxed containers for git auth.
- **CLI flags** (`bobbit [options]`): `--host`, `--port`, `--nord` (NordLynx mesh bind), `--tls`/`--no-tls`, `--cwd`, `--agent-cli`, `--static`, `--no-ui`, `--new-token`, `--show-token`.
- **Lifecycle hooks / event system:** No user-registerable workspace hooks. Agent-side hooks come from pi-coding-agent + MCP tools; server exposes WebSocket events as the primary integration surface.

**Overcode:** YAML global config; Claude Code hook integration; per-session config via supervisor. Bobbit's 3-tier cascade + per-project isolation + role/workflow/personality YAMLs + MCP auto-discovery is substantially more sophisticated than Overcode's config surface.

### 13. Web Dashboard / Remote Access

**This is Bobbit's core bet.** Unlike Overcode (TUI-first) or cmux (native-macOS-first), Bobbit is web-first. Every feature is reachable from a browser.

- **Web UI available?** Yes — it's the ONLY UI. Served by the gateway as static files from `dist/ui/` (or via Vite dev server on :5173 during development).
- **API endpoints?** Full REST + WebSocket. REST covers sessions (CRUD, cost, git-status, pr-status, output, tool-content, review annotations), goals (CRUD, commits, cost, pr-merge, retry-setup), gates (signal, cancel-verification, inspect, retry, history, content), tasks (CRUD, assignment, state transitions, cost), staff, projects, roles, personalities, workflows, tools, MCP, sandbox-status, maintenance, preferences, models, connection-info, CA cert. WebSocket covers live messaging, steer, abort, compact, queue management, model/role/personality changes, resume-with-seq. See [rest-api.md](https://github.com/SuuBro/gastown/blob/master/docs/rest-api.md) (623 lines) and [websocket-protocol.md](https://github.com/SuuBro/bobbit/blob/master/docs/websocket-protocol.md).
- **Remote monitoring (multi-machine):** No cross-machine aggregation. **But** single-machine remote access is first-class:
  - `--nord` binds to NordLynx mesh IP (NordVPN meshnet private IP)
  - `deSEC` dynDNS auto-updates on startup (`.bobbit/state/desec.json` → `yourname.dedyn.io`)
  - Bobbit CA (`.bobbit/state/tls/ca.crt`) + `mkcert` + `acme-client` generate TLS certs so remote devices trust HTTPS
  - Install CA on iOS/Android/Windows → full HTTPS trust, PWA installable
- **Multi-device:** Multiple browsers/devices connect to the same session concurrently. Events broadcast to all clients via the gateway hub. Works for laptop + phone + tablet simultaneously.
- **Mobile-friendly:** Yes — distinct mobile layout (landing page with session cards, bottom-sheet review pane). PWA installable. QR code sharing (`qrcode` npm dep) for onboarding new devices to the mesh.

**Overcode:** full HTTP API, Vue/analytics dashboard, sister integration for aggregating agents across N machines into one view, mobile-accessible web dashboard, cloud relay. Overcode wins on **cross-machine aggregation** (sister); Bobbit wins on **single-machine multi-device reach** (mesh + TLS + PWA + mobile-first layout).

### 14. Git / VCS Integration

- **Branch management:** Heavy. `detectPrimaryBranch(cwd)` via `git symbolic-ref refs/remotes/origin/HEAD` with `master`→`main` fallback. Bobbit uses `master` as its own primary branch by choice (not the Git default of `main`). Goal branches `goal/<slug>`, session branches `session/*`, team-agent per-member branches. Auto-generated slugs; baseline git fetch `origin <primary>` before each review.
- **Commit automation:** `POST /api/git-commit` (session worktree), `POST /api/git-pull`, `POST /api/git-push`. `gh` CLI used for PR operations.
- **PR creation:** `POST /api/goals/:id/pr-merge` with `{ method? }`. PR status cached via `pr-status-store.ts`, fetched with `gh pr view`. `pr-status` visible on sessions + goals.
- **Merge conflict resolution:** Team lead merges member branches; "If conflicts arise, it is your problem — fix them or spawn a coder to fix them" — no AI conflict resolver like dmux, it's delegated back to the orchestrator agent.
- **GitHub/GitLab integration:** GitHub via `gh` CLI (for PR status, PR merge). `GITHUB_TOKEN` propagated to sandbox containers. Goal-level PR workflow assumes GitHub.
- **Git status widget reliability:** `batchGitStatus` 750ms-TTL single-flight cache; Porcelain v1 with `-uno` default, `-uall` on dropdown open; 15s timeout with EAGAIN/ENOBUFS/EBUSY retry; never-cached errors; `runBatchGitStatus` spawn worker with per-container keys; `partial: true` response when untracked scan times out (Phase A succeeded, Phase B did not).
- **Verification baselines:** Gate verifications are baseline-aware — pre-impl gates skip diff entirely; impl+ gates diff against `origin/<primary>...HEAD`; `ready-to-merge` uses `git merge-base` against `origin/<primary>`. Why `origin/` prefix: local refs only as fresh as last `git pull`; goal worktrees created from `origin/<primary>` so verification must diff against the same anchor to avoid surfacing already-upstream commits as goal-unique.

**Overcode:** similar passive git awareness; no merge workflow; `sync to main` CLI command. Bobbit's git integration is dramatically deeper — goal-branch model, PR automation, baseline-aware verification, git-status widget with enterprise-grade reliability.

### 15. Notifications & Attention

- **Alert channels:**
  - **Browser Notification API** — showed with session title + elapsed time on agent turn completion.
  - **Title flash** — alternates document title with "Done (Xm)" until tab regains focus.
  - **Audio beep** — two-tone sine wave (880 Hz + 1046 Hz) via Web Audio API.
  - **In-app toast / pill** for queued messages (`queue_update` events), verification phases, gate passes/fails.
  - **Bobbit mascot animation** — each session's sprite animates + changes accessory based on role.
- **Attention prioritization:** Not modelled as a "jump to next unread" across sessions. Sidebar badges surface new-message counts per session.
- **Focus-stealing discipline:** Standard browser notification permission flow. Server is UI-agnostic; the browser decides whether notifications pop.
- **Per-role sounds / customisation:** Single audio beep; not per-role. No custom sound library.

**Overcode:** no native notifications at all — clear Overcode gap. Bobbit's notification UX is solid but narrower than cmux's (no "jump-to-unread", no per-role sounds, no OSC 9 pickup).

### 16. Data & Analytics

- **Session history / archival:** Sessions can be archived. Archived sessions render "Continue in New Session" button under their transcript (scope-gated: must not be goal/delegate/team/assistant session and project must still be registered). Transcript preserved in `.jsonl`; metadata in `sessions.json` with archived flag.
- **Data export formats:** Not supported as a feature. Raw JSONL is available on disk but there's no `export to CSV/Parquet` action.
- **Analytics / metrics dashboards:** Per-session cost panel; aggregate per goal + per task. No system-wide dashboard, no timeline-of-all-sessions view, no presence overlay.
- **Presence / activity tracking:** Not tracked.
- **Search:** FlexSearch lexical index at `<project>/.bobbit/state/search.flex/` with role-aware content weighting, 2-mode UX (quick + full), orphan filtering, re-index triggers, and `Maintenance` panel for manual rebuild. `ProjectContextManager.searchAll()` aggregates across registered projects (orphan filter applied post-merge).

**Overcode:** Parquet export for offline analysis, rich timeline view, per-agent cost/token/joule counters, presence tracking with idle/lock detection, cross-machine sister aggregation. Bobbit wins on search (cross-project + role-weighted); Overcode wins on analytics + export + presence.

### 17. Extensibility

- **Plugin / hook system:** Not a plugin API per se, but `defaults/` → `.bobbit/config/` override-everything cascade is effectively a plugin model. Roles, personalities, workflows, tool groups, tools, skills, and MCP servers are all YAML/Markdown-configurable.
- **MCP server support:** Yes, first-class. Drop `.mcp.json` (same schema as Claude Code) in project root → auto-discovered, auto-connected, tools appear in Tools UI with role-based access control applied. MCP discovery scans all registered projects so servers defined anywhere are available everywhere (primary project wins on name conflict).
- **API for external tools:** REST + WebSocket protocol are the integration surface. `POST /api/sessions/:id/wait` blocks until idle for external pollers. `GET /api/sessions/:id/output` returns final assistant output.
- **Custom agent definitions:** Custom roles via `.bobbit/config/roles/<name>.yaml`. Custom personalities, workflows, tools similarly. Custom skills via `.claude/skills/<name>/SKILL.md` or `.bobbit/skills/`.
- **Slash skills:** `/skill-name args` at word boundaries, prefix or inline. Autocomplete menu anchors to `/` character. Autonomous activation via `activate_skill` tool (level-1 progressive disclosure). Level-3 skills (with `references/`, `scripts/`, `assets/`) get a synthetic activation header + one-level-deep resource manifest on activation, then read on demand.

### 18. Developer Experience

- **Install:** `npx bobbit` (scaffolds `.bobbit/`, starts gateway on :3001, opens browser). Or `npm install -g bobbit`. Or `git clone + npm install + npm run build + npm start`. No native binaries, no postinstall network fetches, no runtime model downloads — airgap / corporate-network friendly.
- **Run-from-checkout:** `/path/to/bobbit/run` from any project dir; auto-installs deps + builds on first run; detects source changes for rebuild (`git pull && ./run` just works); per-project `.bobbit/`; port auto-increment for side-by-side instances.
- **First-run:** Scaffolds `.bobbit/` config, generates 256-bit auth token (`.bobbit/state/token`, mode 0600), opens browser.
- **Documentation:** 32 markdown files in `docs/` (~7.3k lines total). Deep: `internals.md` alone is 1510 lines covering multi-project architecture, tool policies, semantic search, MCP, sandbox, config cascade, disk state, workflows, goal re-attempt, event stream dedup, scroll lock invariants, snapshot merge invariant, etc. `debugging.md` is 381 lines of scannable symptom→cause checklists.
- **Test coverage / CI:** Unit tests (Node test runner + Playwright `file://` fixtures, <30s); API E2E (in-process gateway); browser E2E (spawned gateway + Playwright); manual integration (real agents + Docker, ~5 min). Multiple Playwright configs (`playwright-e2e.config.ts`, `-smoke`, `-standard`, `-coverage`, `-manual`, `-fullstack`). `c8` coverage reporting. `SCREENSHOTS=1` mode for visual test output. E2E coverage requirement: every user-facing feature must have navigation + happy-path + persistence-across-reload + cleanup/undo tests.
- **Dev server harness:** `npm run dev:harness` watches `.bobbit/state/gateway-restart`; `npm run restart-server` signals rebuild + restart; auto-restarts on crashes; sessions survive restarts.
- **Regression policy:** Testing-coverage doc spells out per-area coverage: prompt interactions, sidebar, sessions & resilience, sidebar child auto-loading.
- **Primary branch:** `master` (explicitly not `main`).

## Unique / Notable Features

1. **Goals + Workflows + Gates (DAG with enforced ordering).** A goal carries a snapshotted workflow (frozen at creation). The workflow is a DAG of gates, each with `dependsOn` edges, optional `content: true`/`inject_downstream: true` (upstream content auto-injected into downstream agent system prompts), and `verify[]` steps (command / llm-review / agent-qa) with `phase` ordering (phase 0 parallel-runs cheap checks, phase 1 runs LLM reviews only if phase 0 passes). Passed gates cascade-reset when re-signaled. Baseline-aware diff rules per gate kind. **No tool in the Overcode bakeoff catalogue has anything this structured.**
2. **Teams mode with team-lead role.** A single lead session spawns up to 12 coders/reviewers/testers into parallel worktrees, partitions work by file, merges branches locally, owns PR creation. Goal auto-starts teams when `autoStartTeam: true`. Agents handoff via `baseSha`/`headSha`/`branch` on tasks.
3. **Roles × personalities × workflows composition.** Behaviour is assembled from orthogonal YAML layers: role (what tools + prompt), personality (modifier fragments), workflow (which gates + verification), assistant type (wizard persona). Overcode has standing-instruction presets but no equivalent multi-axis composition.
4. **Tool-group access policies.** `allow` / `ask` / `never` per tool group, overridable per project, per role, per grant. Generated guard code via `tool-guard-extension.ts`. Blocking and non-blocking tool patterns with distinct semantics for human-in-loop.
5. **Multi-device web UI over NordVPN meshnet with trusted CA.** `--nord` flag + deSEC dynDNS + Bobbit CA + PWA install = laptop/phone/tablet access without cloud, without port forwarding, without security warnings. Serves its own CA cert at `GET /api/ca-cert`.
6. **Session worktree by default.** Every non-assistant session automatically gets its own branch/worktree (`session/<uuid8>`). Sandbox option layers Docker on top. Subdirectory-project support via `path.relative` offset.
7. **Git-status widget with production-grade reliability.** 750ms-TTL single-flight cache, tri-state repo-known, retry ladder, partial-response degrade, untracked dropdown re-scan, test-only fake hooks for deterministic E2E. Reflects a hard-learned lesson that the git widget disappearing on a blip re-orients the user wrongly.
8. **Staff agents with schedule/git/manual triggers.** Persistent agents with their own permanent worktrees, auto-rebased and setup-re-run on each wake. Closest analogue to "the nightly agent that checks for CVEs or broken builds."
9. **Skill parity with Claude Code.** `.claude/skills/` + `.claude/commands/` discovery, `/skill-name` autocomplete, autonomous activation via `activate_skill` tool, Level-3 progressive disclosure with resource manifest.
10. **Interactive non-blocking `ask_user_choices`.** Agent poses multiple-choice questions (up to 5 + optional "Other"), turn ends immediately, session goes idle until user submits, response arrives as a tagged user message that wakes the agent. A blocking-tool equivalent exists for cases where the agent must wait (verification gates).
11. **Continue-Archived sessions.** Reopen an archived transcript as a fresh session that inherits settings (model/role/personalities/sandbox/worktree-presence) but NOT runtime state (branch/cwd/container identity). Transcript becomes read-only seed context under `## Prior Session Transcript` in the new system prompt.
12. **Prompt queue with steer-to-front, drag-reorder, edit, remove.** Server-side queue of user messages while agent busy. Steered messages batch and reorder to front. `follow_up` flag preserved through the queue. Recovered across force-abort via `resetDispatched()` + `drainQueue()`.
13. **Event-stream dedup/reorder with seq+ts.** Reconnect sends `{type:"resume", fromSeq}`; `resume_gap` falls back to `get_messages`. Stale-messages-on-navigate bug fixed via snapshot-merge invariant (server snapshot authoritative; client-bucket entries with matching IDs dropped; stable-sort by timestamp + insertion order).
14. **Per-project state isolation.** Every project owns its own `.bobbit/config/` + `.bobbit/state/`. No shared session/goal/cost stores. Auth token + gateway URL + project registry + preferences are the only truly global state. Removing a project cleanly removes its state; pointing a different Bobbit instance at the project directory gives access to its history.
15. **Multi-project search (FlexSearch).** Cross-project lexical search with role-aware content weighting; orphan filtering keeps dead result clicks from showing deleted content; re-index triggers on goal/session mutations.
16. **Bobbit mascot.** Pixel-art sprite rendered entirely in CSS box-shadows, one colour identity per session, role-accessory signalling (crown = team-lead, magnifying-glass = reviewer, bandana = coder). 580 lines of docs for the sprite system alone.

## Strengths Relative to Overcode

- **Goals/workflows/gates as structured quality gates.** The entire concept of declarative DAG-ordered verification steps (`command` + `llm-review` + `agent-qa`) with phased execution, enforced signal ordering, cascade-reset on re-signal, and baseline-aware diffs is absent from Overcode. Standing instructions are a strictly weaker analogue.
- **Teams with a team-lead agent that partitions work and merges back.** Parallel coding with a human-in-loop-optional orchestrator that owns file-level conflict avoidance, branch merges, and PR creation. Overcode's 5-level hierarchy is more general but has no equivalent workflow.
- **Git worktree per session by default + optional Docker sandbox.** Overcode has neither.
- **Web UI reachable from any device on your mesh.** PWA installable on iOS/Android. Multiple devices can watch the same session concurrently. Overcode has a web dashboard but it's monitoring-only; Bobbit's is the primary control plane.
- **Mobile-first layout with distinct mobile and desktop UIs.** Session-card landing page, bottom-sheet review pane, text-annotator integration. Overcode's TUI is fundamentally desktop-only.
- **Roles × personalities × tool-policies composition.** Cleaner abstractions than Overcode's standing-instruction presets. `allow`/`ask`/`never` policies per tool group with per-role overrides is a practical safety model.
- **MCP server integration with Claude-Code-compatible `.mcp.json`.** Overcode doesn't support MCP at all.
- **Skill parity with Claude Code's `.claude/skills/`.** Slash-skill autocomplete + autonomous activation + Level-3 resource manifests. Overcode has no skill system.
- **Cost aggregation at goal and task level.** Overcode tracks per-agent but doesn't roll up to a work-item.
- **Git-status widget reliability engineering.** TTL cache, single-flight, tri-state, retry ladder, partial responses, test-hooks. A solved problem Overcode has not solved.
- **Prompt queue with steer / drag-reorder / edit / remove.** Overcode's command bar is rich but single-shot; queued messages are a better model for "I want to give the agent three follow-ups in a row while it's mid-turn."
- **Interactive `ask_user_choices` tool.** Structured multiple-choice that doesn't hold an agent slot. Overcode agents ask questions via free-text only.
- **MIT licensed + npm-registry distribution.** `npx bobbit` is a trivially lower onboarding bar than installing Overcode. Airgap-friendly (no runtime downloads).
- **Depth of documentation.** 7.3k lines across 32 docs including symptom-indexed debugging. Overcode's docs are sparser.
- **Primary branch detection with `origin/` prefix enforcement.** Baseline-aware verification prevents the "branch doesn't match design doc" false-positive that drifts when local master is stale. A detail Overcode doesn't need to care about because it has no verification — but the broader lesson ("never assume local git state is authoritative") applies to Overcode's `sync to main` command.

## Overcode's Relative Strengths

- **Entire supervision layer.** Supervisor daemon, 25 standing-instruction presets, heartbeat-to-idle-agents, oversight mode with stuck detection and timeouts, intervention history, per-session standing instructions, budget soft-enforcement. Bobbit has `TeamManager.nudgePending` + staff-agent triggers + tool policies, but nothing that watches an arbitrary running session and injects instructions every N minutes.
- **Cost budgets with enforcement.** Per-agent dollar/token/joule budgets, soft-enforced, transferrable between agents. Bobbit has cost *display* + *aggregation* but no enforcement.
- **Agent hierarchy with 5-level parent/child trees.** Cascade kill, budget inheritance, fork-with-context, follow mode, oversight timeout. Bobbit's hierarchy is 2 levels deep (lead → members) or 1 level (session → delegate).
- **Rich status taxonomy with authoritative transitions.** 10+ states via 442 regex patterns + Claude Code hooks. Bobbit's `SessionStatus` has 6 values because the agent pushes structured events — different strategy, narrower enum.
- **Cross-machine aggregation via sister.** See N remote machines' agents in one view. Bobbit has multi-device for one machine's agents, not multi-machine for one user's agents.
- **Parquet export for offline analysis.** Overcode runs in Jupyter; Bobbit has no equivalent data-science pipeline.
- **Timeline view with color-coded status history bars.** Overcode's dashboard surfaces temporal patterns per-agent; Bobbit's UI is live-only with no retrospective time-series.
- **Presence tracking (macOS idle/lock detection, CSV logging).** Bobbit doesn't track user presence.
- **Information density.** Overcode's Textual TUI with configurable columns, 4 sort modes, ~50+ keybindings surfaces more per-screen than Bobbit's session-card + chat layout.
- **Claude Code-specific deep integration.** JSON session log parsing, hook-based authoritative transitions, pricing per model, session fork with context inheritance. Bobbit bets on pi-coding-agent, so Claude-specific depth isn't available even if they wanted it.
- **Runs purely in a terminal over SSH.** Bobbit's web UI stops working as soon as you lose the browser; Overcode in tmux works fine over a 300-baud SSH link.
- **Claude-cost-model accuracy.** Overcode knows Claude's pricing model cold (haiku/sonnet/opus, cache reads/writes, joules). Bobbit inherits whatever `pi-ai` ships.

## Adoption Candidates

| # | Idea | Value | Complexity | Notes |
|---|---|---|---|---|
| 1 | **Workflow/gate DAG with phased verification** | High | High | The biggest concept Bobbit has that Overcode doesn't. A declarative "goal has gates, gates have DAG deps, gates have verify-steps grouped by phase, phase 0 parallel-runs cheap checks, phase 1 runs LLM reviews only if phase 0 passes" would let Overcode agents be held to process quality instead of relying on standing instructions. Cascade-reset on re-signal of upstream is a crucial detail. |
| 2 | **Goal/task cost aggregation** | High | Low | Overcode tracks per-agent but has no concept of aggregating by logical unit of work. Adding a `goal_id` or `task_id` on sessions + rolling up dollars/tokens would be a small change with big usability payoff. |
| 3 | **Roles × personalities composition** | Med-High | Medium | Overcode's 25 standing-instruction presets collapse role + personality into a single bundle. Splitting them into orthogonal role YAMLs (which tools + prompt template) and personality YAMLs (modifier fragments) would make presets composable rather than multiplicative-explosion. |
| 4 | **Tool-group access policies (`allow`/`ask`/`never`)** | High | Medium | Overcode has permission modes at the agent level (normal/permissive/bypass). A per-tool-group policy with project-level + role-level overrides would let users grant bash-but-not-edit to the supervisor or edit-but-not-bash to a review agent. Maps directly onto Claude Code's tool categories. |
| 5 | **Session worktree by default** | High | High | Overcode's biggest architectural limitation. Bobbit's `session/*` branches + Settings→Maintenance orphan cleanup + subdirectory-project offset via `path.relative` is a good blueprint. Needs: worktree pool, cleanup lifecycle, orphan detection UI. |
| 6 | **MCP server auto-discovery via `.mcp.json`** | High | Medium | Claude Code-compatible schema; drop in project root; auto-connect; tools appear in tool list with role-based access. Overcode being Claude-Code-native should ship this "for free" — most of the agent-side work is already done by Claude Code itself. |
| 7 | **Prompt queue with steer/drag-reorder/edit/remove** | High | Medium | Overcode has a rich command bar but no queue. Bobbit's `follow_up`-preserving queue + steer-to-front + drag-reorder + auto-drain-on-idle is a clear UX improvement for rapid-fire instruction delivery. Force-abort recovery (`resetDispatched` + `drainQueue`) is the non-obvious detail. |
| 8 | **Continue-Archived sessions** | Medium | Low-Med | Reopen an archived transcript as a fresh session inheriting settings but not runtime state, with transcript seeded under `## Prior Session Transcript` in the system prompt. Cheaper than resuming and avoids the "worktree is gone / container is pruned" foot-guns. Matches Overcode's fork-with-context feature but for already-terminated agents. |
| 9 | **Git-status widget TTL cache + tri-state + retry ladder** | Medium | Low | Overcode shows branch/ahead/behind but doesn't have the reliability discipline. The 750ms single-flight cache + tri-state `gitRepoKnown` + [0,500,2000,5000]ms retry ladder + `partial: true` degrade is a transferable pattern for any widget that calls out to shell. |
| 10 | **Staff agents with schedule/git/manual triggers** | Medium | Medium | Recurring agents with permanent worktrees, auto-rebased + setup-re-run on wake. Good fit for "nightly broken-build checker" or "every-Monday dependency-update agent." Overcode has cron-like workflow but not a first-class persistent-agent concept. |
| 11 | **Browser Notification API + title flash + audio beep** | Medium | Low | Three cheap channels; all reachable from JavaScript. If/when Overcode grows a web dashboard worth leaving open, this is the minimum viable notification stack. |
| 12 | **Rebindable shortcut registry** | Medium | Medium | Data-driven registry with browser-reserved-combo blocking, input-focus + shadow-DOM guard, conflict detection, `allowInInput` flag, and persistence to a preferences file. Cleaner than hardcoded keybindings. |
| 13 | **Interactive `ask_user_choices` non-blocking tool** | Medium | Medium | Up to 5 choices + optional "Other"; turn ends immediately; user response arrives as tagged user message that wakes the agent. Makes multi-choice questions idle-safe instead of holding an agent slot. Pairs well with the supervisor daemon — supervisor can ask the user structured questions without blocking. |
| 14 | **Event-stream dedup/reorder with seq+ts** | Medium | Medium | `seq`+`ts` on every event; reconnect sends `{type:"resume", fromSeq}`; `resume_gap` falls back to full message re-fetch. Snapshot-merge invariant (server authoritative, drop matching client-bucket IDs, stable-sort by ts + insertion-order) would help if Overcode's web dashboard ever shows cross-machine live state. |
| 15 | **3-tier config cascade** | Med-Low | Medium | Global → server → project with project winning. Overcode's config is global-only. Adding project-local overrides (e.g. a repo-specific standing-instruction preset) would be a small but user-visible improvement. |
| 16 | **Skill discovery from `.claude/skills/`** | Medium | Low-Med | Claude Code compatibility is a strong positioning move. Level-1 autoexpose + `activate_skill` + Level-3 resource manifests. Given Overcode already IS Claude-Code-native, supporting the skill discovery path costs little. |
| 17 | **NordVPN meshnet + deSEC dynDNS + Bobbit CA for zero-config remote access** | Low-Med | High | Niche but slick. Overcode's sister handles cross-machine aggregation differently; this is specifically "my phone should reach my laptop's agents." Could be a power-user flag rather than default. |
| 18 | **`SessionManager.deliverLiveSteer()` persisting to queue BEFORE steer RPC** | Low | Low | Prevents losing steer messages if the user hits Stop and then steers. Overcode's heartbeat + standing-instructions should have the same invariant: persist-before-deliver. |
| 19 | **Bobbit mascot sprite per session with role accessory** | Low | Med | Fun, not essential. But a visual identity per agent (colour + hat) at glance-distance is genuinely useful when you're watching 8 agents and need to tell them apart. Could apply to Overcode's timeline rows. |
| 20 | **Tool-group-policies YAML as first-class project config** | Low-Med | Low | Separate file from role YAMLs so users can manage the trust surface without touching role definitions. |
| 21 | **Generation-counter poll pattern (`?since=N`)** | High | Low | `SessionStore`/`GoalStore` maintain a monotonic counter incremented on every mutation; clients poll with `?since=N`; server returns `{changed: false, generation: N}` when nothing moved — client skips JSON parsing and renders nothing. Makes a 5-second polling loop essentially free when idle. Directly applicable to Overcode's dashboard + sister aggregation where every machine currently re-serializes full state on every poll. |
| 22 | **Errored-turn implicit unstick with consecutive-error cap** | High | Low-Med | When a turn ends with `stopReason: "error"`, `session.consecutiveErrorTurns` increments. Next prompt/steer: if `< 3`, clears `lastTurnErrored`, prepends `[SYSTEM: previous turn failed with: …. Ignore the incomplete last turn and handle the following.]`, dispatches (no retry of the failed turn — treat new input as fresh intent). If `≥ 3`, parks in queue until explicit Retry. Counter resets on any successful turn. Solves the "Claude Code turn errored on a transient glitch and the session is now permanently wedged until the user notices" problem cleanly. Overcode hits this often with Anthropic API transport blips. |
| 23 | **Viewer WebSocket (`/ws/viewer`) — sessionless read-only live feed** | Medium | Low | A second WS endpoint that auths with the gateway token but is not bound to any session. Server broadcasts gate-verification events to all authenticated sessionless clients. Bobbit uses it for the goal dashboard (no active session, but still wants live verification logs). Overcode could use the exact pattern for its timeline view and sister-side dashboards — a read-only observability channel that doesn't need to own a session. |
| 24 | **Large-content truncation at the event broadcast layer (32 KB threshold)** | High | Medium | When an agent writes a 40 MB file, each streaming chunk carries the full accumulated message — 40 MB per token, on every token, broadcast to every connected browser and held in the ring buffer. Bobbit intercepts `message_update` events between the cost-tracker/search-indexer hooks (which see full content) and the broadcast/EventBuffer (which see a truncated stub). UI lazy-loads via `GET /api/sessions/:id/tool-content/:mi/:bi`. Plus a 2×/sec streaming throttle for truncated content. Overcode has the same footgun latent in its tmux pane + web dashboard streaming — Claude's transcript JSON has the same "full-accumulating-message" property. |
| 25 | **Worktree pool (per-project, host-side pre-create)** | Medium | Medium | If/when Overcode adds worktrees (Idea #5), a pool that pre-creates `N` worktrees per project in the background so `POST /api/sessions` claims one instantly makes session start feel immediate. On acquire, fetch from origin and hard-reset to remote primary branch — "pool freshness" catches the case where the branch advanced since pool entry was created. Per-project keying so a heavy project doesn't starve others. |
| 26 | **Container health monitor with 3-tier worktree recovery** | Medium | High | Only relevant if Overcode adds Docker sandbox (currently a non-goal). Pattern: 20 s `docker inspect` poll → detect container death → recreate container (volumes survive) → for each session, try `test -d <cwd>` → `git worktree repair` → `createWorktree` fresh → archive if all three fail. WebSocket clients preserved across recovery so UI shows `terminated → idle` transition cleanly. Noted for future reference. |
| 27 | **Preview-snapshot pattern with versioned marker** | Low-Med | Low-Med | `__preview_snapshot_v1__\n<html>` prefix on a plain `{type:"text"}` tool-result block rather than a new block type. Survives truncation (marker is preserved, body replaced with 512-char preview). UI renders "Open" button that replays through the normal preview endpoint. Backwards compatible — historical results without marker render with Open disabled. The lesson: versioned sentinels on standard block types beat new block types when you need to extend an owned protocol without breaking readers. |
| 28 | **QA testing as a full gate type with ephemeral environment seed** | High | High | Beyond "LLM reviews the diff": spawn a test-engineer agent, stand up an isolated copy of the application in a `mktemp -d` (outside the repo), seed `.bobbit/state/` with realistic fixture data via `node scripts/qa-seed/seed.mjs`, allocate a free port, `bash_bg` the server, poll `qa_health_check` (up to 60 s), navigate a real browser via `browser_*` tools (native, not `mcp__playwright__*`), screenshot each step, submit HTML report via `verification_result` tool, enforce `qa_max_duration_minutes` + `qa_max_scenarios` budgets, always clean up. Screenshots spilled to `<cwd>/.bobbit-qa/screenshots/<uuid>.png` and referenced via `file://` URLs in the report — server inlines to base64 on submit (20 MB cap). This is a distinct product feature beyond standing instructions or gate verification and would differentiate Overcode for teams that care about "did the feature actually work end-to-end." |
| 29 | **Non-blocking ask-user pattern with envelope-in-transcript** | Medium | Low-Med | Already captured as Idea #13 but the envelope mechanics are worth noting: `[ask_user_choices_response tool_use_id=<ID>]\n{"answers":...}` as a user-role message, role check + causality check (envelope must follow a matching tool_use block) to block prompt-injection, idempotency by `(sessionId, toolUseId)` for multi-tab + retry, transcript-as-state means restart-safe with no `/pending` endpoint and no WS event types for "answered" convergence. Cleaner than the blocking-Promise pattern in every dimension except "agent keeps its turn." |
| 30 | **Blocking-tool harness as a reusable pattern** | Low-Med | Low | Generic shape: `Map<(sessionId, toolUseId), Pending>` + `register` / `submit` / `rejectAllForSession`. Persistence for survival across restart. Idempotent re-registration. Session-termination listener rejects pending Promises so callers get a clean error instead of hanging. Bobbit uses it for `verification_result`; anywhere Overcode's supervisor wants to "park an agent until another subsystem finishes" fits the pattern. |
| 31 | **Mid-session `propose_project` / project proposal diff UI** | Medium | Medium | Any running agent can call `propose_project` to suggest `project.yaml` edits (missing test command, better worktree setup, stale model preference). A "Project" tab appears in the preview panel showing a diff of proposed fields vs current. User accepts → `PUT /api/projects/:id/config` writes atomically, session stays connected, agent continues. The pattern — "agents discover config gaps mid-work, surface structured proposals, user reviews, config updates without breaking flow" — generalises to Overcode's standing-instructions and budget settings. |
| 32 | **Per-session draft persistence with race-safe load** | Low | Low | Textarea drafts saved (debounced) to server; loaded on session switch. Switch guard: the in-flight save promise stored in `_pendingSave`; new-session draft-load awaits it so a stale save doesn't clobber the newly-loaded draft. Teardown lets in-flight saves complete rather than aborting. `requestAnimationFrame` retry loop re-applies loaded value for 5 frames to survive Lit re-renders. Small but the kind of detail that makes a multi-device UI feel reliable. |
| 33 | **Metadata-endpoint blackholing in Docker sandbox** | Low-Med | Low | `--add-host=169.254.169.254:0.0.0.0 --add-host=metadata.google.internal:0.0.0.0 --add-host=metadata.internal:0.0.0.0` as defense-in-depth against SSRF via cloud IMDS endpoints. Only relevant if Overcode adds Docker sandbox but worth remembering. |
| 34 | **Thinking-level per-project / per-session** | Low-Med | Low | `default_thinking_level` in `project.yaml` — `off` / `minimal` / `low` / `medium` / `high` — with hardcoded token budgets (1024/4096/10240/32768). Per-session toggle overrides. A minor product feature but a concrete knob Overcode doesn't expose. |

---

## Additional mechanics surfaced from full `internals.md` re-read

A second pass through Bobbit's 1,510-line `internals.md` surfaced several engineering patterns worth documenting here even when they don't rise to the level of a transferable idea — either because they're Overcode-irrelevant, too tightly bound to Bobbit's architecture, or already implicit in existing Adoption Candidates entries. Listed for completeness:

- **Config cascade with `ResolvedItem<T>` origin tags.** `{ item, origin: "builtin" | "server" | "project", overrides?: ConfigOrigin }` returned from `resolveRoles()`, `resolvePersonalities()`, etc. UI renders grey/blue/green origin badges; inherited items render at 70 % opacity in project scope. Customize endpoint copies a resolved item to the target scope for editing; revert endpoint removes the override. This turns a cascade from "invisible magic that sometimes bites you" into a first-class UI concept.
- **Provisional projects + promote-on-accept.** When the project-setup assistant starts, the server registers a provisional project with a real `projectId` so the session has proper store isolation from the first message. On accept, `POST /api/projects/:id/promote` clears the `provisional: true` flag and writes the final config. If the session terminates without accepting, the provisional project is cleaned up via `DELETE`. Survives page refresh; replaces an earlier client-side `state.pendingProjects` approach.
- **Snapshot merge invariant** (server authoritative, `serverIds` filter on client-bucket entries, stable-sort by `(timestamp, insertionOrder)`) and **chat scroll lock invariant** (`delta === 0` no-op, programmatic-scroll-latch filter, wheel/touchstart/keydown as authoritative user-intent source, no timers). Both are documented as hard invariants with twin regression tests because they were the root of hard-to-repro UI bugs. The lesson for Overcode's Textual TUI is weaker (the failure modes look different in a TUI) but the *discipline* of naming an invariant, pinning it with a test, and forbidding timer-based heuristics is transferable.
- **Event stream via single `emitSessionEvent()` helper.** Every `{type:"event"}` broadcast goes through one function that assigns `seq`, stamps `ts`, pushes to the `EventBuffer` ring, and broadcasts — in lockstep, from a single call site. Replaced a prior pattern of paired `eventBuffer.push() + broadcast()` calls at six sites. Strict monotonicity falls out of "only one place can assign a seq." Applies anywhere Overcode has duplicated push-then-broadcast paths.
- **Steer-interruptible `bash_bg wait` via `AbortController` registry.** `BgProcessManager.waits: Map<sessionId, Set<AbortController>>`. `abortAllWaits(sessionId)` called from every steer delivery path before `rpcClient.steer()` so an agent parked in a 300 s wait doesn't delay the steer. Aborted waits resolve with `{ info, timedOut: false, aborted: true }`; backgrounded processes keep running. Relevant to Overcode's supervisor daemon: standing-instruction delivery should interrupt long-poll tool calls the same way.
- **Tool-guard extension with long-poll permission flow.** Pi-coding-agent's `tool_call` event hook supports `{ block: true }`. Bobbit generates a TypeScript extension at session setup containing the `ask`-policy tool map + session grants; the guard POSTs to `/api/sessions/:id/tool-grant-request` (long-poll) on an ungranted `ask` tool; server broadcasts `tool_permission_needed` WS event; UI shows dialog; server resolves long-poll with `{granted, reason}`. Maps cleanly to Overcode's existing permission mode but adds structured always/this-session/just-this-once grant duration and per-session in-memory grant set.
- **Search orphan filtering with opportunistic cleanup.** `ProjectContextManager.searchAll()` post-filters hits against authoritative stores (`projectRegistry.has`, `goalStore.get`, `sessionManager.getPersistedSession`, `staffStore.get`), recomputes `total` from the filtered list (so Load More honesty is preserved), and fires-and-forgets a cleanup that removes the stale rows from the owning project's `SearchService`. Response doesn't wait on cleanup. Plus weak-match tagging (`matchedOn: "text" | "metadata"`) so `message` rows whose snippet didn't render a `<b>` highlight are dropped as phantom matches. Defence-in-depth against eventually-consistent indexes.
- **FlexSearch chosen explicitly for airgap compatibility.** The prior stack (Nomic + LanceDB via `@huggingface/transformers` + `onnxruntime-node` + `sharp` + platform-specific Rust binaries) pulled ~140–500 MB on first search and could fail in any network-restricted environment. FlexSearch is pure-JS, zero-dependency, no native compilation. Identifier search is *better* (exact-symbol ranking) than embeddings; natural-language fuzzy search is weaker. This is a strategic positioning statement ("Bobbit installs anywhere, including corporate networks") more than a feature.
- **Sandbox scoped token** (one 256-bit token per project container, in-memory only, regenerated on restart) with endpoint allowlist (`sandbox-guard.ts`): only `/api/health`, `/api/internal/mcp-call`, `/api/internal/verification-result`, `/api/preview`, own session/goal/team/gates/tasks CRUD, plus forced-sandboxed `POST /api/sessions`. `bash_bg` blocked at tool and API level. Not applicable to Overcode's current threat model but informative for any future "let an agent talk to my gateway" work.
- **Git-over-HTTPS in sandbox via process-env credential helper.** Dockerfile installs `git config --global credential.helper '!f() { test -n "$GITHUB_TOKEN" && echo "username=x-access-token" && echo "password=$GITHUB_TOKEN"; }; f'`. Token passed per-exec via `docker exec -e GITHUB_TOKEN=xxx`, never written to container filesystem. `gh` CLI honours `GITHUB_TOKEN` natively. Injection path chosen because pooled containers start before credentials are known. A clean pattern for passing secrets to short-lived agent processes without leaking them into image layers or volumes.
- **Inter-agent git handoff via task fields.** Tasks carry `baseSha`, `headSha`, `branch`. Coder agents commit + push + `task_update(state: complete, head_sha: ...)`. Team lead merges member branches locally (goal worktree) before signaling verification gates (verification runs in goal worktree, not member worktree — important for the `origin/<primary>...HEAD` baseline to actually reflect what the gate is meant to verify). The choreography maps cleanly onto a Claude-Code-specific version: Overcode's supervisor could carry similar handoff fields if and when it adds teams + merge workflow.
- **Goal re-attempt flow.** `PersistedGoal.reattemptOf` and `PersistedSession.reattemptGoalId` linkage. Goal assistant session created with `reattemptGoalId` loads the original via `buildReattemptContext()`, walks the user through what went wrong / revert-or-fix / new spec. On accept, old goal archived, new goal carries the back-reference. This is a project-management feature; directly relevant only if Overcode adds a goal concept (Idea #2 / #11), but the *auditability* — you can always walk back the chain of re-attempts from a merged goal — is a pattern worth carrying forward.

None of these change the bakeoff's top-line findings. The full read confirmed that the main Cluster-1 / Cluster-2-bridge / Cluster-3-bridge positioning is right and that Bobbit's core distinctive features (workflow gates, team-lead orchestration, worktree-by-default, roles × personalities × tool-policies, MCP + skills) were correctly identified on the first pass. The re-read surfaced implementation patterns to mine — especially the generation-counter poll, errored-turn implicit unstick, large-content truncation at the event layer, and viewer WebSocket — not new strategic claims.

---

## The Big Takeaway

**Bobbit and Overcode are the most architecturally different tools in the bakeoff catalogue so far.** Not just the form factor — everything underneath is different:

- Bobbit: web UI, browser-native, web-standards-heavy, TypeScript, Lit, pi-coding-agent, multi-project, multi-device, goals/workflows/gates/teams as first-class concepts, worktree-by-default, Docker-sandbox-optional, MCP + Claude-Code-skill compat, process-quality-enforced via declarative DAGs.
- Overcode: tmux TUI, Python, Claude-Code-only, single-project, single-machine-with-sister-aggregation, sessions-as-peers with optional hierarchy, no worktree isolation, no MCP, no workflow engine, no quality gates.

**Do they complement or compete?** They compete for the same user — "I want to run N coding agents in parallel and stay in control" — but on orthogonal axes. A user who wants **process-quality enforcement** (design doc → implementation → code review → QA → ready-to-merge) should pick Bobbit. A user who wants **cost-bounded, supervision-heavy, Claude-Code-specific orchestration with cross-machine visibility** should pick Overcode. The Venn overlap is "a solo dev running 2–4 parallel agents on a single machine," and for that person the decision is mostly taste (browser vs TUI, Claude-Code vs pi-coding-agent).

**Could one replace the other?** No, not without destroying the thing that makes each valuable.

- Bobbit cannot replace Overcode without giving up its browser-UI bet, losing its entire workflow/gate/team machinery (which assumes a browser-rendered dashboard to display gate status, verification logs, team coordination), and switching to Claude Code + tmux. That would be a full rewrite.
- Overcode cannot replace Bobbit without adding: worktree-by-default, goal/workflow/gate DAG, team-lead orchestration, roles × personalities composition, tool-group policies, MCP, browser UI with mobile layout, PWA, TLS + CA management, prompt queue, session worktree pools, and skill discovery. That's most of the Bobbit feature list — effectively a rewrite.

**Where they could cross-pollinate cheaply:**

1. **Overcode adopts goal/task cost aggregation** (#2) and **roles × personalities composition** (#3) — both small, high-value.
2. **Overcode adopts tool-group access policies** (#4) and **MCP auto-discovery** (#6) — medium effort, large safety / ecosystem wins.
3. **Overcode adopts prompt queue** (#7) and **Continue-Archived** (#8) — direct UX wins.
4. **Bobbit adopts Overcode's budget enforcement** and **standing-instruction heartbeat** — Bobbit has cost display and static system prompts; adding budget caps and periodic directive injection would round out its supervision story without breaking its architecture.
5. **Bobbit adopts Overcode's sister-style cross-machine aggregation** — Bobbit's multi-device is single-machine; adding "see all my agents across all my machines in one browser" is the logical next step.

**Biggest single idea for Overcode to consider: workflow gates with phased verification (#1).** It would reframe "agents with standing instructions" as "agents operating within a quality gate DAG" and give users a structured way to enforce "tests pass → code review happens → design is followed." The `command`/`llm-review`/`agent-qa` verify-step trio is expressive enough to cover 80% of what teams want from "don't just merge whatever the agent wrote."

**Biggest single idea Bobbit could consider in return: per-agent budget enforcement with soft-skip / transfer.** Bobbit's cost display is good, but running 12 coders in parallel with no budget ceiling is a foot-gun waiting to fire. A soft-budget check at turn-start and a transfer primitive between sessions would make parallel work materially safer.
