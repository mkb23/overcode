# opencode-notices (#533)

## What changed
- `overcode hook-handler --prompt-context` prints exactly what UserPromptSubmit
  prints (enhanced-context line, then the one-shot rename notice, consumed) and
  writes no hook state. `hook_handler.emit_prompt_context` is the one source for
  both paths. The console-script entry point fast-paths it.
- opencode v1 plugin: `chat.message` awaits that command and pushes a
  `synthetic` text part (opencode-shaped ascending `prt_` id). The part is stored
  with the message and sent to the model, and the TUI hides it.
- opencode2: the server entrypoint `index.js` registers `session.hook("prompt")`
  and appends the text to `prompt.text`, so the TUI shows it in the user message.
  Prompts in child sessions (`parentID`) are skipped.
- Both plugins record the first 3 prompts in `agents/<id>/prompts.jsonl` as
  `{"t": ISO-UTC "…Z", "prompt": first 1024 chars}` (needs OVERCODE_SESSION_ID).
- The opencode/opencode2 `env_prefix` exports `OVERCODE_HOOK_COMMAND`
  (`_resolve_overcode_bin`, copied into opencode.py like the other backends).
- Docs: backends.md (feature row, opencode "Prompt context" section,
  codex/grok notes), opencode-parity.md (gap 6 fixed, table rows, counts),
  cli-reference rename paragraph.

## Verified live
- opencode 1.18.29 and opencode2 dev-19272: hand probes, then
  `tests/e2e/test_live_backends.py` with OVERCODE_LIVE_BACKENDS=opencode,opencode2:
  2 passed. Step 6 now checks that the agent says its new name (upper case) and
  that the plugin wrote prompts.jsonl.
- codex 0.156.1: plain UserPromptSubmit stdout reaches the model, sent as a
  developer message (`codex exec` live check plus codex-rs source). Nothing to
  fix. The live tier now expects the notice for codex too, but codex was not
  run through it here.
- grok 1.0.34: its hooks guide says an allowing hook's stdout is discarded and
  UserPromptSubmit has no `additionalContext`. Not checked live (grok is not
  signed in). Documented as unsupported. Possible follow-up: carry the rename
  notice in PostToolUse `additionalContext`.

## Decisions
- One Python source instead of reimplementing enhanced context in JS. The cost
  is one fast-path process per prompt, as with Claude's hook. A 5 s timeout
  applies, and on failure the prompt goes through without context.
- Prompts in child sessions (v1 `task`, v2 `subagent`) get no context, so a
  child never takes the notice.
- prompts.jsonl format follows docs/design/auto-naming.md. "1 KB" is
  implemented as 1024 characters, and `t` is a UTC ISO string with a `Z`
  suffix. The auto-namer branch's Python writer should match this (or relax
  its reader).
- Live tier: the fixture puts an `overcode` shim first on PATH so the agents'
  hooks and plugins run this checkout. Before this, they ran whatever overcode
  was installed.
- The test replay harness sets an inert `OVERCODE_HOOK_COMMAND`, so replays
  never spawn a real overcode.

## Tests
- New: tests/unit/test_opencode_prompt_context.py (15, node-driven against the
  real handler), entry-point fast path, env_prefix tests.
- Unit suite: 9392 passed, 17 skipped. ruff clean.
