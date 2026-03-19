# ACP (Agent Client Protocol) Integration Analysis for Overcode

**Document Type:** Technical Design Analysis
**Date:** March 2026
**Status:** Reference / Investigation
**Author:** Claude Code Investigation

---

## Executive Summary

This document analyzes the feasibility and desirability of integrating **Agent Client Protocol (ACP)** as a mechanism for monitoring and controlling Claude Code agents via overcode.

**Key Finding:** ACP is a partial fit. It provides ~25-30% of overcode's observability features out-of-the-box, but lacks the metrics, control, and multi-agent orchestration capabilities that overcode currently provides via its custom sister protocol.

**Recommendation:** Use ACP for read-only observability (terminal output, file tracking, session state); maintain custom sister protocol for agent lifecycle control and metrics.

---

## Background: Two Different ACPs

Confusion exists because "ACP" refers to **two distinct protocols**:

### 1. Agent Communication Protocol (Agent-to-Agent)
- **Scope:** Agent-to-agent communication
- **Transport:** REST (HTTP)
- **Use Case:** Agents discovering and collaborating with each other
- **Status:** Recent merging into Linux Foundation's A2A umbrella
- **Not** what Claude Code implements

### 2. Agent Client Protocol (Editor-to-Agent)
- **Scope:** Code editor ↔ coding agent communication
- **Transport:** JSON-RPC 2.0 over stdin/stdout
- **Use Case:** IDE integration (Zed, VS Code extensions)
- **Status:** Standard being adopted by multiple agent frameworks
- **This is** what Claude Code implements

**This document focuses on Agent Client Protocol (the editor-agent variant).**

---

## Agent Client Protocol Specification

### Message Types

The protocol uses **JSON-RPC 2.0** with two communication styles:

1. **Methods** — request-response pairs (expect a result or error)
2. **Notifications** — one-way messages (no response expected)

### Agent Methods (Incoming from Editor/Client)

**Baseline (required):**
- `initialize` — Negotiate protocol version and exchange capabilities
- `session/new` — Create a new conversation session
- `session/prompt` — Send user instruction/prompt to agent

**Optional:**
- `session/load` — Load and resume an existing session (requires `loadSession` capability)
- `session/set_mode` — Switch agent operating modes
- `session/cancel` — Cancel ongoing operations (notification)

### Client Methods (Outgoing from Agent)

**Baseline (required):**
- `session/request_permission` — Agent asks editor for user approval before tool use

**File System (requires agent to advertise `fs.*` capabilities):**
- `fs/read_text_file` — Agent requests file content
- `fs/write_text_file` — Agent requests to write/create file

**Terminal (requires `terminal` capability):**
- `terminal/create` — Create a new terminal session
- `terminal/output` — Get accumulated terminal output and exit status
- `terminal/release` — Release a terminal
- `terminal/wait_for_exit` — Wait for command to complete
- `terminal/kill` — Kill running command

**Status Updates (notification):**
- `session/update` — Agent sends progress updates (message chunks, tool calls, plans, available commands, mode changes)

### Capabilities

Agents advertise capabilities during `initialize`:

```json
{
  "capabilities": {
    "loadSession": true,
    "fs": { "readTextFile": true, "writeTextFile": true },
    "terminal": true,
    "promptCapabilities": { "image": true }
  }
}
```

---

## Overcode's Current Stats/Metrics

Overcode tracks ~35 distinct metrics across three layers:

### Session-Level Stats (SessionStats)

**Interaction & Activity:**
- `interaction_count` — total user + robot interactions
- `steers_count` — automated interventions by overcode
- `current_task` — current activity description

**Token/Cost:**
- `total_tokens` — cumulative LLM token usage
- `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens` — token breakdown
- `estimated_cost_usd` — running cost

**Time Tracking:**
- `green_time_seconds` — time agent was actively working
- `non_green_time_seconds` — time agent was stalled/waiting
- `sleep_time_seconds` — time in deliberate sleep mode
- `state_since` — ISO timestamp of current state change

**Work Metrics:**
- `median_work_time` — median operation duration
- `operation_times` — per-operation durations
- `last_activity` — timestamp of last action

### Remote/Sister-Specific Metrics

When fetching from remote agents:
- `pane_content` — terminal output snapshot
- `remote_git_diff` — (files, insertions, deletions) tuple
- `remote_median_work_time` — work duration from remote
- `remote_activity_summary` — AI-generated activity summary
- `remote_daemon_state` — raw daemon state dict (generic forwarding)

### Supervision & Control

- `standing_instructions` + `standing_instructions_preset` — persistent agent instructions
- `heartbeat_enabled`, `heartbeat_frequency_seconds`, `heartbeat_paused`, `last_heartbeat_time` — periodic task config
- `permission_mode` — (normal/permissive/bypass) execution mode
- `time_context_enabled` — whether agent receives time hints
- `agent_value` — priority ranking (default 1000)
- `cost_budget_usd` — spending limit (0 = unlimited)

### Hierarchy & Composition

- `child_count` — live spawned subagents
- `live_subagent_count` — similar to child_count
- `background_bash_count` — active background processes
- `parent_session_id` — reference to parent agent (agent hierarchy)

---

## Feature Mapping: ACP vs Overcode

### What Works (✅)

| Feature | ACP Support | Notes |
|---------|-------------|-------|
| **Send instructions** | ✅ Native | `session/prompt` maps directly to instruction sending |
| **Terminal output capture** | ✅ Native | `terminal/output` provides pane content |
| **File change tracking** | ✅ Partial | `fs/write_text_file` notifications show files being edited |
| **Session resumption** | ✅ Native | `session/load` preserves agent state across restarts |
| **User approvals** | ✅ Native | `session/request_permission` handles permission flows |
| **File reading** | ✅ Native | `fs/read_text_file` agent method available |

### What Partially Works (⚠️)

| Feature | ACP Support | Workaround |
|---------|-------------|-----------|
| **State transitions** | ⚠️ Partial | Infer from `session/update` notifications, but no explicit state enum |
| **Permission modes** | ⚠️ Partial | Could extend with custom `session/set_mode` implementation |
| **Git diff tracking** | ⚠️ Partial | Infer from `fs/write_text_file` calls, but no metadata |
| **Custom capabilities** | ⚠️ Yes | Can add non-standard fields, but breaks ACP compliance |

### What Doesn't Work (❌)

| Feature | Why Not | Severity |
|---------|---------|----------|
| **Token/cost metrics** | No reporting mechanism in protocol | **CRITICAL** |
| **Time tracking** | No duration/timing fields | **CRITICAL** |
| **State time tracking** | No state_since or duration tracking | **HIGH** |
| **Agent lifecycle control** | Agent is passive; no kill/restart/pause methods | **CRITICAL** |
| **Heartbeat/scheduled tasks** | No periodic message mechanism | **HIGH** |
| **Standing orders** | No persistent instruction storage in protocol | **MEDIUM** |
| **Cost budgets** | No budget enforcement mechanism | **HIGH** |
| **Multi-agent discovery** | No registry/service discovery | **MEDIUM** |
| **Sister instance coordination** | ACP is 1:1 editor↔agent; no multi-instance support | **HIGH** |
| **Bulk operations** | No transport_all, cleanup_agents, etc. | **MEDIUM** |
| **Child agent tracking** | No agent spawning mechanism in protocol | **MEDIUM** |

---

## Architectural Mismatch

### Agent Client Protocol Design Principles

ACP is fundamentally **editor-centric**:
1. Editor initiates all communication
2. Agent responds passively to prompts
3. Agent requests permissions from editor
4. All state stays internal to agent
5. Designed for interactive (session-based) use

### Overcode's Design Principles

Overcode is fundamentally **cluster-centric**:
1. Monitors independently poll agent status
2. Agents should be controllable by remote supervisors
3. Rich metrics are exported continuously
4. State is externalized (SessionManager, JSON files)
5. Designed for long-running, autonomous operation

**These are orthogonal concerns.** ACP assumes an editor managing one agent; overcode manages a fleet.

---

## Extension Feasibility

### Could We Extend Agent Client Protocol?

**Technically yes, but with costs:**

```json
// Example extended session/update (non-standard)
{
  "jsonrpc": "2.0",
  "method": "session/update",
  "params": {
    // Standard ACP fields
    "messages": [...],
    "toolCalls": [...],

    // Extended: Overcode metrics (NON-STANDARD)
    "metrics": {
      "tokens": { "total": 15234, "input": 10000, "output": 5234 },
      "cost": { "usd": 0.45 },
      "time": {
        "green_seconds": 120.5,
        "non_green_seconds": 30.2,
        "state": "running",
        "state_since_iso": "2026-03-19T10:30:00Z"
      }
    },

    // Extended: Agent control capabilities (NON-STANDARD)
    "agentControl": {
      "supportKill": true,
      "supportRestart": true,
      "supportHeartbeat": true,
      "supportStandingOrders": true
    }
  ]
}
```

**Effort:** 2-3 weeks to design, implement in Claude Agent SDK, integrate into SisterPoller, handle backward compatibility.

**Cost:** You now have "Agent Client Protocol +" which:
- Other frameworks won't recognize
- Breaks ACP compliance
- Requires custom client implementation
- Won't be future-proof if ACP evolves

---

## Use Cases & Scenarios

### Scenario A: Monitor Claude Code in Zed via Overcode (Read-Only)

**Setup:**
```
┌────────────────────────┐
│  Overcode Monitor      │
│  (running on desktop)  │
└────────────┬───────────┘
             │ SSH/mosh connection
             │ to remote machine
             ▼
┌────────────────────────┐
│  Zed IDE               │
│  + Claude Agent SDK    │
│  (Agent Client Protocol)
└────────────────────────┘
```

**What You Can Do:**
- ✅ Capture terminal output
- ✅ Infer agent status from session updates
- ✅ Track file edits in real-time
- ✅ Send new instructions/prompts

**What You Cannot Do:**
- ❌ Kill/restart agent (no protocol support)
- ❌ Track token/cost metrics (no fields)
- ❌ Set heartbeat (no mechanism)
- ❌ Enforce cost budget (no mechanism)

**Feasibility:** **High** (~30% of overcode features) — useful for lightweight read-only monitoring, not sufficient for full orchestration.

---

### Scenario B: Extend ACP for Full Overcode Parity

**Changes needed:**
1. Add metrics to `session/update` (tokens, cost, time)
2. Add agent control methods (`agent/kill`, `agent/restart`, `agent/pause`)
3. Add heartbeat notification mechanism
4. Add persistent config mechanism (standing orders, budgets)
5. Define multi-agent discovery

**Effort:** 4-6 weeks of design + implementation
**Outcome:** Becomes "Overcode Protocol" (not standard ACP)
**Risk:** High maintenance burden; incompatible with ACP ecosystem

**Verdict:** Not worth it. Use custom sister protocol instead.

---

### Scenario C: Hybrid Approach (Recommended)

**Architecture:**
```
Overcode Monitor
├─ Sister Protocol (Control)
│  ├─ Launch/kill agents
│  ├─ Set heartbeat, standing orders
│  └─ Enforce budgets
│
└─ Agent Client Protocol (Observability)
   ├─ Terminal output capture
   ├─ File change tracking
   └─ Session state inference
```

**Implementation:**
- Keep custom sister protocol for control operations
- Expose Agent Client Protocol for IDE integration
- Agents can be monitored from both overcode and editors
- Non-overcode agents can integrate via ACP for read-only observability

**Effort:** 1-2 weeks (ACP read-only client library)
**Benefit:** Ecosystem compatibility + no feature loss

**Recommended approach.**

---

## Comparison Matrix: Sister Protocol vs ACP vs Extended ACP

| Capability | Sister Protocol | ACP Native | ACP Extended | Hybrid |
|------------|---|---|---|---|
| **Token metrics** | ✅ Yes | ❌ No | ⚠️ Custom | ✅ Sister |
| **Cost tracking** | ✅ Yes | ❌ No | ⚠️ Custom | ✅ Sister |
| **Time tracking** | ✅ Yes | ❌ No | ⚠️ Custom | ✅ Sister |
| **Agent kill/restart** | ✅ Yes | ❌ No | ⚠️ Custom | ✅ Sister |
| **Heartbeat** | ✅ Yes | ❌ No | ⚠️ Custom | ✅ Sister |
| **Standing orders** | ✅ Yes | ❌ No | ⚠️ Custom | ✅ Sister |
| **Cost budgets** | ✅ Yes | ❌ No | ⚠️ Custom | ✅ Sister |
| **Terminal capture** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Both |
| **File tracking** | ✅ Yes | ✅ Yes | ✅ Yes | ✅ Both |
| **IDE integration** | ❌ No | ✅ Yes | ✅ Yes | ✅ ACP |
| **Multi-agent discovery** | ⚠️ Manual | ❌ No | ⚠️ Custom | ❌ No |
| **Ecosystem compatibility** | ❌ Custom | ✅ Standard | ⚠️ Fork | ✅ Standard |
| **Implementation complexity** | Low | Medium | High | Medium |
| **Maintenance burden** | Low | Low | High | Low |

---

## Claude Agent SDK Integration

### What the SDK Provides

The Claude Agent SDK (Python/TypeScript) supports:

**Built-in tools:**
- Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch
- AskUserQuestion, Agent (subagents)

**Hooks for observability:**
- `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`
- Can log tokens, durations, file changes

**Sessions:**
- Session IDs for resumption
- Full context preservation across runs

**MCP Integration:**
- Connect external services (databases, APIs, browsers)

### What SDK Does NOT Provide (Re: Observability)

- ❌ Token/cost reporting via structured fields
- ❌ Time-in-state tracking
- ❌ Heartbeat/periodic task mechanism
- ❌ Budget enforcement
- ❌ Remote agent discovery

**Hooks can capture some data, but you'd need to build the observability layer yourself.**

---

## Recommendations

### Short Term (Now - 2 weeks)

**Keep status quo:**
- Continue using custom sister protocol for control & metrics
- Document sister protocol design
- No changes needed

### Medium Term (1-3 months)

**Evaluate ACP for limited use:**
- Build experimental read-only ACP client for SisterPoller
- Capture terminal output + file changes via ACP
- Keep metrics/control via sister protocol
- Test with agents running in Zed

### Long Term (3-12 months)

**Three options:**

**Option 1: Stay with Sister Protocol**
- Sister protocol is purpose-built for overcode
- No compatibility cost
- Clear, simple design
- Downsides: Overcode-specific, no ecosystem compatibility

**Option 2: Adopt ACP Fully (with extensions)**
- Design extension spec for missing metrics/control
- Implement in Claude Agent SDK
- Becomes "Overcode-compatible ACP"
- Downsides: Non-standard, high maintenance, ecosystem fragmentation

**Option 3: Hybrid (Recommended)**
- Sister protocol handles control (agent lifecycle, budgets, heartbeat)
- ACP handles observability (terminal, files, session state)
- Non-overcode agents can integrate via ACP read-only layer
- Downsides: Dual protocol maintenance

**Verdict: Recommend Option 3 (Hybrid)** as it:
- Preserves all current functionality
- Enables ecosystem integration
- Adds future flexibility
- Minimal implementation cost

---

## Open Questions

1. **Do we want to support non-Claude-Code agents in future?** If yes, hybrid approach is mandatory.

2. **How important is IDE integration parity?** If agents should be manageable from both overcode and Zed, hybrid approach simplifies this.

3. **Will Claude Agent SDK eventually ship native metrics/control?** If yes, wait before investing in extensions.

4. **What's the priority on ecosystem compatibility vs. feature completeness?** If ecosystem matters, stay with sister protocol + ACP layer. If features matter most, extend ACP or keep sister protocol.

---

## References

- [Agent Client Protocol Specification](https://agentclientprotocol.com/protocol/overview)
- [Claude Agent SDK Documentation](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Zed Agent Client Protocol](https://zed.dev/acp)
- [Agent Client Protocol GitHub](https://github.com/agentclientprotocol/agent-client-protocol)
- [Overcode Architecture Doc](../architecture.md)

---

## Document History

| Date | Author | Changes |
|------|--------|---------|
| 2026-03-19 | Investigation | Initial analysis of ACP integration feasibility |
