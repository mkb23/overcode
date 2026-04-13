# Claude Code Session Files

How overcode reads Claude Code's local session data for token counting, cost estimation, and interaction tracking.

## Directory Layout

Claude Code stores conversation data under `~/.claude/`:

```
~/.claude/
  history.jsonl                          # Global interaction log (prompts only)
  projects/
    {encoded-project-path}/
      {sessionId}.jsonl                  # Parent session conversation
      {sessionId}/
        subagents/
          agent-a{hex}.jsonl             # Regular subagent transcript
          agent-a{hex}.meta.json         # Subagent metadata (agentType, etc.)
          agent-acompact-{hex}.jsonl     # Compaction subagent (see below)
          agent-aside_question-{hex}.jsonl  # Side-question subagent (see below)
        tool-results/
          {toolUseId}.txt                # Large tool outputs stored externally
```

The `{encoded-project-path}` is the absolute path with `/` replaced by `-` (e.g. `/Users/mike/Code/myapp` becomes `-Users-mike-Code-myapp`).

## JSONL Message Types

Each line in a session `.jsonl` file is a JSON object with a `type` field:

| Type | Description | Has `usage`? |
|------|-------------|:---:|
| `assistant` | Claude's response | Yes |
| `user` | User prompt or tool result | No |
| `system` | System messages (compaction markers, etc.) | No |
| `progress` | Streaming progress updates | No |
| `file-history-snapshot` | File state snapshots | No |

### Assistant Message `usage` Fields

The `message.usage` object on assistant messages contains the token counts that overcode sums for cost estimation:

```json
{
  "type": "assistant",
  "timestamp": "2026-03-25T12:13:10.916Z",
  "message": {
    "model": "claude-opus-4-6",
    "usage": {
      "input_tokens": 1003,
      "output_tokens": 278,
      "cache_creation_input_tokens": 2884,
      "cache_read_input_tokens": 25944
    }
  }
}
```

- **`input_tokens`**: Non-cached input tokens for this API call.
- **`output_tokens`**: Generated response tokens.
- **`cache_creation_input_tokens`**: Tokens written to the prompt cache on this call.
- **`cache_read_input_tokens`**: Tokens read from the prompt cache. This is typically the largest number because the full conversation context is cached and re-read on each API call.

### Context Size

The current context window usage is approximated as `input_tokens + cache_read_input_tokens` from the most recent assistant message. This reflects how much of the model's context window is occupied.

## Subagent File Types

### Regular Subagents (`agent-a{hex}.jsonl`)

Spawned by Claude Code's `Agent` tool. Each file is an independent conversation with its own API calls. Token usage is additive — the parent session's tokens reflect the parent's context (which includes the subagent's final response as a tool result), and the subagent file reflects the subagent's own API calls.

Metadata lives in a sibling `.meta.json` file with `agentType` (e.g. `"Explore"`, `"Plan"`, `"general-purpose"`).

### Compact Subagents (`agent-acompact-{hex}.jsonl`)

Created by Claude Code's context compaction feature (`/compact` command or auto-compact when context is near-full). These come in **three variants**:

| Variant | `isMeta` | Lines | Contains |
|---------|:--------:|------:|----------|
| **Small summary** | `false`/absent | 2-4 | The API call(s) to generate the compaction summary. Real, unique token usage. |
| **New-style continuation** | `false`/absent | ~80-100 | Starts with `type: "system", subtype: "compact_boundary"`. The compaction work itself. Real, unique token usage. |
| **Duplicate conversation log** | **`true`** | 1000s | **Copy of messages already in the parent session file.** Must be excluded from token counting to avoid double/triple counting. |

The `isMeta: true` variant is the dangerous one for cost estimation. These files can be enormous (5000+ lines, 20+ MB) and contain near-complete copies of the parent session's messages with identical UUIDs and timestamps. A single parent session may have multiple such files (e.g. two compact logs and one side-question log), leading to 3-4x overcounting.

### Side-Question Subagents (`agent-aside_question-{hex}.jsonl`)

Created by the `/btw` feature (asking a side question during a conversation). The same `isMeta` distinction applies — when `isMeta: true`, the file is a duplicate conversation log that must be excluded from token counting.

## How Overcode Counts Tokens

The main entry point is `get_session_stats()` in `history_reader.py`:

1. Find all Claude session IDs belonging to the overcode session (via `history.jsonl` matching).
2. For each session ID, read the parent `.jsonl` file and sum `usage` fields from all assistant messages.
3. Scan the `subagents/` directory for `agent-*.jsonl` files.
4. **Skip** files identified as duplicate conversation logs (`_is_duplicate_subagent()` — checks for `isMeta: true` on compact/side-question files).
5. Sum tokens from remaining (real) subagent files.
6. Sum tokens from `task-*.jsonl` files in `tasks/` (background tasks).

## Known Accuracy Issues

### Cost estimates are API-rate approximations

Overcode estimates cost by applying per-model API token rates. For users on flat-rate plans (Claude Max, Team plans with included usage), these dollar amounts do not reflect actual billing. The token counts themselves are accurate; only the dollar conversion may not apply.

### `/btw` and `/compact` conversation logs

Prior to the `isMeta` filtering fix, compact and side-question subagent files were incorrectly summed alongside the parent session, causing 2-4x overcounting of tokens and cost. The `_is_duplicate_subagent()` function now detects and skips these.

If new Claude Code features introduce additional subagent file types that duplicate parent messages, they would need to be added to the detection logic.

### Subagent token attribution

When a parent session spawns a subagent, the subagent's final response is included in the parent's next API call as a tool result. This means the parent's `cache_read_input_tokens` will include the subagent's response text. This is not double-counting — it reflects a real API call where the parent re-reads its context (now including the subagent result). However, it does mean the "cost" of a subagent is slightly higher than just the subagent file's tokens alone.

### Post-compaction context reset

After compaction, the parent session continues with a much smaller context (just the summary). The token counts in subsequent assistant messages reflect this reset. Overcode sums all messages including post-compaction ones, which is correct — these are real API calls.

### Session files from older Claude Code versions

The JSONL format has evolved across Claude Code versions. Older files may lack fields like `isMeta`, `isCompactSummary`, or `compact_boundary` markers. The current detection logic is conservative — it only skips files that positively match the duplicate pattern. Unknown file types are included in the count (erring on the side of overcounting rather than undercounting).
