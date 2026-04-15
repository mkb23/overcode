# Zellij vs Tmux: Bakeoff for Overcode

## Executive Summary

We investigated whether overcode could swap tmux for zellij as its terminal multiplexer backend. The short answer: **not yet, but the architecture supports it when zellij matures.**

Overcode's existing `TmuxInterface` protocol abstracts ~70% of tmux usage, making a second backend feasible without rewriting core logic. However, the remaining 30% — linked sessions, keybinding guards, hooks, and the split layout — relies on tmux-specific primitives that have no direct zellij equivalents. The split layout (`overcode tmux`) is the hardest part: it depends on linked sessions sharing a window group, which zellij's fully-isolated session model cannot replicate.

Zellij offers genuine advantages (real-time pane streaming, structured JSON output, built-in session resurrection, better out-of-box UX), but its CLI automation API shipped in v0.44.0 (early 2026) and is largely untested in production automation. The pre-1.0 status, history of breaking changes, performance gap under high-throughput output, and lack of ubiquity on remote servers make it a risky primary backend today.

**Recommendation:** Keep tmux as the primary backend. If zellij demand emerges, implement a second backend behind `TmuxInterface` starting with stacked panes (no plugin needed) and graduating to a WASM plugin for full split-layout parity. Wait for zellij 1.0 and at least one stable release cycle before investing heavily.

---

## Table of Contents

1. [How Overcode Uses Tmux Today](#1-how-overcode-uses-tmux-today)
2. [Zellij Capabilities for Programmatic Control](#2-zellij-capabilities-for-programmatic-control)
3. [Opportunities from Switching to Zellij](#3-opportunities-from-switching-to-zellij)
4. [Disadvantages and Feature Gaps](#4-disadvantages-and-feature-gaps)
5. [Risks](#5-risks)
6. [The Split Layout Problem](#6-the-split-layout-problem)
7. [General Comparison: Zellij vs Tmux](#7-general-comparison-zellij-vs-tmux)
8. [Community Sentiment](#8-community-sentiment)
9. [Recommendation](#9-recommendation)

---

## 1. How Overcode Uses Tmux Today

### Abstraction Layer (8/10 decoupling score)

Overcode has a well-designed abstraction layer with three tiers:

- **Protocol** (`protocols.py`): `TmuxInterface` defines the contract — `capture_pane`, `send_keys`, `has_session`, `new_session`, `new_window`, `kill_window`, `kill_session`, `list_windows`, `attach`, `get_pane_pid`, `select_window`.
- **Implementation** (`implementations.py`): `RealTmux` wraps libtmux with caching (30s TTL).
- **Mock** (`mocks.py`): `MockTmux` enables full unit testing without real tmux.
- **Manager** (`tmux_manager.py`): High-level API supporting dependency injection.

### Raw tmux CLI Usage (Outside the Abstraction)

The following tmux features are used directly via subprocess and are **not** covered by `TmuxInterface`:

| Feature | Where Used | Purpose |
|---|---|---|
| `bind-key -n` with `if-shell` guards | `cli/split.py` | Scope keybindings to split window only |
| Linked sessions (`new-session -t`) | `cli/split.py` | Bottom pane shares agent window group |
| `set-hook` (attach/detach/resize) | `tmux_utils.py`, `tmux_manager.py`, `cli/split.py` | Lifecycle and resize management |
| `load-buffer` / `paste-buffer` | `tmux_utils.py` | Multi-line text injection |
| Custom window options (`@is_ssh_proxy`) | `tmux_manager.py` | Detect SSH proxy windows |
| Format strings (`#{window_name}`, etc.) | `cli/split.py` | Conditional keybinding logic |
| `bind-key -T copy-mode-vi` | `cli/split.py` | Scrollback rebinding for SSH proxies |
| Nested tmux client (`unset TMUX; tmux attach`) | `cli/split.py` | Bottom pane of split layout |

### Key tmux Commands Used

Via libtmux: `new_session`, `new_window`, `kill`, `send_keys`, `capture_pane`, `set_window_option`, `select_window`, `sessions.get`, `windows.get`.

Via subprocess: `new-session`, `kill-session`, `new-window`, `send-keys`, `capture-pane -p -S -e`, `list-windows -F`, `list-keys`, `select-window`, `attach-session`, `has-session`, `show-options`, `set`, `set-hook`, `bind-key`, `unbind-key`, `resize-window`, `load-buffer`, `paste-buffer`.

---

## 2. Zellij Capabilities for Programmatic Control

As of zellij v0.44.x, the CLI automation surface is surprisingly complete:

### Session Management
- `zellij list-sessions` / `zellij ls`
- `zellij attach --create-background my-session` (headless/daemon)
- `zellij kill-session` / `zellij kill-all-sessions`

### Pane/Tab Operations
- `zellij --session s action new-pane --name "worker" -- command` (returns pane ID)
- `zellij --session s action new-tab --name "tests"` (returns tab ID)
- `--blocking`, `--block-until-exit-success`, `--block-until-exit-failure`

### Sending Keystrokes
- `zellij --session s action send-keys --pane-id $ID "Ctrl c" "Enter"`
- `zellij --session s action write-chars --pane-id $ID "text"`
- `zellij --session s action paste --pane-id $ID "cargo build\n"` (bracketed paste)

### Capturing Output
- `zellij --session s action dump-screen --pane-id $ID --full` (point-in-time snapshot)
- `zellij --session s subscribe --pane-id $ID --format json` (real-time NDJSON stream)

### Querying State
- `zellij --session s action list-panes --json` (structured metadata)
- `zellij --session s action list-tabs --json`

### Notable Gaps vs Tmux
- No linked sessions
- No `if-shell` conditional keybinding guards
- No custom user-defined options on panes/windows
- No `wait-for` inter-script synchronization channels
- No equivalent to `load-buffer`/`paste-buffer` flow
- No Python library (libtmux equivalent) — subprocess only
- Concurrent mutations to the same pane are explicitly unsupported

---

## 3. Opportunities from Switching to Zellij

### Real-Time Pane Streaming
Zellij's `subscribe --pane-id $ID --format json` provides NDJSON streaming of pane content. This could eliminate the polling loop in `PollingStatusDetector`, making status detection faster and more efficient.

### Structured JSON Output
`list-panes --json` and `list-tabs --json` return structured data natively — no more parsing tmux format strings.

### Pane-Addressed Commands
`--pane-id $ID` is more explicit than tmux's `session:window.pane` target syntax.

### Built-In Session Resurrection
Serializes layout metadata automatically; can restore sessions after reboot without third-party plugins.

### Blocking Pane Creation
`new-pane -- command --blocking` blocks until the command exits, with exit-code variants. Could simplify job management.

### Better Out-of-Box UX
Discoverable keybindings, floating panes, modern aesthetics — lower barrier for new overcode users.

---

## 4. Disadvantages and Feature Gaps

### Features Overcode Relies On That Zellij Lacks

| Overcode Feature | Tmux Mechanism | Zellij Equivalent |
|---|---|---|
| Linked sessions (split layout) | `new-session -t` sharing window group | No equivalent — sessions are isolated |
| Keybinding guards | `bind-key -n` + `if-shell -F` | WASM plugin required |
| Custom window options | `set @is_ssh_proxy on` | No user-defined metadata on panes |
| Hooks (attach/detach/resize) | `set-hook client-attached/detached/resized` | WASM plugin events |
| Multi-line text injection | `load-buffer` / `paste-buffer` | `paste` action (less battle-tested) |
| Copy-mode rebinding | `bind-key -T copy-mode-vi` | Not configurable at same granularity |

### No Python Library
There is no libtmux equivalent for zellij. All interaction would be via subprocess, losing the object model that `TmuxManager` currently leverages.

### Plugin System Requires WASM
Any automation beyond the CLI (keybinding guards, pane hide/show) requires writing a Rust WASM plugin. This is a much higher bar than tmux's shell-based hooks and conditionals.

---

## 5. Risks

### API Instability (Biggest Risk)
Zellij is pre-1.0 (v0.44.x). The CLI automation features are months old vs tmux's decades. The plugin API has had breaking changes across versions.

### Session Incompatibility Across Versions
Historically, upgrading the zellij binary kills all running sessions. v0.44.0 promises this is fixed via protobuf contracts, but it's unproven. For overcode managing long-running agent sessions, this is serious.

### Concurrent Mutation Safety
Zellij docs warn: "avoid concurrent mutations to the same pane." Overcode sends keys to panes while simultaneously capturing their output — this interleaving is explicitly flagged as unpredictable.

### Performance Under Load
Zellij uses 30-80 MB RSS vs tmux's 2-5 MB. Users report noticeable rendering lag with high-throughput output (compilation logs, CI). Overcode users tail agent output continuously.

### Small Automation User Base
Most zellij users are interactive — the programmatic/automation community is tiny. Bugs in this surface may go unreported longer.

---

## 6. The Split Layout Problem

### How It Works Today (Tmux)

The `overcode tmux` split layout is the most tmux-specific feature. Three sessions collaborate:

```
┌──────────────────────────────────────┐
│  Overcode Monitor TUI (top pane)     │  ← "overcode" session
├──────────────────────────────────────┤
│  Agent Terminal (bottom pane)        │  ← nested tmux client attached to
│  Shows whichever agent is selected   │    "oc-view-agents" linked session
└──────────────────────────────────────┘
```

1. **"agents"** — real agent windows live here.
2. **"oc-view-agents"** — linked session sharing the same window group as "agents". Created with `tmux new-session -s oc-view-agents -t agents`.
3. **"overcode"** — contains the split window. Bottom pane runs `unset TMUX; tmux attach -t oc-view-agents`.

When the TUI navigates to agent X, it calls `tmux select-window -t oc-view-agents:=X`. The nested client instantly shows that agent. Scrollback is real (shared panes), keybindings use `if-shell` guards scoped to the split window, and resize is handled via `client-resized` hooks.

### Zellij Alternatives

#### Approach A: Stacked Panes (CLI-only, no plugin)

```
┌──────────────────────────────────────┐
│  TUI pane (top, tiled)               │
├──────────────────────────────────────┤
│  ┌─ agent-1 (collapsed, 1 row)      │
│  ┌─ agent-2 (collapsed, 1 row)      │
│  ╔═ agent-3 (EXPANDED, fills rest)  ║
│  ║  Full terminal output here        ║
│  ╚═══════════════════════════════════╝
└──────────────────────────────────────┘
```

- Single zellij session. TUI on top, `stacked=true` pane group on bottom.
- Switch agents: `zellij action focus-pane-id terminal_N`.
- **Pro:** No WASM plugin needed. Real scrollback. Simple to implement.
- **Con:** Each collapsed agent costs 1 row of screen space. Degrades past ~10 agents.

#### Approach B: Plugin-Driven Hide/Show (cleanest, requires WASM)

```
┌──────────────────────────────────────┐
│  TUI pane (top, tiled)               │
├──────────────────────────────────────┤
│  agent-3 (visible, full height)      │
│  Other agents hidden via plugin      │
└──────────────────────────────────────┘
```

- A small Rust WASM plugin (~100 LOC) manages visibility.
- TUI signals the plugin via `zellij action pipe --name overcode --payload "terminal_5"`.
- Plugin calls `hide_pane_with_id(current)` then `show_pane_with_id(new, false, true)`.
- **Pro:** Zero screen overhead. Closest to current tmux UX. Plugin is small and stable.
- **Con:** Requires shipping a compiled WASM binary. Plugin API is pre-1.0.

#### Approach C: Tab Switching (simplest, different paradigm)

- Each agent in its own tab. TUI in a separate tab.
- **Breaks the core UX** — can't see TUI and agent simultaneously.

#### Approach D: Floating Pane Overlay

- Agents as floating panes positioned over the bottom portion.
- Still requires a plugin for individual hide/show.
- Adds complexity over Approach B for no clear benefit.

### Approach Comparison

| | Stacked (A) | Plugin (B) | Tabs (C) | Floating (D) |
|---|---|---|---|---|
| Split view preserved | Yes | Yes | No | Sort of |
| Screen overhead per agent | 1 row each | Zero | Zero | Zero (w/ plugin) |
| Requires WASM plugin | No | Yes (~100 LOC) | No | Yes |
| Agent limit before UX degrades | ~10 | Unlimited | Unlimited | Unlimited |
| Keybinding scoping | Global only | Plugin can scope | Global only | Plugin can scope |
| Closest to current tmux UX | Close | Closest | Different | Awkward |

---

## 7. General Comparison: Zellij vs Tmux

### Where Zellij Wins

- **First-hour experience.** Status bar with keybinding hints. No prefix key to learn. Beginners are productive immediately.
- **Declarative layouts.** KDL layout files define workspaces cleanly vs tmux's scripted approach.
- **Floating panes.** First-class, not bolted on.
- **Session resurrection.** Built in, no third-party plugins.
- **Modern architecture.** Rust, WASM plugin sandboxing, protobuf IPC.
- **Structured output.** JSON from CLI commands natively.

### Where Tmux Wins

- **Performance.** 2-5 MB RSS, fast rendering even over slow SSH. Zellij is 30-80 MB with noticeable lag under high-throughput output. This is the #1 reason people switch back.
- **Ubiquity.** On virtually every Linux server. SSH into any box and it's there.
- **Scriptability.** 19 years of automation API. `send-keys`, `capture-pane`, `wait-for`, `if-shell`, format strings, hooks — a complete programmatic interface.
- **Stability.** Almost never crashes. Config from 2015 still mostly works. Zellij is pre-1.0 with breaking changes.
- **Ecosystem.** vim-tmux-navigator, TPM, hundreds of plugins, thousands of blog posts and answers.
- **Configuration depth.** Virtually everything is configurable, albeit with arcane syntax.

### Performance Comparison

| Dimension | Tmux | Zellij |
|---|---|---|
| Memory (RSS) | ~2-5 MB | ~30-80 MB |
| Rendering under load | Excellent | Noticeable lag |
| SSH performance | Excellent | Adequate |
| Startup time | Instant | ~100ms |

### User Profiles

| Prefers Tmux | Prefers Zellij |
|---|---|
| Sysadmins, DevOps, heavy SSH users | Local-first developers |
| Long-time Unix users with muscle memory | Newer developers wanting quick productivity |
| Anyone scripting/automating terminal workflows | People who value aesthetics and modern UX |
| High-throughput scenarios (CI, builds, logs) | Declarative workspace/layout enthusiasts |
| vim-tmux-navigator users | People coming from GUI editors |

---

## 8. Community Sentiment

### What People Love About Zellij
- "I switched and never looked back — the discoverability alone is worth it."
- Floating panes are frequently praised.
- Layout system is called "what tmuxinator should have been."
- Active development with a responsive maintainer.

### What People Love About Tmux
- "It's everywhere. Every server I SSH into has it."
- Speed over SSH is unmatched.
- Decades of stability; muscle memory transfers.
- Deep integration ecosystem (vim-tmux-navigator, etc.).

### Why People Switch Back from Zellij
- Performance with high-output commands.
- Missing scriptability features.
- Zellij not available on remote servers.
- Modal system conflicts with neovim keybindings.
- Breaking changes between versions.

### The Skeptic Take
- "Tmux's learning curve is a one-time cost; zellij's performance penalty is ongoing."
- "WASM plugins are a solution in search of a problem."
- "I don't need my terminal multiplexer to hold my hand."

---

## 9. Recommendation

### Short Term: No action needed

Tmux remains the right choice for overcode. Its scriptability, performance, and stability are exactly what overcode needs for reliable programmatic control of agent sessions.

### Medium Term: Maintain the abstraction

The existing `TmuxInterface` protocol is well-positioned for a future dual-backend architecture. Continue routing new tmux usage through the protocol where possible to keep the abstraction score high.

### Long Term: Revisit after zellij 1.0

If zellij reaches 1.0 with a stable CLI automation API and addresses the performance gap, implement a `ZellijBackend` behind `TmuxInterface`:

1. **Phase 1:** Core operations (capture, send-keys, session/window CRUD) via CLI subprocess.
2. **Phase 2:** Stacked panes for split layout (no plugin, works for < 10 agents).
3. **Phase 3:** WASM plugin for full split-layout parity (hide/show, keybinding scoping).

The trigger to begin this work would be: zellij 1.0 ships, the `dump-screen` / `subscribe` / `pipe` APIs survive a stable release without breaking changes, and user demand materializes.
