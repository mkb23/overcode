# Usage log: fine-grained keystroke analytics

- Date: 2026-09-27
- Issues: #483, #484

Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)

## 1. Foundation: the usage log (fine-grained keystroke analytics)

### 1.1 What is captured

Every input event is recorded, with the action it resolved to and the context
it happened in:

| kind | when | key fields |
|---|---|---|
| `key` | every key the app sees, including keys inside modals | `key`, `char`*, `ctx`, `dt`, `rep` |
| `action` | an action was run (or refused) | `action`, `via`, `ok`, `ctx` |
| `unbound` | a key in list context that no binding handles | `key`, `ctx` |
| `palette_query` | each palette keystroke settles (debounced 400 ms) | `q`, `n_results` |
| `palette_pick` | a command is chosen from the palette | `action`, `q`, `rank` |
| `click` | a mouse click on a row, a header or a dialog button | `target`, `action?` |
| `dialog` | a modal opens or closes | `name`, `phase: open\|ok\|cancel`, `dur_ms` |
| `focus` | the focused agent changes | `agent_ref`, `how: key\|click\|jump\|auto` |
| `nudge` | a journey nudge is shown or answered | `id`, `surface`, `outcome` |
| `tui` | TUI start, stop, attach or detach | `size`, `version` |
| `cli` | an `overcode <cmd>` invocation | `cmd`, `flags` (names only, never values) |

`via` is one of `key | palette | click | cli | agent | auto`. `agent` means the
overagent drove the action through the view-control API (§3.3). That activity
is logged but **never counts toward the user's own fluency**.

\* `char` is redacted in text-entry contexts (see 1.3).

Common fields on every record:

```json
{"v":1, "t":1790000000123.4, "host":"mbp", "sid":"<tui-run uuid>", "ts":"agents",
 "kind":"key", "key":"j", "ctx":"list", "dt":212, "rep":3}
```

- `t` is epoch milliseconds.
- `host` is the machine. The log is per machine today; `host` is there so
  sister machines' logs can later be merged into one journey (§5, decision 5).
- `dt` is milliseconds since the previous input event.
- `rep` counts consecutive identical keys.
- `ctx` is the input context: `list`, `command_bar`, `annotation`,
  `standing_orders`, `palette`, `help`, `fullscreen`, `modal:<name>` or
  `terminal`.

### 1.2 Capture points (one chokepoint per layer)

These are small, contained changes to `tui.py`:

1. **Raw keys:** override `SupervisorTUI.on_event`. Textual's `App.on_event`
   sees every input event before the focused widget does, so this catches
   keys that modals `stop()`. The existing `on_key` (`tui.py:4682`) misses
   those keys.
2. **Resolved actions:** override `run_action`, recording the action name and
   whether `check_action` allowed it. Blocked actions are a signal too: "you
   pressed `x` while help was open".
3. **Palette:** `on_command_palette_command_chosen` (`tui.py:4586`) calls
   `action_*` directly and bypasses `run_action`, so it needs one explicit
   `record()` with `via=palette`, the query and the rank. The palette widget
   also emits `palette_query`.
4. **Clicks:** `on_column_header_clicked`, row clicks, and dialog buttons.
5. **Dialogs:** `_dialog_will_open` and `_dialog_did_close` already bracket
   every modal, so they get `dialog` records with duration and outcome for free.
6. **CLI:** a callback in the root Typer app appends one `cli` record per
   invocation. The overagent's CLI calls carry `OVERCODE_SESSION_NAME` and are
   logged as `via=agent`.

### 1.3 Privacy and consent

- **Everything stays on this machine.** The log lives under
  `~/.overcode/activity/`, and nothing reads it except overcode and agents you
  run.
- **Kill switch.** `activity.record: on|off` in `config.yaml`, plus the palette
  command "Pause activity recording" for the current TUI run. While recording is
  off, **no record is created at all**; a hidden flag on a stored record is not
  good enough.
- **Never what you say to your agents.** Anything typed into tmux or sent to
  an agent is always redacted, with no opt-in: the command bar, standing orders
  and the new-agent prompt. Printable keys there are logged as `"key":"<c>"`
  without the character. Everything else is kept: timing, backspaces, cursor
  movement, pastes (length only), submit or cancel, and time-to-submit. That is
  enough to see hesitation and editing effort.
- **Overcode's own metadata is recorded in full**, because it is what good
  suggestions are made from: human annotations (`a`), tag names, agent names
  (new-agent name field, rename), palette queries, value and budget edits, and
  column and sort choices. This lets the journey and the overagent say things
  like "you tag by repo name by hand on every launch; `--tag` or a wrapper can do
  that".
- **Tests never write the log.** An autouse fixture sets `activity.record=off`,
  the same way #485 stubs out daemons. Pilot-driven keys would otherwise pollute
  a developer's real journey.

### 1.4 Storage and performance

- **Files.** `~/.overcode/activity/YYYY-MM.jsonl`, one file per month for all
  tmux sessions, since the journey is about the *person*, not one session.
  Closed months are gzipped. The default retention is 12 months; `activity`
  records are small (about 120 B), so heavy use (20k events a day) comes to
  about 70 MB a year before gzip.
- **Writes are buffered.** `record()` appends a dict to an in-memory list. The
  existing 1 s status timer flushes it with one `O_APPEND` write, and the list is
  also flushed at exit and on detach. Records stay under PIPE_BUF, the same
  pattern `append_hook_event` relies on.
- **Budget test.** Add `record()` < 20 µs per event and flush < 1 ms per 100
  events to the scale-budget suite next to `_publish_state`.
- **Month summaries are cached.** Each closed month gets a
  `YYYY-MM.summary.json` with per-action counts by `via`, first and last use,
  and n-gram tallies. The journey then reads 11 small summaries plus the live
  month, not 12 months of raw keys.

### 1.5 Derived analytics (`usage_analytics.py`, pure functions)

These are candidate signals. Each one is a fold over events, and each one is
a **hypothesis** until it has been shown to be real and useful (see "Proving
the signals" below).

| signal | derivation | what it's for |
|---|---|---|
| **fluency per action** | uses, and the share done by key or palette rather than click | the mastery ladder (§2.2) |
| **the hard way** | the same outcome reached by a slow path, e.g. `j`×≥6 to land on the agent `b` would have jumped to; header click instead of `S`; palette pick of an action that has a key | the most valuable nudge |
| **phantom keys** | `unbound` keys ranked by frequency, with what the user did next | discovery ("you press `v` expecting…") and product input: keys people reach for |
| **blocked actions** | `ok:false`, grouped by the reason in `check_action` | UI friction |
| **toggle regret** | an action followed by its own inverse within 3 s (`m m`, `Z Z`) | confusing toggles |
| **dialog abandonment** | `dialog` open → `cancel`, and time spent before cancelling | dialogs that are too hard |
| **help lookups** | help opened → key pressed within 10 s of closing → action | which keys people have to look up; a ✓ once they stop |
| **palette misses** | `palette_query` with `n_results=0`, or long queries before a pick | missing features and missing synonyms |
| **hesitation** | gap before an action > 2 s after a context change | where people have to think |
| **navigation cost** | keystrokes between "an agent needed attention" and focus landing on it | an oversight-speed metric (`b` adoption shows up here) |
| **session shape** | attended minutes, keys per minute, contexts visited | the baseline for all of the above |
| **action sequences** | common 2- and 3-action sequences | a cheap index for session mining (below), not a source of competencies on its own |

**Session mining (where competencies come from).** Short action sequences
are not powerful enough to find real workflows. Instead, an agent mines the
data: from time to time (on demand at first), a Claude session reads a window
of the activity log together with `sessions.json`, the hook-event logs and the
agents' status history, and writes up the workflows it sees. For example: "on
Mondays you restart every dead agent one at a time, then re-send the same
standing order to each". Each write-up is a proposed competency, hard-way
pattern or feature gap. A human (you) reviews it before it enters the catalog
(§2.1), and it ships with a detector plus a negative-control test (§2.6). The
catalog grows from observed use rather than from guesses.

**Proving the signals.** Every signal starts as `experimental` and appears in
`overcode activity summary` under that label. A signal graduates to feeding the
journey or the director only when both hold:

- it fires on real data at a plausible rate
- the nudges it drives get engaged more often than they get dismissed
  (`nudge` outcomes, §1.1)

A signal that never earns its place is removed. `overcode activity summary
--signals` reports each one's firing rate and nudge engagement, so the decision
rests on data.

**CLI (`overcode activity`).** `overcode usage` is already taken by the
subscription-limits command, so this is a new command:

- `overcode activity summary [--since 7d] [--json]`: every table above.
- `overcode activity keys [--since 24h]`: raw events, for debugging.
- `overcode activity stream [--detail rollup|significant|verbose]`: follows the
  live file by byte offset, starting at EOF. `rollup` prints a digest every 15
  minutes with a pre-written one-line `description`. Built for the overagent's
  `Monitor` tool.

### 1.6 Lessons to design in from the start

- **One way to assemble events.** The TUI panel, `overcode journey` and
  `overcode activity` must all call the same `load_events(since, …)`. When two
  surfaces assemble events separately, they end up showing different journeys.
- **Every nudge button must record its outcome.** It's easy to wire "show me"
  so it opens a panel but never records `engaged`, which silently kills the
  feedback loop.
- **Say what an action is, not how it's done.** A label like "Switch agents
  (j/k)" makes clicks look like key use. Name the outcome.


---
Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)
