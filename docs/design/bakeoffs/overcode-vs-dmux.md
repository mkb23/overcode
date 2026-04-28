# Overcode vs dmux: Feature Bakeoff

## Overview

| | **dmux** | **Overcode** |
|---|---|---|
| **Repo** | [standardagents/dmux](https://github.com/standardagents/dmux) | This project |
| **Language** | TypeScript (Ink/React TUI) | Python (Textual TUI) |
| **Purpose** | Parallel AI agent orchestrator with git worktree isolation | Claude Code supervisor/monitor with instruction delivery |
| **Agent Support** | 11 agents (Claude, Codex, Gemini, Cline, etc.) | Claude Code only |
| **Isolation Model** | Git worktrees (one branch per agent) | Shared repo (agents work on same files) |
| **TUI Style** | Narrow sidebar (40-char) alongside agent panes | Full-screen dashboard with optional split |
| **Status Detection** | Worker threads + LLM analysis (OpenRouter) | Regex polling + Claude Code hooks |
| **Autonomy** | Autopilot with LLM risk assessment | Supervisor daemon (Claude-powered approval) |

---

## Core Philosophy Differences

**dmux** is built around **parallel isolation** — every agent gets its own git worktree on its own branch, so multiple agents can edit files simultaneously without conflicts. The workflow is: spawn agents → they work in parallel → merge results back. It's agent-agnostic, supporting 11 different AI CLI tools.

**Overcode** is built around **supervision and orchestration** — it's a control tower for Claude Code agents. Agents share the same repo, and the emphasis is on monitoring status, delivering instructions, managing budgets, tracking costs, and having a supervisor daemon that can approve/redirect agents. It's Claude Code-specific, deeply integrated with Claude's hooks and session model.

---

## Feature-by-Feature Comparison

### Agent Launching & Management

| Feature | dmux | Overcode |
|---|---|---|
| Multi-agent support | 11 agents (Claude, Codex, Gemini, Cline, OpenCode, Amp, etc.) | Claude Code only |
| Agent isolation | Git worktree per agent (full file isolation) | Shared repo (agents can conflict) |
| Branch management | Auto-creates branch per agent with AI-generated slug | Manual or shared branch |
| Merge workflow | Built-in merge with AI conflict resolution | "Sync to main" (reset + pull) |
| Sub-worktrees | Worktrees-from-worktrees with cascade merge | Not applicable |
| Resume sessions | Resume previous worktree branches | Resume via `--resume` flag |
| Multi-agent per workspace | Attach multiple agents to same worktree | N/A (agents share repo inherently) |
| Agent hierarchy | Flat (all panes are peers) | Parent/child tree (5 levels deep) |
| Fork with context | Not supported | Fork inherits full conversation |
| Permission modes | bypassPermissions / acceptEdits / plan | normal / permissive / bypass |
| Model selection | Per-agent type | `--model` flag (haiku/sonnet/opus) |

**dmux wins**: Multi-agent support, git worktree isolation, merge workflow
**Overcode wins**: Agent hierarchy, conversation forking, Claude-deep integration

### Status Detection

| Feature | dmux | Overcode |
|---|---|---|
| Primary method | Worker threads (1s polling) + LLM classification | Regex pattern matching (442 lines) |
| Secondary method | Heuristic fingerprinting | Claude Code hooks (authoritative, instant) |
| LLM analysis | OpenRouter (3-model fallback: Gemini Flash → Grok 4 → GPT-4o-mini) | Not used for detection |
| Classification | `option_dialog` / `open_prompt` / `in_progress` | 10+ statuses (running, waiting_user, waiting_approval, error, etc.) |
| Risk assessment | LLM evaluates risk of auto-accepting | Not applicable |
| Latency | ~1s polling + LLM roundtrip when settled | ~0ms (hooks) or 2-10s (polling) |
| Cost | Requires OpenRouter API key (uses free/cheap models) | Free (hooks are local) |

**dmux wins**: Agent-agnostic detection (works with any CLI tool), risk assessment
**Overcode wins**: Zero-cost detection, richer status taxonomy, hook-based instant detection for Claude

### Autonomy & Supervision

| Feature | dmux | Overcode |
|---|---|---|
| Auto-accept | Autopilot with LLM risk assessment | Supervisor daemon (Claude-powered) |
| Standing instructions | Not supported | 25 presets + custom per-agent |
| Heartbeat | Not supported | Periodic instruction delivery to idle agents |
| Budget enforcement | Not supported | Per-agent $ budgets with soft enforcement |
| Oversight mode | Not supported | Follow mode with stuck detection + timeout |
| Intervention history | Not tracked | Logged per agent |

**Overcode wins decisively**: The entire supervision layer (standing instructions, heartbeat, budgets, oversight) has no equivalent in dmux. dmux's autopilot is simpler — it just auto-accepts safe permission prompts.

### TUI & UX

| Feature | dmux | Overcode |
|---|---|---|
| Layout | 40-char sidebar + agent panes side-by-side | Full-screen dashboard or split (top/bottom) |
| Framework | Ink (React for terminal) | Textual (Python) |
| Agent visibility | Hide/show individual panes, isolate by project | Hide sleeping/terminated/done agents |
| File browser | Built-in with syntax highlighting + git diff | Not built-in |
| Preview pane | Agents visible directly in tmux panes | Terminal output preview below agent list |
| Timeline view | Not supported | Status history bars (color-coded) |
| Keybindings | ~15 keys | ~50+ keys |
| Column config | Not supported | Configurable columns |
| Sort modes | Not supported | 4 modes (alpha, status, value, tree) |
| Copy mode | Not supported | Toggle mouse/text selection |
| Command bar | Prompt input for new agents | Rich command bar with multi-line, history |
| Notifications | macOS native (Swift helper) with custom sounds | Not supported |
| Popup system | Agent picker, settings, file browser | Modals for various actions |

**dmux wins**: Side-by-side pane layout (see all agents at once), file browser, native notifications
**Overcode wins**: Richer dashboard (timeline, columns, sorting), more keybindings, command bar

### Configuration & Ecosystem

| Feature | dmux | Overcode |
|---|---|---|
| Config format | JSON (project + global) | YAML (global) |
| Lifecycle hooks | 11 hook types (before/after pane, worktree, merge) | Claude Code hook integration |
| Web dashboard | Vue-based (in development) | Full HTTP API + analytics dashboard |
| Remote/multi-machine | Multi-project in single session | Sister integration (cross-machine monitoring) |
| Data export | Not supported | Parquet export for Jupyter analysis |
| Cost tracking | Not supported | Per-agent tokens, dollars, joules display |
| Presence tracking | macOS focus detection (Swift helper) | macOS idle/lock detection + CSV logging |
| Cloud relay | Not supported | Push state to remote endpoint |

**Overcode wins**: Web dashboard, cost tracking, data export, sister integration, cloud relay

---

## Unique Strengths

### dmux's Unique Strengths

1. **Git worktree isolation** — The killer feature. Each agent works on its own branch/copy, eliminating file conflicts entirely. This is the correct architecture for true parallel agent work.
2. **Agent-agnostic** — Supports 11 different AI CLI tools. Not locked into one ecosystem.
3. **Merge workflow** — AI-assisted merge conflict resolution, sub-worktree chains, cascade merging. Full git workflow built in.
4. **Side-by-side visibility** — All agent panes visible simultaneously in tmux (not behind tabs).
5. **Native macOS notifications** — Swift helper with 10 custom sounds, focus-aware attention system.
6. **Autopilot with risk assessment** — LLM evaluates whether to auto-accept, not just blindly approving.
7. **AI branch naming** — Generates meaningful branch names from prompts.

### Overcode's Unique Strengths

1. **Supervisor daemon** — A meta-agent (Claude) that monitors and directs other agents. Standing instructions, heartbeat, approval workflows.
2. **Agent hierarchy** — Parent/child trees with cascade kill, budget inheritance, follow mode, oversight timeouts.
3. **Cost/budget management** — Per-agent budgets, token tracking, cost display in dollars/tokens/joules, budget transfer between agents.
4. **Hook-based detection** — Zero-cost, instant, authoritative status from Claude Code's own hooks.
5. **Web dashboard + API** — Full HTTP control plane with analytics, timeline, presence overlay.
6. **Sister integration** — Aggregate agents across multiple machines into one view.
7. **Data export** — Parquet export for offline analysis in Jupyter.
8. **Conversation forking** — Create child agent inheriting full conversation context.
9. **Standing instruction presets** — 25 built-in presets covering common workflows.
10. **Rich status taxonomy** — 10+ distinct states with nuanced transitions.

---

## Ideas Each Could Bring to the Other

### Ideas dmux could bring to Overcode

| Idea | Value | Complexity |
|---|---|---|
| **Git worktree isolation** | Huge — eliminates file conflicts between parallel agents | High — fundamental architecture change |
| **Multi-agent support** | Medium — opens to Codex, Gemini, etc. | High — status detection is Claude-specific |
| **Merge workflow** | High — structured way to combine agent work | Medium — git operations are well-understood |
| **AI branch naming** | Low-medium — nice UX polish | Low — simple API call |
| **Side-by-side pane layout** | Medium — better visibility of multiple agents | Medium — tmux layout management |
| **Built-in file browser** | Low — users have editors | Low-medium |
| **Native notifications** | Medium — attention management | Medium — requires platform-specific code |
| **Autopilot risk assessment** | Medium — smarter auto-approval | Low — could enhance supervisor daemon |

### Ideas Overcode could bring to dmux

| Idea | Value | Complexity |
|---|---|---|
| **Supervisor daemon** | Huge — automated agent management | High — needs instruction delivery + approval flow |
| **Standing instructions** | High — guide agents without manual intervention | Medium — per-pane config + delivery mechanism |
| **Heartbeat system** | High — keep idle agents productive | Medium — timer + instruction delivery |
| **Cost/budget tracking** | High — essential for managing agent spend | Medium — token extraction from agent history |
| **Agent hierarchy** | Medium — useful for complex task decomposition | Medium-high |
| **Web dashboard + API** | Medium — remote monitoring, mobile access | Medium |
| **Hook-based detection** | Medium — faster/cheaper than LLM for Claude agents | Low — read hook state files |
| **Sister/multi-machine** | Medium — scale beyond one machine | Medium |
| **Data export/analytics** | Low-medium — useful for cost analysis | Low |
| **Timeline visualization** | Medium — understand agent patterns over time | Low-medium |
| **Conversation forking** | Medium — branch conversations like branches | Low (for Claude agents) |

---

## The Big Takeaway

These tools are **complementary more than competitive**. They solve different problems in the multi-agent workflow:

- **dmux** solves the **isolation and parallelism** problem — how do you let multiple agents edit code simultaneously without stepping on each other? Git worktrees + merge workflow.
- **Overcode** solves the **supervision and orchestration** problem — how do you keep agents productive, on-track, within budget, and properly directed? Supervisor daemon + standing instructions + heartbeat + budgets.

The ideal tool would combine both: **worktree-isolated agents** (dmux's model) with **rich supervision and cost management** (Overcode's model). The most impactful cross-pollinations would be:

1. **Overcode adopting git worktree isolation** — This would be transformative for parallel agent work. Currently, Overcode agents sharing a repo is its biggest limitation for parallelism.
2. **dmux adopting a supervision/instruction layer** — dmux has no way to give agents ongoing direction. A heartbeat + standing instructions system would make its autopilot much more powerful.
3. **dmux adopting cost tracking** — Running 11 different agents with no budget visibility is risky.
4. **Overcode adopting a merge workflow** — If agents work on worktrees, you need structured merge-back.
