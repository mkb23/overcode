# Overcode vs GasTown: Feature Bakeoff

## Overview

| | **GasTown** | **Overcode** |
|---|---|---|
| **Repo** | [steveyegge/gastown](https://github.com/steveyegge/gastown) | This project |
| **Language** | Go 1.25+ (Cobra CLI, bubbletea/lipgloss TUI, htmx dashboard) | Python 3.12+ (Textual TUI) |
| **Stars** | 14,128 (as of 2026-04-15) | N/A (private) |
| **License** | MIT | Proprietary |
| **First Commit** | 2025-12-15 | 2025 |
| **Last Commit** | 2026-04-15 (active daily) | Active |
| **Purpose** | Git-backed, multi-agent workspace manager with persistent agent identity, feudal hierarchy, inter-agent mail, and federated work markets. | Claude Code supervisor/monitor with instruction delivery via tmux. |

## Core Philosophy

GasTown is Steve Yegge's "civilization simulator" for AI agents — the whole system is modeled as a town, with a **Mayor** (Claude-based coordinator), **Rigs** (projects, one per repo), **Polecats** (persistent worker identities with ephemeral sessions), **Crew members** (humans), a **Deacon** (cross-rig supervisor daemon), **Witnesses** (per-rig monitors), a **Refinery** (merge queue) and **Dogs** (dispatchable maintenance workers). Work is reified as **beads** (git-backed issues, `internal/cmd/bead.go`) bundled into **Convoys** and executed by **Molecules** (TOML workflow templates from `internal/formula/`). The defining bet is that *state belongs in git, not in agent memory* — agents are assumed to die constantly, so the entire work-tracking substrate (Beads + git worktrees called "hooks") is designed to survive restarts and allow new sessions to pick up predecessors' context via **Seance** (`gt seance --talk <id>`).

The target user is someone running **20-30+ concurrent agents** across multiple repos, often unattended. The MEOW loop (Mayor-Enhanced Orchestration Workflow) is: human tells Mayor a goal → Mayor decomposes into beads/convoys → Mayor dispatches polecats → Witnesses monitor health → Refinery lands completed work as a Bors-style bisecting merge queue → Deacon patrols cross-rig and escalates blockers up the chain. The human is expected to interact primarily with the Mayor and `gt feed`, not with individual agents.

The mental model is **bureaucratic** and **agent-agnostic** — 10 runtimes (`claude`, `codex`, `cursor`, `gemini`, `auggie`, `amp`, `opencode`, `copilot`, `pi`, `omp`) can all play any role. Agents communicate via a first-class **mail system** (`internal/mail/`), complete with priorities, threading, inboxes, channels, escalations, and `queue` vs `interrupt` delivery modes. The ambition is roughly an order of magnitude larger than Overcode's: Overcode supervises Claude Code agents on one machine; GasTown aims to be a federated multi-agent operating system with a work marketplace (**Wasteland**) that lets Gas Towns on different machines claim each other's work via DoltHub.

## Feature Inventory

### 1. Agent Support

- **Runtimes**: 10 built-in presets — `claude`, `gemini`, `codex`, `cursor`, `auggie`, `amp`, `opencode`, `copilot`, `pi`, `omp` (`README.md:415`). Default is Claude Code.
- **Adding new agents**: Both built-in and custom agents via `gt config agent set <name> <cmd>` (e.g., `gt config agent set claude-glm "claude-glm --model glm-4"`, `README.md:429-432`). Plugin system at `plugins/` for Dogs and other extensions (`plugins/` directory includes `stuck-agent-dog`, `github-sheriff`, `quality-review`, etc.).
- **Agent-agnostic**: Yes. Per-rig config in `settings/config.json` selects runtime provider; hooks layer abstracts context injection per agent. Roles (Mayor, Polecat, Witness, Deacon, Refinery, Boot) can each use any runtime.
- **Runtime hooks mechanism** (`docs/HOOKS.md`):
  - Claude Code + Gemini: `.claude/settings.json` lifecycle hooks (sessionStart, userPromptSubmitted, preToolUse, sessionEnd).
  - OpenCode: JS plugin at `workDir/.opencode/gastown.js`.
  - GitHub Copilot: JSON lifecycle hooks at `.github/hooks/gastown.json`.
  - Codex and others: startup-nudge fallback (`gt prime`, `gt mail check --inject`, `gt nudge deacon session-started`).
- ACP (Agent Client Protocol) implementation in `internal/agent/provider/acp.go` — JSON-RPC wrappers for tool-use/tool-result content.

**Overcode comparison**: Overcode is Claude-Code-only. GasTown wins decisively on breadth.

### 2. Agent Launching

- **Spawn entry points**:
  - `gt sling <bead-id> <rig>` — assign a specific bead to a polecat in a rig; auto-spawns if needed (`internal/cmd/sling.go`, `sling_dispatch.go`, `sling_schedule.go`, `sling_batch.go`, `sling_convoy.go`, `sling_formula.go`).
  - `gt sling <bead> <rig> --agent cursor` — override runtime per-dispatch.
  - `gt polecat spawn` — explicit polecat creation (`internal/cmd/polecat_spawn.go`).
  - `gt convoy create "<name>" <beads...> --notify --human` — Mayor-driven bulk spawn.
  - `gt mayor start --agent <alias>` / `gt mayor attach` (`internal/cmd/mayor.go`).
  - `gt dog` — dispatch a maintenance-agent for a plugin task.
  - `bd cook <formula>` / `bd mol pour <formula>` — formula-driven workflow execution.
- **Inputs**: bead IDs, rig name, runtime/agent alias, `--notify`, `--human`, severity (for escalations), optional model/scheduler flags.
- **Launch from file / pre-written prompt**: Yes — beads themselves are the structured prompt (title, body, metadata in the Beads DB). Convoy create accepts multiple bead IDs. Formulas are TOML files (`internal/formula/formulas/*.toml`) with variable substitution (`bd cook release --var version=1.2.0`).
- **Initial prompt delivery**:
  - Claude/Gemini: via `sessionStart` hook → injects mail + context.
  - Copilot: via `.github/hooks/gastown.json` lifecycle hook.
  - Codex/others: 5-second ready delay then `gt prime` + `gt mail check --inject` nudge sent via tmux.
- **Templates / presets**: Formulas (`release.formula.toml` etc.), molecules (tracked instances with checkpoint recovery), directives (`gt directive`), roles, built-in agent presets.

**Overcode comparison**: Overcode launches agents via TUI `n` hotkey with a host selector and a 25-preset standing-instruction library. GasTown has a much richer task representation (beads/convoys/formulas) but no local "host selector" concept.

### 3. Session/Agent Lifecycle

- **Polecat state machine** (`internal/polecat/types.go:8-62`):
  - `StateWorking` — session active, doing assigned work.
  - `StateIdle` — work completed, session killed, sandbox preserved for reuse.
  - `StateDone` — `gt done` called, transient; stuck here = zombie.
  - `StateStuck` — polecat self-reported needing help.
  - `StateStalled` — tmux session died while work was assigned (externally detected).
  - `StateZombie` — tmux session exists but worktree gone.
- **Additional lifecycle concepts**:
  - Convoy lifecycle (`docs/design/convoy/`).
  - Molecule / wisp lifecycle with root-only and poured modes.
  - Hook lifecycle (Created → Active → Suspended → Active → Completed → Archived, `README.md:634-644`).
- **Persistence**:
  - Polecat identity + work history persist forever; *sessions* are ephemeral. A polecat "keeps its worktree so it can be quickly reassigned without creating a new one" (`polecat/types.go:21`).
  - Beads = SQLite + git-backed issue DB (via `bd` binary).
  - Dolt server (MySQL-compatible DVCS) for cross-rig coordination (`doltserver/`).
  - Agent state on disk: `<rigPath>/.runtime/<stateFile>.json` with atomic writes (`internal/agent/state.go:14-67`).
  - `.events.jsonl` logs per session for Seance discovery.
- **Survives**: Process restart ✅, TUI restart ✅, machine reboot ✅ (everything is on disk/git/Dolt).
- **Resume/reattach**: `gt mayor attach`/`detach`, `gt seance --talk <id>`, `gt prime`, `gt resume` (`internal/cmd/resume.go`, `seance.go`, `prime.go`).
- **Cleanup**: `gt cleanup`, `gt reaper`, `gt maintain`, `gt prune-branches`, `gt estop` (emergency stop), `gt signal stop`, `gt tap polecat stop`, `gt polecat_cycle.go` reaper for zombies.

**Overcode comparison**: Overcode persists `Session` dataclass auto-serialized; sessions survive restarts via tmux attach. GasTown has a richer, explicitly-named state machine and a **persistent identity decoupled from session** concept Overcode lacks.

### 4. Isolation Model

- **Worktree-per-polecat**: Each polecat gets its own `git worktree` (called a "hook" in GasTown parlance — confusingly overloaded with lifecycle hooks). Hooks survive agent restarts.
- **Branch management**: Auto-created per polecat, merged back via Refinery. `gt prune-branches` cleans up (`internal/cmd/prune_branches.go`). Polecats never push directly to main.
- **Shared workspace**: No — polecats always get their own worktree. Crew members have their own workspace at `myproject/crew/<name>`.
- **Merge workflow (Refinery)** (`README.md:556-566`, `internal/refinery/`): Polecat runs `gt done` → branch pushed, MR bead created → Refinery batches pending MRs → runs verification gates on merged stack → if green, batch merges to main; if red, **bisects** to isolate the failing MR and merges the rest. This is a **Bors-style bisecting merge queue** — a genuinely sophisticated piece of machinery.
- **Sub-tasks / sub-worktrees**: Molecules support "poured wisps" — steps materialized as sub-wisps with checkpoint recovery (`docs/concepts/molecules.md`). Convoys group beads; formulas can spawn dependent sub-steps via `needs =` fields.

**Overcode comparison**: Overcode has **no worktree isolation** — all agents share the repo. This is a major gap. GasTown's Refinery merge queue is a category Overcode doesn't have at all.

### 5. Status Detection

- **Mechanisms** (hybrid):
  - **Claude lifecycle hooks** (authoritative, like Overcode): `.claude/settings.json` injects `gt heartbeat`, `gt signal`, `gt mail check`, etc. at sessionStart/preToolUse/sessionEnd.
  - **`gt heartbeat`** — every 3 minutes, agents post liveness (`internal/cmd/heartbeat.go`, `README.md:528-533`).
  - **tmux liveness cross-check** — Witness cross-checks tmux session state against beads state to derive `stalled`/`zombie`/`idle` (`polecat/types.go:44-61`).
  - **`gt signal` / `gt signal stop`** — hook-triggered events are processed locally fast.
  - **Events log** — `.events.jsonl` per session.
  - **Deacon patrol** — continuous AI-driven patrol cycles across rigs.
  - **Witness** — per-rig AI supervisor that runs recovery (nudge or handoff) when agents stall.
  - **Boot** — intelligent triage AI dispatched by Deacon.
- **Statuses tracked** (beyond polecat lifecycle): Agent health states surfaced in `gt feed --problems`:
  - **GUPP Violation** — hooked work with no progress for an extended period.
  - **Stalled** — hooked work with reduced progress.
  - **Zombie** — dead tmux session.
  - **Working** — active, progressing normally.
  - **Idle** — no hooked work (`README.md:493-500`).
- **Latency**: Claude hooks instant; heartbeat every 3 min; Deacon patrol on its own cycle.
- **Cost of detection**: Non-trivial — Witness, Deacon, and Boot are *themselves* AI agents that consume tokens to reason about health (unlike Overcode's polling, which is free regex).

**Overcode comparison**: Overcode uses 442 regex patterns + Claude Code hooks — same hook mechanism, cheaper but less semantic. GasTown adds AI-reasoning supervisors on top (expensive but richer).

### 6. Autonomy & Auto-Approval

- **Unattended operation**: First-class — the entire design assumes overnight/multi-day autonomous runs.
- **Auto-accept**:
  - Copilot runtime uses `--yolo` mode (`README.md:386-389`).
  - Claude runs with permissive settings by default to enable unattended work.
- **Safety modes**:
  - `gt dnd` — Do Not Disturb (`internal/cmd/dnd.go`).
  - `gt estop` — emergency stop (`internal/cmd/estop.go`, `estop_unix.go`, `estop_windows.go`) — must work even when Dolt is down.
  - `gt tap_guard_dangerous.go` and `tap_guard_bd_init.go` — tap guards on dangerous operations.
  - `gt warrant` — warrants (`internal/cmd/warrant.go`).
  - `gt quota` — per-something quota checks.
  - **Refinery gates** — verification tests must pass before merge to main.
- **Risk assessment before auto-accept**: Not per-command — it's a whole-system gate (Refinery) rather than per-tool-call.

**Overcode comparison**: Overcode has supervisor daemon with standing instructions (soft). GasTown's Refinery *hard-gates* merges — a stronger form of safety at the cost of slower feedback.

### 7. Supervision & Instruction Delivery

- **Sending instructions to running agents**:
  - `gt mail send` / `gt broadcast` (`internal/cmd/mail_send.go`, `broadcast.go`) — full mail system.
  - `gt nudge <target>` — inject a system-reminder directly into session (`internal/cmd/nudge.go`).
  - `gt handoff <agent>` — context refresh / handoff (`internal/cmd/handoff.go`).
  - `gt prime` — context recovery inside session.
  - `gt remember` / `gt forget` — persistent memory (`memories.go`).
- **Mail system details** (`internal/mail/types.go:11-100`):
  - **Priorities**: Low, Normal, High, Urgent.
  - **Types**: Task, Escalation, Scavenge, Notification, Reply.
  - **Delivery modes**: `queue` (agent polls) or `interrupt` (inject system-reminder directly into session).
  - **Threading**: `ThreadID`, `ReplyTo`.
  - **Channels**: `gt mail channel` — group mail (`mail_channel.go`).
  - **Inbox / drain / search / queue**: `gt mail inbox`, `gt mail drain`, `gt mail search`, `gt mail queue`.
  - **Announce / identity / directory**: `gt mail announce`, `gt mail identity`, `gt mail directory`.
- **Standing instructions / directives**: `gt directive`, `gt directive edit`, `gt directive list`, `gt directive show` (`internal/cmd/directive*.go`).
- **Heartbeat**: Every 3 min, daemon sends liveness check (`README.md:528`).
- **Supervisor daemon**: Yes — the **Deacon** (AI) runs continuous patrol (`internal/deacon/`), backed by the **Daemon** (Go process, `internal/daemon/`) for low-latency housekeeping. Also **Witness** per rig, **Boot** for triage, **Dogs** for dispatched work.
- **Intervention history / logging**: `.events.jsonl` per session, OTEL telemetry (`README.md:606-620`), `gt activity`, `gt audit`, `gt trail`, `gt log`, `gt agent-log` — multiple audit views.

**Overcode comparison**: Overcode has supervisor daemon + 25 standing-instruction presets + heartbeats. GasTown has the **mail system with delivery modes and threading**, a four-tier agent-supervisor hierarchy (Daemon → Boot → Deacon → Witnesses), and channels — substantially more sophisticated.

### 8. Cost & Budget Management

- **Token tracking**: Yes — reads Claude Code transcript files from `$CLAUDE_CONFIG_DIR/projects/` (default `~/.claude/projects/`) and sums token usage from assistant messages (`internal/cmd/costs.go:1-60`).
- **Cost calculation**: Applies model-specific pricing.
- **Flags**:
  - `gt costs` — live costs from running sessions.
  - `gt costs --today` / `--week` — time-windowed.
  - `gt costs --by-role` — breakdown by polecat/witness/deacon/mayor.
  - `gt costs --by-rig` — per-project breakdown.
  - `gt costs --json` — machine-readable.
  - `gt costs record` — called by Stop hook, appends to `~/.gt/costs.jsonl`.
  - `gt costs digest` — aggregates log → daily digest bead (Deacon patrol task).
- **Per-agent budgets**: Not surfaced in the README's cost commands. `gt quota` and `gt scheduler` constrain *concurrency* rather than spend.
- **Budget enforcement**: Indirect — via scheduler concurrency (`scheduler.max_polecats`) and Refinery gates, rather than a hard `$X/session kill` limit.
- **Cost display**: Tokens, dollars, by-role, by-rig. Daily digests persisted as beads.

**Overcode comparison**: Overcode has **per-agent cost budgets with soft enforcement** — a direct feature GasTown appears to lack. GasTown has better *post-hoc analytics* (by-role, by-rig, daily digests as beads); Overcode has better *live budget control*.

### 9. Agent Hierarchy & Coordination

- **Parent/child relationships**: Implicit via roles (Mayor → Polecat) and escalation chain. Convoys bundle beads.
- **Agent-to-agent communication**: First-class mail system (see §7) with channels, threading, and interrupt delivery.
- **Task decomposition**: Mayor (AI) decomposes goal → beads → convoy. Molecules/formulas provide TOML-declarative DAGs with `needs =` dependencies (`README.md:291-328`).
- **Cascade operations**:
  - `gt done --closeDescendants` (`done_closeDescendants_test.go` exists).
  - `gt convoy close`, `convoy land`.
  - `gt estop` — emergency stop whole town.
  - `gt tap polecat stop`.
- **Follow/oversight modes**:
  - Witness per rig, Deacon cross-rig, Mayor whole-town, Overseer (escalation terminus).
  - Escalation chain: CRITICAL (P0) → HIGH (P1) → MEDIUM (P2), routed through Deacon → Mayor → Overseer (`README.md:98-100`, `docs/design/escalation.md`).
  - `gt escalate`, `gt escalate list`, `gt escalate ack`.
- **Seance (cross-session)**: `gt seance --talk <id>` — agent-to-predecessor-agent querying of earlier sessions via `.events.jsonl` (`README.md:581-590`).

**Overcode comparison**: Overcode has parent/child trees 5 levels deep with cascade kill and fork-with-context. GasTown has a **feudal role hierarchy** that is flatter per agent but wider (Daemon → Boot → Deacon → Witness → Polecat), plus **cross-session identity continuity** via Seance that Overcode lacks.

### 10. TUI / UI

- **Interfaces**:
  - `gt feed` — bubbletea TUI (`internal/tui/feed/`) — primary monitoring view.
  - `gt dashboard` — web UI with htmx, auto-refresh, command palette (`README.md:503-522`, `internal/cmd/dashboard.go`).
  - `gt mayor attach` — directly attach to Mayor's tmux session (agent-as-UI).
  - CLI for everything else (~405 command files in `internal/cmd/`, via Cobra).
- **Framework**: bubbletea + lipgloss for the TUI; htmx for the dashboard.
- **Layout** (feed): **Three-panel TUI** — Agent Tree (hierarchical, grouped by rig/role) | Convoy Panel (in-progress + recently-landed) | Event Stream (creates, completions, slings, nudges). Toggleable **Problems view** (`p`) surfaces stuck agents.
- **Keyboard shortcuts** (complete list from `internal/tui/feed/keys.go:43-134`):

| Key | Action |
|---|---|
| `↑` / `k` | Up |
| `↓` / `j` | Down |
| `pgup` / `ctrl+u` | Page up |
| `pgdown` / `ctrl+d` | Page down |
| `home` / `g` | Top |
| `end` / `G` | Bottom |
| `tab` | Switch panel |
| `shift+tab` | Previous panel |
| `1` | Focus Agent Tree |
| `2` | Focus Convoys |
| `3` | Focus Event Feed |
| `enter` | Expand / details |
| `o` / `l` | Toggle expand |
| `R` | Refresh |
| `p` | Toggle problems view |
| `n` | Nudge selected agent (problems view) |
| `h` | Handoff agent (problems view) |
| `/` | Search |
| `f` | Filter |
| `esc` | Clear filter |
| `?` | Help |
| `q` / `ctrl+c` | Quit |

- **Customization**: `gt theme` (`internal/cmd/theme.go`), plain-text mode (`gt feed --plain`), dedicated tmux window (`gt feed --window`), time-window filter (`--since 1h`), problems-first (`--problems`).

**Overcode comparison**: Overcode's Textual TUI has ~50+ keybindings, timeline view, configurable columns, timeline. GasTown's feed is simpler (22 keys) but ships *alongside* a full web dashboard with command palette — Overcode's web dashboard is separate/simpler.

### 11. Terminal Multiplexer Integration

- **Multiplexer**: tmux (recommended; `tmux 3.0+` prereq, `README.md:131`). Support code in `internal/tmux/`.
- **Pane/window management**: Each polecat gets its own tmux session; Mayor has a session. `gt feed --window` opens feed in a dedicated tmux window.
- **Layout calculation**: Not surfaced as a tiling feature — sessions are detached by default; user attaches when needed.
- **Live output**: Via `gt mayor attach`, or direct `tmux attach -t <session>`. `gt peek` and `gt cat` surface output without attach.
- **Split/zoom/focus**: Delegated to tmux itself. GasTown does not ship a custom tmux layout manager in the Overcode/Claude-Squad sense.

**Overcode comparison**: Overcode is much more opinionated about tmux layouts (timeline, sidebar, pane routing). GasTown treats tmux as a dumb durable-session substrate and invests its UI energy in the feed + dashboard instead.

### 12. Configuration

- **Config locations**:
  - `~/gt/` — default workspace ("town").
  - `~/.gt/hooks-base.json` — shared hook config (`docs/HOOKS.md`).
  - `~/.gt/hooks-overrides/{role}.json` and `{rig}__{role}.json`.
  - `~/.gt/costs.jsonl` — cost log.
  - Per-rig `settings/config.json` — runtime provider, command, args, prompt mode.
  - `~/.codex/config.toml` (external) — for Codex integration.
- **Config scope**: Town-wide base, per-rig overrides, per-role overrides, per-(rig+role) overrides — merge strategy `base → role → rig+role`.
- **Key commands**:
  - `gt config agent list|get|set|remove <name> [cmd]`
  - `gt config default-agent [name|list]`
  - `gt config set scheduler.max_polecats 5`
  - `gt config set <key> <value>` / `gt config get <key>`
  - `gt config cost-tier`, `gt config agent email-domain`
- **Environment variables** (from README + code):
  - `GT_COMMAND` — renames the CLI binary.
  - `GT_OTEL_LOGS_URL` / `GT_OTEL_METRICS_URL` — OTEL endpoints.
  - `CLAUDE_CONFIG_DIR` — transcript location.
  - `GIT_USER`, `GIT_EMAIL`, `FOLDER`, `DASHBOARD_PORT` — docker-compose.
- **Lifecycle hooks**: Full — sessionStart, userPromptSubmitted, preToolUse, sessionEnd, Stop (for costs), plus the full internal event bus (`internal/events/`, `internal/channelevents/`, `internal/activity/`).

**Overcode comparison**: Overcode has simpler per-project config. GasTown's `base → role → rig+role` override chain is more powerful for multi-project workflows.

### 13. Web Dashboard / Remote Access

- **Web UI**: Yes — `gt dashboard [--port 3000] [--open]`. Single-page overview of agents, convoys, hooks, queues, issues, escalations. Auto-refreshes via htmx. Includes a **command palette** for running `gt` commands from the browser (`README.md:503-522`).
- **API endpoints**: Implied by dashboard + htmx; also OTLP endpoints for telemetry.
- **Remote monitoring**: Yes — via **Wasteland** federated network (`README.md:593-604`, `docs/WASTELAND.md`):
  - `gt wl join <remote>` — join a wasteland on DoltHub.
  - `gt wl browse` — view wanted board.
  - `gt wl claim <id>` — claim work from another town.
  - `gt wl done <id> --evidence <url>` — submit completion.
  - `gt wl post --title "Need X"` — post a wanted item.
  - Multi-dimensional stamps (quality, speed, complexity) as portable reputation.
- **Mobile-friendly**: Not documented.

**Overcode comparison**: Overcode has a web dashboard + HTTP API + Sister (cross-machine monitoring). GasTown's Wasteland is a **work market** not just monitoring — it's a genuinely novel category.

### 14. Git / VCS Integration

- **Branches**: Auto-created per polecat worktree; `gt prune-branches` cleans.
- **Commit automation**: `gt commit` (`internal/cmd/commit.go`).
- **PR creation**: Implied via `gt done` + Refinery. GitHub integration in `internal/github/` and `internal/cmd/github-sheriff` plugin. Bitbucket support in `internal/bitbucket/`.
- **Merge conflict resolution**: Refinery bisect isolates failing MRs; polecats can be re-dispatched inline.
- **GitHub/GitLab integration**: GitHub via `internal/github/`, `plugins/github-sheriff/`; generic git-hosting via `internal/git/`.
- **Git hygiene**: `plugins/git-hygiene/`, `plugins/gitignore-reconcile/`, `plugins/submodule-commit/`, `gt gitinit`.
- **Dolt**: Dolt server (MySQL-compatible DVCS) runs for cross-rig coordination; backup, archive, snapshots, log-rotate plugins in `plugins/dolt-*/`.

**Overcode comparison**: Overcode has minimal git integration. GasTown's **Bors-style merge queue, Dolt integration, and plugin-based git hygiene** are categorically more advanced.

### 15. Notifications & Attention

- **Mechanisms**:
  - `gt notify` (`internal/cmd/notify.go`) — notification plumbing.
  - Mail with `PriorityUrgent` + `DeliveryInterrupt` = immediate session injection.
  - `gt broadcast` for town-wide alerts.
  - Escalations (CRITICAL/HIGH/MEDIUM) auto-route to Deacon/Mayor/Overseer.
  - `gt feed --problems` surfaces stuck agents visually.
  - `gt dnd` — do-not-disturb mode.
- **Desktop notifications / sound**: Not explicitly surfaced in the README; likely via the `notify` subsystem if configured.
- **Attention prioritization**: Yes — explicit 3-level escalation severity + 4-level mail priority, routed differently.

**Overcode comparison**: Overcode has no native desktop notifications. GasTown has *in-system* attention routing (escalations + problems view) but also no clear OS-level notifications documented.

### 16. Data & Analytics

- **Session history**: `.events.jsonl` per session; Seance makes them queryable by other agents.
- **Activity / audit**: `gt activity`, `gt audit`, `gt trail`, `gt log`, `gt agent-log` (`internal/cmd/*.go`).
- **Export formats**: JSON (e.g., `gt costs --json`), OTLP logs/metrics.
- **Analytics**: Cost digests stored as beads; `gt metrics` (`internal/cmd/metrics.go`, reads local JSONL, no beads required); `gt vitals`, `gt health`, `gt doctor` for diagnostics.
- **Presence / activity**: `gt agents` lists active; `gt whoami`, `gt info`, `gt show`; `gt feed` live stream.
- **Telemetry (OpenTelemetry)** — `README.md:606-620`:
  - Events: session lifecycle, agent state changes, bd calls with duration, mail operations, sling/nudge/done workflows, polecat spawn/remove, formula instantiation, convoy creation, daemon restarts.
  - Metrics: `gastown.session.starts.total`, `gastown.bd.calls.total`, `gastown.polecat.spawns.total`, `gastown.done.total`, `gastown.convoy.creates.total`, …
  - Default backend: VictoriaMetrics/VictoriaLogs.

**Overcode comparison**: Overcode exports to Parquet; GasTown exports via OTLP. GasTown's OTEL-native design is better suited to existing observability stacks.

### 17. Extensibility

- **Plugin system**: `plugins/` directory with shipped plugins: `compactor-dog`, `dolt-archive`, `dolt-backup`, `dolt-log-rotate`, `dolt-snapshots`, `git-hygiene`, `github-sheriff`, `gitignore-reconcile`, `quality-review`, `rebuild-gt`, `stuck-agent-dog`, `submodule-commit`, `tool-updater`. Design doc: `docs/design/plugin-system.md`.
- **MCP support**: Not prominently advertised in the README; ACP (Agent Client Protocol) is the first-class cross-agent bus (`internal/agent/provider/acp.go`).
- **Custom formulas / molecules**: TOML-defined in `internal/formula/formulas/` or via `gt formula-overlay edit/list/show`.
- **Custom agents**: `gt config agent set <name> <cmd>`.
- **API for external tools**: Dashboard's htmx endpoints; OTLP output; `bd` CLI is a separate project you can script against.
- **Hook system**: `~/.gt/hooks-overrides/` with base→role→rig+role merge.

**Overcode comparison**: Overcode has limited plugin surface. GasTown's **plugin + formula + hooks-override chain** is a genuine three-axis extensibility model.

### 18. Developer Experience

- **Install**:
  - `brew install gastown` (recommended macOS).
  - `npm install -g @gastown/gt`.
  - `go install github.com/steveyegge/gastown/cmd/gt@latest` (Linux; macOS SIGKILLs unsigned binaries so `make build` is required).
  - Docker Compose option with `docker compose up -d` + `gt up`.
- **First run**:
  ```
  gt install ~/gt --git && cd ~/gt && gt config agent list && gt mayor attach
  ```
  You are then expected to just talk to the Mayor in natural language.
- **Onboarding**: Strong. README walks through town setup, rig add, crew add, mayor attach. Extensive glossary (`docs/glossary.md`), design docs for every major subsystem, architecture diagrams (mermaid) inline.
- **Documentation quality**: High — `docs/design/` has subdirs for escalation, scheduler, otel, convoy, polecat-lifecycle-patrol, witness-at-team-lead, plugin-system, agent-provider-integration. CHANGELOG maintained. AGENTS.md for agent conventions.
- **Test coverage / CI**: Extensive — `codecov.yml`, `gt-model-eval/` directory for model evals, `Dockerfile.e2e` for end-to-end CI, pervasive `*_test.go` files (e.g., ~405 files in `internal/cmd/`, roughly half tests).
- **Shell completions**: bash/zsh/fish (`gt completion bash|zsh|fish`).
- **Doctor / health**: `gt doctor`, `gt health`, `gt vitals`, `gt hooks repair`.

**Overcode comparison**: GasTown has a bigger surface area and correspondingly bigger onboarding, but the Mayor-first UX lowers the floor nicely. Overcode is smaller and faster to grok.

## Unique / Notable Features

1. **Bors-style bisecting merge queue (Refinery)** — Batches polecat MRs, runs gates on the merged stack, and bisects on failure to isolate and isolate the bad MR while merging the rest (`README.md:556-566`). Nothing comparable in Overcode or any other competitor in the bakeoff list.

2. **Persistent polecat identity decoupled from session** (`internal/polecat/types.go:8-62`) — Polecats survive work completion (`StateIdle`) and keep their worktrees/mailboxes so they can be reassigned without re-cloning or rebuilding context. This is different from "resume a session" — it's "same worker, new session, memory preserved."

3. **Seance (cross-session predecessor querying)** — `gt seance --talk <id> -p "What did you find?"` lets an agent interrogate a predecessor session's `.events.jsonl` and derive context from prior work without re-reading the codebase (`README.md:581-590`).

4. **Mail with delivery modes** (`internal/mail/types.go:50-62`) — `DeliveryQueue` (agent polls) vs `DeliveryInterrupt` (inject system-reminder directly into running session) + priority + threading + channels + typed messages (task/escalation/scavenge/notification/reply). Full messaging stack between agents.

5. **Wasteland federation** (`README.md:593-604`) — Gas Towns on different machines coordinate work via DoltHub: post wanted items, claim others' work, submit completion evidence, earn multi-dimensional reputation stamps (quality, speed, complexity). A work *market* not just a supervisor.

6. **Four-tier watchdog chain** (`README.md:528-533`): Go Daemon (3-min heartbeat) → Boot (AI triage) → Deacon (AI patrol across rigs) → Witnesses (per-rig) + Refineries. Each tier narrows scope and adds intelligence.

7. **Formulas + Molecules** — TOML-defined workflow templates with DAG dependencies (`needs =`), variable substitution, and two execution modes (root-only lightweight wisps vs poured wisps with checkpoint recovery, `README.md:83-84`, `docs/concepts/molecules.md`). A declarative workflow engine inside the CLI.

8. **Beads (git-backed issue DB)** — Work state is structured data in git, not prose in agent memory. Convoys bundle beads; escalations *create* beads; mail messages *are* beads. Everything is queryable SQL (`sqlite3` prereq).

9. **Severity-routed escalation** (`README.md:98-100`) — Agents hit blockers and call `gt escalate -s HIGH "desc"`; CRITICAL/HIGH/MEDIUM routes to Deacon/Mayor/Overseer with tracked beads. A blocker becomes a work item automatically.

10. **Hooks override chain** (`docs/HOOKS.md:26-40`) — `base → role → rig+role` merge for per-rig/per-role Claude/Copilot settings. Lets you configure e.g. "witnesses in `gastown` rig get a different toolset than crew in `myproject`" without editing files by hand.

## What This Tool Does Better Than Overcode

- **Worktree isolation + bisecting merge queue**: Overcode has zero isolation and no merge workflow. Refinery is production-grade and a category Overcode doesn't even attempt.
- **Agent-agnostic runtime**: 10 built-in agent presets (claude/gemini/codex/cursor/auggie/amp/opencode/copilot/pi/omp) vs Overcode's Claude-only.
- **Inter-agent mail with interrupt vs queue delivery**: Overcode has supervisor→agent but no agent↔agent mail, and no queue vs interrupt distinction.
- **Persistent agent identity decoupled from session**: Overcode conflates Session and identity; GasTown's polecats live forever and can be re-spawned with preserved history.
- **Seance (predecessor session querying)**: Cross-session memory transfer with explicit dialog is unique.
- **Wasteland federation**: Cross-machine work market. Overcode's Sister is monitoring, not coordination.
- **Beads as structured work state**: SQL-queryable issues with explicit schema beats unstructured prose-in-session.
- **Formulas / Molecules**: Declarative TOML DAG workflows with checkpoint recovery. Overcode has no workflow engine.
- **Severity-routed escalation**: Blockers become tracked work items and auto-escalate. Overcode has no equivalent.
- **OpenTelemetry-native**: Out-of-the-box OTLP export. Overcode exports Parquet; OTEL plugs into existing observability stacks.
- **Plugin system**: 13 shipped plugins + design doc. Overcode's plugin surface is smaller.
- **Web dashboard with htmx command palette**: Browser-driven command execution. Overcode's dashboard is read-mostly.
- **Documentation depth**: Design docs for every subsystem (convoy, polecat-lifecycle-patrol, escalation, scheduler, otel, plugin-system, wasteland).

## What Overcode Does Better

- **Instant-status via 442 regex polling patterns**: Free (no tokens) and lower-latency than Deacon's AI-reasoning patrol. GasTown's supervisors cost tokens to think.
- **Standing-instructions library (25 presets)**: Tighter and less bureaucratic than GasTown's directive/mail/nudge layering.
- **Per-agent cost budgets with soft enforcement**: Overcode can kill/skip on budget; GasTown caps *concurrency* via scheduler but doesn't appear to cap per-agent spend.
- **Timeline view and configurable columns in TUI**: GasTown's bubbletea feed is simpler.
- **Simpler mental model**: Overcode is one machine, one repo, many Claude agents. GasTown's learning curve includes Mayor/Polecat/Witness/Deacon/Refinery/Convoy/Bead/Hook/Molecule/Formula/Seance/Wasteland/Dolt — real onboarding cost.
- **Fork-with-context parent/child trees 5 levels deep**: More explicit sub-agent spawning from a parent session than GasTown's flat polecat model.
- **Single binary to install and run** (effectively — `pip install`): No Dolt server, no beads binary, no `bd`, no DoltHub account required.
- **Python-native**: Easier to hack on for most Overcode users than Go.
- **Host selector for remote agents**: Overcode's recently-unified `n` hotkey with host-step flow is smoother than GasTown's rig-centric model for "launch on this machine."
- **Claude Code hooks as *cheap* authoritative status**: Overcode uses the same mechanism but pairs it with regex polling rather than AI reasoning — cheaper per-agent.

## Ideas to Steal

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **Bisecting merge queue (Refinery)** for landing multiple agents' work | High | High | The killer feature. Batch completed agents, run tests on merged stack, bisect on failure. Requires worktree isolation first. See `README.md:556-566` and `internal/refinery/`. |
| **Persistent agent identity decoupled from session** — reuse worktree + history across sessions | High | Med | Today Overcode's `Session` conflates both. Split into `Agent` (persistent: worktree, mailbox, history) and `Session` (ephemeral: pid, tmux pane). Polecat state enum at `internal/polecat/types.go:8-62` is a good blueprint. |
| **Predecessor-session querying (Seance-style)** via `.events.jsonl` | High | Med | Let a new agent ask "what did my predecessor in this worktree try?" without rereading the repo. Overcode's compact/side-question subagent logs (see recent commit `4f9be06`) are already the right substrate. |
| **Mail with `queue` vs `interrupt` delivery modes** for instructions | High | Med | Current Overcode supervisor pushes instructions synchronously. Add a queue that the agent drains at hook-points, and reserve injection for urgent items. `internal/mail/types.go:50-62` is a clean model. |
| **Severity-routed escalation** — `gt escalate -s HIGH` creates a tracked blocker bead | Med | Med | When an Overcode agent gets stuck, today the human notices via status. Instead, give agents a `blocked` command that creates a supervisor task and surfaces in the TUI with severity routing. |
| **Formulas (TOML DAG workflows with `needs =`)** for repeatable multi-step tasks | Med | Med | Overcode's standing-instruction presets are flat. TOML formulas with variable substitution and dep graphs are a natural upgrade for "release", "triage", "security-review" recurring workflows. |
| **Hooks override chain** (`base → role → rig+role`) for per-project settings | Med | Low | Overcode has per-project config but not a merged override chain. Lets power users keep a `base` + small overrides per repo and role. |
| **OpenTelemetry-native telemetry** instead of Parquet | Med | Med | OTLP exporters plug into Grafana/VictoriaMetrics out of the box. Keep Parquet for long-term archival, add OTLP for live dashboards. |
| **Problems view** in the TUI (dedicated stuck-agent triage screen, keys `n`/`h` for nudge/handoff) | Med | Low | Overcode already detects stalled agents; a dedicated view grouping by GUPP-violation/Stalled/Zombie/Working/Idle would reduce cognitive load at 10+ agents. `internal/tui/feed/stuck.go` and `keys.go:101-111`. |
| **Plugin directory with explicit plugin API** (`plugins/*/`) | Med | High | 13 shipped plugins show the value. Dogs like `stuck-agent-dog` and `compactor-dog` are essentially cron-like maintenance agents — Overcode's heartbeat could become a dispatch mechanism. |
| **Dashboard command palette** (htmx, runs `gt` commands from browser) | Low | Med | Overcode dashboard is read-mostly. Add a `/` palette that can run `kill/fork/send-instruction/budget-update` without touching the TUI. |
| **Cost digests persisted as beads (issues)** | Low | Low | `gt costs digest` rolls up daily spend into a bead. Overcode could persist daily cost summaries as supervisor tasks/reports rather than only live dashboards. |
| **Shell completions (bash/zsh/fish)** for Overcode's CLI | Low | Low | `gt completion bash|zsh|fish`. Easy to add, nice polish. |
| **Wasteland federation** — cross-machine work market | Low | High | Cool idea, but Overcode's Sister already covers the monitoring use case. Full work-claiming with reputation stamps is speculative territory. |
