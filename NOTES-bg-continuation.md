# bg-continuation: an agent's conversation in Claude's background worker

Live case (2026-10-20, Claude Code 2.1.304): `overcode-main` was messaged over
Remote Control; Claude moved its conversation into its background daemon under
a new id (`df7bb2f5…`, a full copy of `b08316a9…` plus new turns). overcode
filed it as foreign background work (`foreign_sessions[df7…].kind = "bg"`), so
the agent's light went stale and a 🌙 row flickered under it.

## What changed

- `claude_bg.continues(job, agent_session_id)`: the job is the agent's own
  conversation when it has `interactiveLineage: true`, none of `/fork`'s
  fields (`forkSourceAlive`, `forkBoundaryAt`, `forkSessionId`,
  `forkParentSessionId`), and its transcript holds the last record `uuid` of
  the agent's active transcript. None when a transcript is missing (asked
  again later). About 1 ms on the real 5 MB transcript.
- Hook handler: a registered job is asked once (`continues` in its foreign
  entry). True → new placement `continued`: adopted like `own` (record,
  `agent_session_ids`, restart) with no 🔀 warning, plus a
  `bg_worker: {session_id, pane_pid, at}` note in hook state. Back to the
  pane (an own event from `pane_pid`) drops the note; a new conversation from
  `pane_pid` is followed too, unless another agent owns it (still `shared`).
  Entries already filed `kind: bg` without `continues` (Mike's live state)
  are placed again once, on the worker's next event.
- Engine: no 🌙 row for a job whose `sessionId` is one of its agent's
  conversations, or that `ClaudeBgSource` found to continue the agent's active
  conversation (`FleetAgent.active`; once per job), so no flash before the
  first hook event. Ledger: a segment opened for such a session (a 🌙 row
  billed before the fix) is restated to nothing with a `correction` row when
  its row goes (`BgLedger.sync(owned=...)`).
- Spend: the agent reads the new transcript after the one it copied, so the
  fork dedup (reader v3) leaves the copy out. `get_session_stats` now also
  reads the active conversation when `history.jsonl` lists no prompt of it.
  `get_session_window_token_usage` (burn rate) now takes off a later
  conversation's copied prefix; before, any full copy counted twice in the
  window.
- Agent row: 🌙 badge (`bg_worker`, `Bw` emoji-free) in the status detail while
  the note names the current conversation; hover "running in Claude's
  background worker (Remote Control, /background); still this agent".

## Decisions

- Signal: `interactiveLineage` + no fork fields + content check. Read from the
  2.1.304 binary: the job writer sets `interactiveLineage: origin === "repl"`;
  the REPL hand-off writes `true`; `/fork` records the fork fields.
- Not used: process ancestry (the daemon is spawned by whichever CLI needed it
  first, often the agent's, so `claude --bg` work descends from it too; the
  live worker did descend from the agent's CLI) and the worker's environment
  (a nested Claude inherits OVERCODE_* too). `ps -E` was not needed.
- `/background` (`/bg`, "send this session to the background and free the
  terminal") now reads as the agent continuing, not a 🌙 row: it is the
  agent's conversation leaving its pane. The tui-guide's 🌙 section now names
  `/fork` where it said `/bg`.
- A job whose `sessionId` is one of the agent's conversations
  (`claude --bg --resume <agent's id>`, which appends to the agent's own
  transcript) gets no 🌙 row any more; the ledger already skipped it.
- No 🔀 warning on a continuation: it is expected, and the 🌙 badge says it.

## Gaps

- An older Claude whose `/fork` recorded no fork fields would read as a
  continuation if the agent wrote nothing between the copy and the fork's
  first hook event.
- A nested Claude that resumed the agent's own conversation and then sent it
  to the background reads as the agent.
- No live run of the new code against a real Remote Control move (it would
  touch the real fleet). `continues` was checked read-only against the real
  job record and transcripts: True for the moved conversation, False for an
  older one and for the same record with fork fields.

- Restart / rename-by-restart of such an agent used to type its launch
  line into the still-running Claude after a fixed 3 s (#549). Fixed
  generically: the line is typed only when the pane's shell owns the
  terminal (`process_resources.shell_in_foreground`: /proc on Linux, ps on
  macOS), and a restart whose agent doesn't exit within 15 s types
  nothing. A restart still doesn't ask whether to pull the conversation
  back from the worker.

## Tests

`tests/unit/test_bg_continuation.py` (36, both hook-state layouts),
`test_layout_matrix.py` (+4: the 🌙 badge at 120-260 columns, every detail
level, emoji and emoji-free).
