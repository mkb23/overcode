# opencode event-stream corpus (#474)

Verbatim plugin-hook traffic captured from **real opencode v1.18.29**
sessions (macOS/arm64, `openai/gpt-4o-mini`, Sep 21 2026), one file per
scenario. Each line is one hook invocation as opencode made it, recorded by
the spy plugin in `tests/js/opencode_spy_plugin.js`:

```
{"t": <epoch>, "hook": "event",               "event": {"type": ..., "properties": {...}}}
{"t": <epoch>, "hook": "chat.message",        "input": {...}, "output": {...}}
{"t": <epoch>, "hook": "tool.execute.before", "input": {...}, "output": {...}}
{"t": <epoch>, "hook": "tool.execute.after",  "input": {...}, "output": {...}}
{"t": <epoch>, "hook": "chat.params" | "experimental.text.complete" | "shell.env" | "__load__", ...}
```

The first line of every file is a `{"_fixture": ..., "_note": ...}` header.
`message.part.updated` / `message.part.delta` payloads were reduced to
their routing fields at capture time (they are the streaming token
firehose); everything else is untouched apart from replacing the temp
project path with `/proj` and the home directory with `/home/user`.

These are replayed through the **real** bundled plugin by
`tests/unit/test_opencode_plugin_replay.py` (via
`tests/js/opencode_plugin_replay.mjs`) and every publish is checked against
the reference semantics in `tests/opencode_oracle.py`. When opencode changes
its event vocabulary, re-capture with `tests/e2e/test_live_opencode_states.py`
(`OVERCODE_LIVE_BACKENDS=opencode`), which drives the same scenarios against
the installed CLI and writes fresh spy logs.

| Fixture | Scenario | What it pins |
| --- | --- | --- |
| `simple_turn` | fresh launch, one text-only turn | startup noise (`plugin.added` ×90, `catalog.updated`…) is ignored; `session.created` → `chat.message` → `session.status busy` → … → `session.idle` |
| `read_tool` | one `read` tool call | `tool.execute.before/after` → `PreToolUse`/`PostToolUse[Read]` |
| `permission_allow` | `bash` under `permission: ask`, Enter (= Allow once) | `permission.asked` → `PermissionRequest[Bash]`; `permission.replied once` → `PreToolUse[Bash]` |
| `permission_reject` | same, Escape (= Reject) | `permission.replied reject` → `PostToolUse[Bash]`; no `tool.execute.after` |
| `subagent_task` | `task` tool spawns a `general` sub-agent that runs `glob` + `read` | the child's `chat.message`, tool calls and `session.idle` must **not** publish for the parent (the bug: they did, so a spurious `Stop` landed mid-`Task`) |
| `child_permission` | the sub-agent's `bash` asks permission | the child's `permission.asked`/`replied` **must** surface (the dialog is answered in the parent's TUI); its idle still must not |
| `interrupt` | double-Escape mid-generation | `session.error {name: MessageAbortedError}` is the user's interrupt, not an error; the idle after it settles to `Stop` |
| `provider_error` | bad API key | `session.error {name: APIError}` then `session.status idle` + `session.idle` **within 1 ms** (the bug: `Stop` overwrote `StopFailure`, the error never showed) |
| `new_session` | `/new`, then a turn | a second root `session.created`; the turn publishes for the new root |
| `queued_prompts` | second prompt typed while the first runs | two `chat.message`s, one `session.idle` — one `Stop` |
| `resume` | `overcode restart` (`--session <id>`) | fresh process, **no** `session.created`: the first session seen is adopted |

Not captured (not reproducible on demand with this provider): `session.status
{type: retry}` (provider backoff), compaction (`experimental.session.compacting`),
`session.deleted`. The oracle documents the intended handling for each.

Observed but *absent*: `/exit` produces **no** bus event — the process just
ends, leaving the last `Stop` in `hook_state`. `HookStatusDetector` therefore
checks the pane for a bare shell prompt (see `_pane_shows_dead_shell`).
