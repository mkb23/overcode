# Better State Detection Architecture for Claude Agents

**Issue:** [#5](https://github.com/mkb23/overcode/issues/5) - Better state detection architecture -- with stop hooks?

**Date:** 2026-01-18

---

## Executive Summary

This proposal evaluates options for improving state detection in Overcode. The current polling-based architecture has limitations in responsiveness and reliability. Claude Code's hook system offers a compelling event-driven alternative that could provide instant, accurate state detection.

**Recommendation:** Implement a **hybrid architecture** combining Claude Code's `Stop` hook for instant idle detection with existing content analysis for running state details.

---

## Current Architecture

### How It Works

The current system uses **polling-based terminal content analysis**:

```
┌─────────────────────────────────────────────────────────────────┐
│                    MONITOR DAEMON (10s loop)                    │
├─────────────────────────────────────────────────────────────────┤
│  For each session:                                              │
│    1. Capture last 150 lines from tmux pane                     │
│    2. Filter status bar elements                                │
│    3. Pattern match for:                                        │
│       - Shell prompts → STATUS_TERMINATED                       │
│       - Permission dialogs → STATUS_WAITING_USER                │
│       - Active indicators → STATUS_RUNNING                      │
│       - User input prompts → STATUS_WAITING_USER                │
│    4. Track content changes (hash comparison)                   │
│    5. Publish to JSON state file                                │
└─────────────────────────────────────────────────────────────────┘
```

**Key Files:**
- `src/overcode/status_detector.py:82` - Main `detect_status()` method
- `src/overcode/status_patterns.py` - Pattern definitions
- `src/overcode/monitor_daemon.py:553` - Main daemon loop

### Current Status States

| Status | Description | Detection Method |
|--------|-------------|------------------|
| `running` | Claude actively working | Content changed OR active indicators |
| `no_instructions` | No standing orders | No instructions set |
| `waiting_supervisor` | Needs supervisor | Reserved for supervisor logic |
| `waiting_user` | User input required | Permission/prompt patterns |
| `terminated` | Claude exited | Shell prompt visible |

### Limitations

1. **10-second latency** - State changes detected up to 10s late
2. **Pattern fragility** - UI changes can break detection
3. **False positives** - Dynamic status bar elements trigger false "content changed"
4. **Complex heuristics** - 340+ lines of pattern matching logic
5. **Resource overhead** - Constant tmux capture regardless of activity
6. **No Claude insight** - Can't distinguish task completion from waiting

---

## Claude Code Hook System

Claude Code provides a **native hook system** that fires on specific events:

### Available Hooks

| Hook | Fires When | Usefulness for State Detection |
|------|------------|-------------------------------|
| **Stop** | Claude finishes responding | **High** - Instant idle detection |
| **SubagentStop** | Subagent finishes | Medium - For parallel agents |
| **Notification** | Various events (including `idle_prompt`) | **High** - 60s idle detection |
| **PostToolUse** | After tool completes | Medium - Tool activity tracking |
| **PreToolUse** | Before tool runs | Low - Just starting work |
| **PermissionRequest** | Permission dialog shown | **High** - User input needed |
| **UserPromptSubmit** | User sends prompt | Medium - User activity |
| **SessionStart/End** | Session lifecycle | High - Track session state |

### Hook Input Data

All hooks receive JSON via stdin:
```json
{
  "session_id": "unique-session-id",
  "transcript_path": "/path/to/conversation.jsonl",
  "cwd": "/current/working/directory",
  "hook_event_name": "Stop"
}
```

The **Stop hook** additionally indicates:
```json
{
  "stop_hook_active": true  // Prevents infinite loops
}
```

### Hook Output Control

Hooks can return JSON to control behavior:
```json
{
  "decision": "block",
  "reason": "Work not complete"
}
```

---

## Proposed Options

### Option 1: Pure Hook-Based Architecture

Replace polling entirely with Claude Code hooks.

**Architecture:**
```
┌─────────────────────────────────────────────────────────────────┐
│                    HOOK-BASED STATE DETECTION                   │
├─────────────────────────────────────────────────────────────────┤
│  Claude Code Hooks (instant)                                    │
│    ├── Stop → Write "idle" to state file                        │
│    ├── Notification (idle_prompt) → Write "waiting_user"        │
│    ├── PermissionRequest → Write "waiting_user"                 │
│    ├── PostToolUse → Write "running" + tool info                │
│    └── SessionEnd → Write "terminated"                          │
│                                                                 │
│  Monitor Daemon (reduced role)                                  │
│    ├── Read hook-written state files                            │
│    ├── Aggregate stats                                          │
│    ├── Sync Claude Code history                                 │
│    └── Publish MonitorDaemonState                               │
└─────────────────────────────────────────────────────────────────┘
```

**Hook Configuration (`~/.claude/settings.json`):**
```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "overcode-hook stop"
      }]
    }],
    "PostToolUse": [{
      "hooks": [{
        "type": "command",
        "command": "overcode-hook tool-use"
      }]
    }],
    "Notification": [{
      "matcher": "idle_prompt",
      "hooks": [{
        "type": "command",
        "command": "overcode-hook idle"
      }]
    }],
    "PermissionRequest": [{
      "hooks": [{
        "type": "command",
        "command": "overcode-hook permission"
      }]
    }]
  }
}
```

**Pros:**
- Instant state detection (0ms latency)
- More accurate (Claude knows its own state)
- Simpler logic (no pattern matching)
- Lower CPU usage (event-driven)
- Future-proof (tracks Claude Code updates)

**Cons:**
- Requires user hook configuration
- No visibility during long operations (between tool calls)
- Dependency on Claude Code hook stability
- Can't detect "stalled" states (Claude thinking too long)
- Single point of failure if hooks misconfigured

---

### Option 2: Hybrid Architecture (Recommended)

Combine hooks for instant idle detection with polling for running state details.

**Architecture:**
```
┌─────────────────────────────────────────────────────────────────┐
│                    HYBRID STATE DETECTION                       │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 1: Hooks (instant, authoritative for transitions)       │
│    ├── Stop → Immediately write "idle" state + timestamp        │
│    ├── PostToolUse → Update last activity                       │
│    └── PermissionRequest → Immediately write "waiting_user"     │
│                                                                 │
│  LAYER 2: Polling (enrichment, fallback, running details)      │
│    ├── When hook says "idle" → Trust it, skip detection        │
│    ├── When no recent hook → Fall back to content analysis     │
│    ├── Always extract current activity from pane               │
│    └── Content change detection as fallback indicator          │
│                                                                 │
│  STATE MERGING:                                                 │
│    hook_state + pane_analysis → final_state                    │
│    hook_timestamp determines freshness                          │
└─────────────────────────────────────────────────────────────────┘
```

**Implementation Flow:**

```python
def detect_status_hybrid(session):
    # 1. Check hook state file (instant, authoritative)
    hook_state = read_hook_state(session)

    if hook_state and is_fresh(hook_state, threshold_seconds=15):
        if hook_state.status == "idle":
            return STATUS_WAITING_USER, "Claude finished", pane_content
        if hook_state.status == "permission":
            return STATUS_WAITING_USER, hook_state.reason, pane_content

    # 2. Fall back to content analysis for running states
    #    (hooks don't fire during streaming/thinking)
    return existing_detect_status(session)
```

**Pros:**
- Best of both worlds
- Instant idle detection via hooks
- Detailed activity info via content analysis
- Graceful degradation if hooks fail
- Maintains current capabilities

**Cons:**
- More complex (two systems to maintain)
- Still requires some pattern matching
- Hook configuration still needed

---

### Option 3: Enhanced Polling with Transcript Analysis

Keep polling but add transcript file analysis for better accuracy.

**Architecture:**
```
┌─────────────────────────────────────────────────────────────────┐
│                TRANSCRIPT-ENHANCED POLLING                      │
├─────────────────────────────────────────────────────────────────┤
│  Monitor Daemon (10s loop)                                      │
│    1. Capture tmux pane (existing)                              │
│    2. Read Claude Code transcript file                          │
│       ~/.claude/projects/{path}/{session}.jsonl                 │
│    3. Parse last message:                                       │
│       - role: "assistant" with no tool_use → idle               │
│       - role: "user" with no response → waiting                 │
│       - tool_use in progress → running                          │
│    4. Cross-reference with pane content                         │
│    5. Publish combined state                                    │
└─────────────────────────────────────────────────────────────────┘
```

**Pros:**
- No hook configuration needed
- Uses existing Claude Code data
- More accurate than pure terminal analysis

**Cons:**
- Still polling (10s latency)
- Transcript path discovery is complex
- JSONL parsing overhead
- File might not be flushed immediately

---

### Option 4: Prompt-Based Stop Hooks

Use Claude Code's LLM-powered stop hooks for intelligent state detection.

**Architecture:**
```json
{
  "hooks": {
    "Stop": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "Analyze the conversation and output JSON with: {\"status\": \"complete|waiting_user|blocked\", \"reason\": \"...\", \"next_action\": \"...\"}",
        "timeout": 30
      }, {
        "type": "command",
        "command": "overcode-hook process-stop-decision"
      }]
    }]
  }
}
```

**Pros:**
- Intelligent state analysis by Claude
- Can determine task completion
- Understands context

**Cons:**
- Adds latency (LLM call on every stop)
- Increases costs (~$0.001 per stop)
- Overkill for simple state detection

---

## Recommendation: Option 2 (Hybrid Architecture)

### Why Hybrid?

1. **Instant idle detection** - The primary pain point is knowing when Claude stops. The Stop hook solves this immediately.

2. **Preserved running state visibility** - Current content analysis provides valuable activity details during execution.

3. **Graceful degradation** - If hooks aren't configured, falls back to current behavior.

4. **Incremental migration** - Can be implemented in phases without breaking existing functionality.

### Implementation Plan

#### Phase 1: Hook Infrastructure

1. Create `overcode-hook` CLI command that:
   - Reads JSON from stdin
   - Writes to session-specific state file
   - Exits quickly (hooks have timeout)

2. Define hook state file format:
   ```json
   {
     "event": "stop",
     "timestamp": "2026-01-18T12:00:00",
     "session_id": "abc123",
     "status": "idle",
     "last_tool": "Bash",
     "transcript_path": "/path/to/transcript.jsonl"
   }
   ```

3. Add hook state file paths to settings:
   ```
   ~/.overcode/sessions/{session}/hook_state.json
   ```

#### Phase 2: Integrate with Status Detector

1. Modify `StatusDetector.detect_status()`:
   - First check hook state file
   - If fresh (< 15s) and indicates idle → return immediately
   - Otherwise fall through to existing logic

2. Add `HookStateReader` class:
   - Watch for hook state file changes
   - Parse and validate hook events
   - Track hook reliability metrics

#### Phase 3: Documentation & Setup

1. Create setup script for hook configuration:
   ```bash
   overcode hooks install  # Adds hooks to ~/.claude/settings.json
   overcode hooks verify   # Tests hook connectivity
   ```

2. Document hook requirements in README

3. Add TUI indicator showing hook status

#### Phase 4: Optional Enhancements

1. Add `PostToolUse` hook for real-time activity updates
2. Add `PermissionRequest` hook for faster permission detection
3. Add transcript analysis for task completion detection

### Hook State File Schema

```python
@dataclass
class HookState:
    event: str           # "stop", "tool_use", "permission", "notification"
    timestamp: datetime  # When hook fired
    session_id: str      # Claude Code session ID
    status: str          # "idle", "waiting_user", "running"

    # Optional fields
    tool_name: str = ""          # For PostToolUse
    permission_type: str = ""    # For PermissionRequest
    transcript_path: str = ""    # For transcript analysis
```

### Freshness Logic

```python
HOOK_FRESHNESS_THRESHOLD = 15  # seconds

def is_hook_state_fresh(hook_state: HookState) -> bool:
    """Determine if hook state is recent enough to trust."""
    age = (datetime.now() - hook_state.timestamp).total_seconds()
    return age < HOOK_FRESHNESS_THRESHOLD
```

---

## Comparison Matrix

| Aspect | Current | Option 1 (Pure Hooks) | Option 2 (Hybrid) | Option 3 (Transcript) | Option 4 (LLM Stop) |
|--------|---------|----------------------|-------------------|----------------------|---------------------|
| Idle detection latency | 0-10s | 0ms | 0ms | 0-10s | 0ms |
| Running state details | Good | Poor | Good | Good | Good |
| Permission detection | 0-10s | 0ms | 0ms | 0-10s | 0ms |
| User configuration | None | Required | Recommended | None | Required |
| Graceful degradation | N/A | Poor | Good | Good | Poor |
| Complexity | High | Low | Medium | Medium | High |
| Resource usage | Medium | Low | Medium | Medium | High |
| Cost | None | None | None | None | ~$0.001/stop |
| Future-proof | Low | High | High | Medium | High |

---

## Files to Modify

### New Files
- `src/overcode/hook_handler.py` - Hook CLI command
- `src/overcode/hook_state.py` - Hook state data structures

### Modified Files
- `src/overcode/status_detector.py` - Add hook state integration
- `src/overcode/settings.py` - Add hook state paths
- `src/overcode/cli.py` - Add `hooks` subcommand

### Configuration
- Document `~/.claude/settings.json` hook setup
- Add `overcode hooks install` command

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Hook misconfiguration | Graceful fallback to polling |
| Hook timeout/failure | Age-based freshness check |
| Claude Code API changes | Version detection + pattern fallback |
| Multiple daemons reading | Atomic file operations |
| Hook spam (frequent tool use) | Rate limiting in handler |

---

## Conclusion

The hybrid approach (Option 2) provides the best balance of:
- Instant state transitions via hooks
- Detailed running state via content analysis
- Graceful degradation for reliability
- Incremental implementation path

This architecture addresses the core issue (#5) while maintaining backwards compatibility and providing a clear migration path.
