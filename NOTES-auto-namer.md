# auto-namer: auto-naming P2, with P4's parent notices and Claude name alignment (#542)

Sections 3-8 of `docs/design/auto-naming.md` (each now has a "Built"
paragraph with the as-built details) and `overcode name suggest`.

## What changed

- `namer.py` (new): `due` (§3 triggers), `gather_inputs` / `render` /
  `parse_reply` / `suggest` (§4-5), `record_spend` (ledger row under the
  code `overcode`), `DailySpend` (cost cap), the Claude-title helpers, and
  `Namer`, the daemon's part: `on_tick` sees turn ends (green → waiting) and
  new Claude titles; the model call, the rename, typing `/rename` and
  adopting a title run on one worker thread.
- `monitor_daemon.py`: builds a `Namer` (spend file beside the daemon
  state), calls `on_tick` after publishing (a failure only warns), shuts
  it down with the daemon.
- Capture: `agents/<id>/brief.txt` (launch/fork), `prompts.jsonl` (hook
  handler, UserPromptSubmit), `namer.json`, `child_notice.txt`; all in
  `paths.AGENT_DIR_FILES` and kept in day bundles. The hook event log
  keeps `tool_input.file_path`.
- `rename()` stores `source` as `name_source` in the same locked write
  (`rename_session(name_source=)`); the TUI dialog passes its source. A
  renamed child's parent gets a one-shot notice on its next prompt.
- Claude: `display_name_args` backend hook (`--name`); transcript readers
  pick up `custom-title` (`AgentSessionStats.custom_title`,
  `StatsSync.transcript_titles`).
- TUI: a toast when a row's name changes to an `auto` one
  (`name_source.auto_rename_toast`).
- CLI: `overcode name suggest <agent> [--advice-file] [--json]`;
  `overcode list --self` (by `OVERCODE_SESSION_ID`); the overcode skill
  uses it.
- Config: `naming.cli_title` (default true); `naming.provider` now used
  (`SummarizerClient(overrides=)`); prompt placeholder `{parent}`.
- Docs: configuration (Naming, Agent Files), cli-reference (`list --self`,
  `name suggest`), TUI guide (Agent names), design doc.

## Decisions

- A turn end is the published status going from green to
  waiting_user / waiting_heartbeat / waiting_oversight / done (works for
  hookless agents; waiting_approval is never a Stop). Turns = max(turn
  ends seen, transcript interaction count).
- "A human keypress in the TUI in 30 s" = the TUI keypress heartbeat file
  (per tmux session, written ≤ every 5 s). So it is session-wide, not per
  agent. The same rule guards typing `/rename`.
- Refinement needs 10x every threshold. A failed call (no reply,
  unreadable JSON) is retried ≥ 10 min later; a missing model is logged
  once per daemon.
- Spend: each call is a closed ledger segment of its own (`sid
  namer:<hex>`, `kind final`, `namer: true`, `parent` = the agent), billed
  to `overcode` (resolved through the codes registry). The `overcode`
  code's agent count is therefore its call count. The daily cap is per
  tmux session (`namer_spend.json`). `name suggest` also writes its row.
- `follow_agent_name` only on a root agent's first auto rename (owner
  decision 3: "first auto name"); a child shares its root's code.
- Claude title alignment runs for every rename (CLI, TUI, auto), with
  `naming.enabled` off too; `naming.cli_title: false` disables it and
  adoption. Adoption normalises the title, refuses a taken/invalid one
  (logged), and ignores the agent's name, its old names and the title
  overcode last typed. Real record shape checked on Claude 2.1.304 in a
  scratch project: `{"type":"custom-title","customTitle","sessionId"}` plus
  an `agent-name` twin; `-n` alone wrote no record before the first
  `/rename`.
- Parent notices are written by every rename of a child, not only auto.
- Not done: a children-preset switch for naming. A parent's children get
  `parent` names, which are never auto-renamed, so a preset `naming` key
  would change nothing for them; left out rather than add a Session field.
- Not done: opencode prompt capture (`chat.message`) — TODO for the
  opencode-notices branch; hermes `/title` and opencode titles (§7).
- No repo-column flag is passed to the model.

## Tests

- `tests/unit/test_namer.py` (70): triggers; replays (first name at the
  2nd Stop, one refinement, keep, tokens trigger, human/parent lock, lock
  during the call, waiting_approval, TUI keypress, off by default, retry,
  no model, siblings); choosing (taken, all taken → `wiki3`, aliases,
  normalising, rules, max_length as target, lost race); spend (ledger row
  and totals, daily cap, no tokens); billing code follow (root first name
  only; human and child codes stay); inputs and prompt; reply parsing;
  notices (source stored, agent + parent notice, parent reads once, toast);
  capture (brief, fork brief, 3 prompts, bundling); Claude title (`--name`,
  typed `/rename` only on an empty input line, cli_title off, other
  backends, adoption, own titles ignored, taken title, transcript record);
  wiring (daemon, `name suggest`, `list --self`).
- `test_paths`, `test_hook_handler` updated (new files; `file_path` kept).
- Full unit suite: 9444 passed, 17 skipped (5 m 58 s). ruff clean.
