# rename-every: auto names follow the work (#547)

A power user's request: the namer renames every N minutes from what the
agent is doing now, and says when it names.

## What changed

- `namer.refresh_due` replaces `due` / `REFINE_AFTER` / `refine_done`: after
  the first name, a check each `naming.rename_every.minutes` (default 30)
  of green time since the last answered check (`NamerState.checked_green`,
  `checked_at`), on any tick, mid-turn or idle (`Namer._name_due`, the same
  once-a-minute look as the first name). A count that starts again (a
  restart) restarts the check's count. `_on_stop` no longer names.
- `max_renames` defaults to null (no cap); a number still caps.
- Inputs: `recent_prompts.jsonl` (the last 3 prompts, written by the hook
  handler and both opencode plugins beside `prompts.jsonl`), files edited
  since the last check (hook events' `timestamp`), and `why` the current
  name was chosen (`NamerState.why`). New placeholders `{recent_prompts}`,
  `{recent_files}`, `{why}`; a saved template without them gets
  `RECENT_SECTION` appended. The default prompt tells the model to keep an
  `auto` name unless the focus has clearly moved on.
- The naming lab and `overcode name suggest` pass the agent's namer state,
  so they show the same recent window.
- `describe_auto_naming` (palette toast, new-agent tip, lab header,
  `overcode name policy`): "...; then re-checked every 30 min of work,
  renamed when the work has moved on".

## Owner decisions (2026-10-22)

- Cadence: N minutes of work, checked any time (not only at a Stop).
- Inputs: the brief plus the recent window.
- Churn: keep-biased prompt, no lifetime cap.
- Visibility: the policy line and the config docs (no per-agent
  next-check field, no TUI settings dialog).

## Gaps

- Not run against a live model; the prompt was checked by rendering it.
- An agent named before this has `checked_green` 0, so its first check
  comes as soon as the daemon sees it (one call per such agent).
