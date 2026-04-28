# Bakeoff Candidates & Analysis Template

## Candidate List

### Tier 1: High Priority (most comparable / most to learn from)

| # | Tool | Repo | Why Analyze |
|---|---|---|---|
| 1 | **Claude Squad** | [smtg-ai/claude-squad](https://github.com/smtg-ai/claude-squad) | Most direct competitor. Same niche (TUI + tmux + Claude Code). ~6,100 stars. Go. Worktree isolation model. |
| 2 | **GasTown** | [steveyegge/gastown](https://github.com/steveyegge/gastown) | Steve Yegge's project. Persistent agent identity, feudal hierarchy, inter-agent comms, watchdog chain. Most ambitious vision. |
| 3 | **Composio Agent Orchestrator** | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | ~5,600 stars. Fully autonomous AI orchestrator that decomposes tasks. Tests whether Overcode's human-in-loop model is the right abstraction. |
| 4 | **Kagan** | [kagan-sh/kagan](https://github.com/kagan-sh/kagan) | Kanban board + GitHub Issues sync + code review workflow. Project-management angle Overcode completely lacks. |
| 5 | **Superset** | [superset-sh/superset](https://github.com/superset-sh/superset) | Full GUI IDE for agents. Tests whether terminal-native is the right form factor. Corporate adoption at Amazon, Google. |

### Tier 2: Worth Analyzing

| # | Tool | Repo | Why Analyze |
|---|---|---|---|
| 6 | **cmux** | [manaflow-ai/cmux](https://github.com/manaflow-ai/cmux) | ~7,700 stars. Native macOS terminal app. Scriptable in-app browser. Different form factor. |
| 7 | **Bernstein** | [chernistry/bernstein](https://github.com/chernistry/bernstein) | Fellow Python project. Deterministic scheduling, test verification gates, circuit breaker. |
| 8 | **CCManager** | [kbwo/ccmanager](https://github.com/kbwo/ccmanager) | ~940 stars. Context transfer between worktrees, devcontainer sandboxing, session status hooks. |
| 9 | **Vibe Kanban** | [BloopAI/vibe-kanban](https://github.com/BloopAI/vibe-kanban) | Kanban-first approach, inline code review, built-in browser with devtools. |
| 10 | **Orca** | [stablyai/orca](https://github.com/stablyai/orca) | ~980 stars. Worktree-native IDE, built-in source control, GitHub integration per worktree. |

### Already Completed

| Tool | Repo | File |
|---|---|---|
| **dmux** | [standardagents/dmux](https://github.com/standardagents/dmux) | [overcode-vs-dmux.md](overcode-vs-dmux.md) |
| **Orca** | [stablyai/orca](https://github.com/stablyai/orca) | [overcode-vs-orca.md](overcode-vs-orca.md) |
| **Bobbit** | [SuuBro/bobbit](https://github.com/SuuBro/bobbit) | [overcode-vs-bobbit.md](overcode-vs-bobbit.md) |

### Noted but Not Prioritized

These are smaller, niche, or too different in scope to warrant a full bakeoff:

| Tool | Repo | Notes |
|---|---|---|
| Multi-Agent-Shogun | [yohey-w/multi-agent-shogun](https://github.com/yohey-w/multi-agent-shogun) | YAML-driven hierarchical coordination |
| kodo | [ikamensh/kodo](https://github.com/ikamensh/kodo) | Gemini Flash as cheap orchestrator for overnight autonomy |
| orc | [safethecode/orc](https://github.com/safethecode/orc) | Tournament-based competing strategies (WIP) |
| amux | [mixpeek/amux](https://github.com/mixpeek/amux) | Lightweight, REST API + shared memory |
| agent-deck | [asheshgoplani/agent-deck](https://github.com/asheshgoplani/agent-deck) | Simple TUI session manager |
| Mato | [mr-kelly/mato](https://github.com/mr-kelly/mato) | Terminal multiplexer replacement with activity signals |
| AWS CLI Agent Orchestrator | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | AWS-backed, MCP inter-agent communication |
| Automagik Forge | [automagik-dev/forge](https://github.com/automagik-dev/forge) | Multi-agent kanban with MCP |
| Agent Kanban | [saltbo/agent-kanban](https://github.com/saltbo/agent-kanban) | Agents as first-class kanban team members |
| AgentsRoom | [agentsroom.dev](https://agentsroom.dev) | macOS IDE for Claude agents (commercial) |

### Platform-Native Features (not standalone tools)

| Feature | Notes |
|---|---|
| **Claude Code Agent Teams** | Built-in since v2.1.32. 1 lead + 2-16 teammates, file locking, peer messaging. |
| **GitHub Copilot CLI /fleet** | Decomposes objectives into parallel subagents behind the scenes. |

---

## Analysis Template

Each bakeoff agent should follow this template exactly. The goal is a comprehensive feature dump that can later be combined into comparison tables across all tools.

### Instructions for Analysis Agents

You are analyzing a competitor tool to compare against Overcode (a Python/Textual TUI for supervising multiple Claude Code agents in tmux). Your job is to produce a **thorough, structured feature inventory** of the target tool.

#### Step 1: Clone and Explore

1. Clone the repo to `~/Code/{tool-name}` if not already present
2. Read the README, docs, and any wiki/guides
3. Read the key source files — entry points, CLI commands, TUI/UI code, config handling
4. Check package manifests (package.json, pyproject.toml, go.mod, Cargo.toml) for dependencies
5. Look at test files for usage patterns and edge cases
6. Check GitHub issues/discussions for roadmap and known limitations

#### Step 2: Produce the Feature Inventory

Write your output as a markdown file at `docs/design/bakeoffs/overcode-vs-{tool-slug}.md` following the structure below. Every section is **mandatory** — if a feature doesn't exist in the tool, say "Not supported" rather than omitting the section.

```markdown
# Overcode vs {Tool Name}: Feature Bakeoff

## Overview

| | **{Tool Name}** | **Overcode** |
|---|---|---|
| **Repo** | [link](url) | This project |
| **Language** | {lang} | Python (Textual TUI) |
| **Stars** | {count} | N/A (private) |
| **License** | {license} | Proprietary |
| **First Commit** | {date} | 2025 |
| **Last Commit** | {date} | Active |
| **Purpose** | {1-sentence} | Claude Code supervisor/monitor with instruction delivery |

## Core Philosophy

{2-3 paragraphs on the tool's design philosophy, target user, and mental model.
How does it think about agents? Sessions? Work? What's the core workflow loop?}

## Feature Inventory

### 1. Agent Support
- Which AI CLI agents are supported? (list all with version/detection info)
- How are new agents added? (plugin system? hardcoded registry?)
- Is it locked to one agent or agent-agnostic?

### 2. Agent Launching
- How are agents created? (CLI command, TUI action, API, etc.)
- What inputs are required? (prompt, model, permissions, branch, etc.)
- Can you launch with a pre-written prompt? From a file?
- How is the initial prompt delivered? (CLI arg, stdin, tmux send-keys, etc.)
- Are there templates or presets for launch configs?

### 3. Session/Agent Lifecycle
- What states can an agent be in? (list all)
- How are sessions persisted? (files, database, in-memory?)
- Can sessions survive process restarts? TUI restarts? Machine reboots?
- Resume/reattach support?
- Cleanup behavior on close/kill?

### 4. Isolation Model
- How are agents isolated from each other? (worktrees, containers, nothing?)
- Branch management (auto-create, naming, prefix?)
- Can multiple agents share a workspace?
- Merge workflow (manual, AI-assisted, automated?)
- Sub-task / sub-worktree support?

### 5. Status Detection
- How does the tool know what an agent is doing?
- Polling? Hooks? LLM analysis? Heuristics?
- What statuses are detected? (list all)
- Latency from state change to detection?
- Cost of detection (API calls, tokens)?

### 6. Autonomy & Auto-Approval
- Can agents run unattended?
- Auto-accept/auto-approve mechanisms?
- Risk assessment before auto-accepting?
- Permission/safety modes?

### 7. Supervision & Instruction Delivery
- Can you send instructions to running agents?
- Standing instructions / persistent directives?
- Heartbeat / periodic instruction delivery?
- Supervisor daemon or meta-agent?
- Intervention history / logging?

### 8. Cost & Budget Management
- Token tracking?
- Cost calculation? (which pricing models?)
- Per-agent budgets?
- Budget enforcement (hard kill vs soft skip)?
- Cost display (tokens, dollars, other units)?

### 9. Agent Hierarchy & Coordination
- Parent/child relationships?
- Agent-to-agent communication?
- Task decomposition (manual or AI-driven)?
- Cascade operations (kill, budget)?
- Follow/oversight modes?

### 10. TUI / UI
- What kind of interface? (TUI, GUI, web, CLI-only?)
- Framework used?
- Layout model (sidebar, fullscreen, split, tabs?)
- Key features visible in the UI
- Keyboard shortcuts (list all)
- Customization (columns, sort, themes?)

### 11. Terminal Multiplexer Integration
- Which multiplexer? (tmux, zellij, screen, custom, none?)
- How are panes/windows managed?
- Layout calculation?
- Can you see agent output live?
- Split/zoom/focus behavior?

### 12. Configuration
- Config file format and location
- Per-project vs global settings
- Key configurable options (list all)
- Environment variables
- Lifecycle hooks / event system?

### 13. Web Dashboard / Remote Access
- Web UI available?
- API endpoints?
- Remote monitoring (multi-machine)?
- Mobile-friendly?

### 14. Git / VCS Integration
- Branch management
- Commit automation
- PR creation
- Merge conflict resolution
- GitHub/GitLab integration

### 15. Notifications & Attention
- How does the tool alert the user?
- Desktop notifications? Sound? Visual?
- Attention prioritization?

### 16. Data & Analytics
- Session history / archival?
- Data export formats?
- Analytics / metrics dashboards?
- Presence / activity tracking?

### 17. Extensibility
- Plugin / hook system?
- MCP server support?
- API for external tools?
- Custom agent definitions?

### 18. Developer Experience
- Install process
- First-run experience / onboarding
- Documentation quality
- Test coverage / CI

## Unique / Notable Features

{List 5-10 features that are genuinely unique or unusually well-done in this tool.
For each, explain what it does and why it matters.}

## What This Tool Does Better Than Overcode

{Honest assessment. What concrete features or approaches should Overcode consider adopting?
Be specific — not "better UX" but "the merge workflow auto-commits, creates a PR, and cleans up the worktree in one action."}

## What Overcode Does Better

{Features where Overcode has a clear advantage.
Reference specific Overcode features.}

## Ideas to Steal

{Ranked list of concrete ideas Overcode could adopt, with estimated value and complexity.}

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| ... | High/Med/Low | High/Med/Low | ... |
```

#### Step 3: Quality Checklist

Before finishing, verify:
- [ ] Every section in the template is filled in (use "Not supported" if absent)
- [ ] All CLI commands/subcommands are listed
- [ ] All keyboard shortcuts are listed
- [ ] All config options are listed
- [ ] All agent states/statuses are listed
- [ ] The "Ideas to Steal" table has at least 5 entries
- [ ] Claims are backed by specific file paths or code references from the repo
- [ ] The Overcode comparison columns reference actual Overcode features (not guesses)

#### Notes on Overcode (for comparison)

When filling in the Overcode column, use these facts:
- Python 3.12+ / Textual TUI / tmux backend
- Claude Code only (no other agents)
- Shared repo (no worktree isolation)
- Status detection: regex polling (442 patterns) + Claude Code hooks (instant, authoritative)
- Supervisor daemon (Claude-powered) with standing instructions (25 presets)
- Heartbeat system for periodic instruction delivery
- Per-agent cost budgets with soft enforcement
- Agent hierarchy: parent/child trees, 5 levels deep, cascade kill, fork with context
- Web dashboard + HTTP API with analytics
- Sister integration for cross-machine monitoring
- Data export to Parquet
- ~50+ TUI keybindings, timeline view, configurable columns
- No merge workflow, no worktree isolation, no multi-agent support, no native notifications
