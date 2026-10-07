# The engine (0.6.0): one computer, many views

Status: accepted for 0.6.0 (2026-10-07). Supersedes the "daemon recorded
layer" step of the #507 plan, which it absorbs.

## Why

Two processes compute the same thing today. The monitor daemon detects every
agent's status every 2 s and syncs stats every 60 s. The TUI detects status
again on a 250 ms rotation, sweeps stats again every 5 s, reads the daemon's
state file every second, and only uses the daemon's status for agents it did
not capture that tick, and only while the file is fresh. The cost is:

- **Double the work.** Every reader and every detector runs twice per agent.
  Fleet-scale CPU reports (#486, #517, #524-#527) were all in this path.
- **Two truths.** The daemon's `state_since`, the TUI widget's colour timer
  and the stall/bell logic each keep their own clock (#507 finding 6). The
  bell and the timeline disagree with what is on screen.
- **Polling everywhere.** A status change reaches the screen only when some
  timer next looks.

## Roles

| Role | Who | Does |
|---|---|---|
| **Sensors** | hook handler, opencode/hermes plugins, grok hooks | write `hook_state_<agent>.json` and append `hook_events_<agent>.jsonl`, as today |
| **Engine** | the monitor daemon | the only place status, stats, cost, energy, burn, episodes, the bell and history are computed |
| **Views** | TUI, CLI, sister API | render what the engine publishes; compute nothing about agents |

One exception stays in the TUI: the focused agent's pane preview at 250 ms. It
is a picture of a terminal, not a fact about the agent, and routing it
through the engine would only add latency.

## Freshness contract (enforced by tests)

| Output | Attended | Unattended |
|---|---|---|
| Hook-driven status change, e.g. Stop or a permission prompt | ≤ 0.5 s | ≤ 2 s |
| Pane-derived signals (interrupt marker, shell/monitor counts, dead shell) | ≤ 1 s focused, ≤ 2 s others | ≤ 10 s, ambiguous agents only |
| Token, cost and energy columns | 5 s | 60 s |
| Burn rate | ≤ 10 s | not computed |
| Timeline | 30 s | recorded continuously, no gaps |
| Bell / notification | on episode start | on episode start |

"Attended" means at least one visible view is subscribed (see Attendance).
Each cadence is a named constant. `tests/unit/test_engine_contract.py` checks
the constants against this table, so a "fix" that slows a loop fails a test
rather than shipping (the #476/#517 lesson).

## The sensor path: wake on change, not on a timer

The engine keeps a stat signature `(st_mtime_ns, st_size)` for each agent's
hook state file and event log. Its wake loop stats them, 2 per agent:
- every 0.25 s while attended
- every 2 s while unattended

That is about 400 stats/s at 50 agents attended, roughly 1 ms of CPU per
second, and 50/s unattended. Only an agent whose signature changed is
re-detected. This is backend-agnostic: no plugin changes, no inotify or
kqueue dependency, and it works when a sensor wrote while the engine was
down.

A later optimisation, not needed for 0.6.0, is for the Python hook handler
to send a one-byte datagram to `engine.wake` after writing, which ends the
engine's sleep at once. Losing a datagram changes nothing, because the stat
scan still finds the change.

## The engine's tick

1. **Wake scan** (above). Changed agents go on the detect list.
2. **Pane checks.** One `tmux list-panes` per tick gives each pane's activity
   and the attached-client count. An agent's pane is captured only when
   both are true:
   - its state is ambiguous: stopped, waiting, unknown, or holding
     pane-derived counts;
   - its pane changed since the last capture, or its check is due (focused
     1 s; others 2 s attended, 10 s unattended).

   Working agents are driven by hooks and need no capture. This replaces
   `select_capture_sessions` and the pane-change gate in the TUI.
3. **Detect** each agent on the list: the existing `HookStatusDetector`,
   unchanged, producing the live 4-colour `StatusDetail`.
4. **Record** (the #507 layer, below).
5. **Stats** on their cadence through the incremental readers. Burn uses
   the per-turn window indexes (#517, #524, #525).
6. **Publish** deltas to subscribers. The state file is rewritten atomically
   at most once a second, and only if something changed.

## Recorded layer (#507)

The live colour is what the detector says this tick. The recorded colour is
what history, the timeline, timers and the bell use. Per agent, the engine
keeps the current **episode** (colour, start time) and the previous one.

- **Merging blips.** An excursion that returns to the previous colour within
  **G = 20 s** is merged into the surrounding episode: the episode's start
  time snaps back, and the excursion is logged as a merged blip, so a 👤
  tick can mark a prompt inside it. At G = 20 s this merges 14% of
  red/orange episodes in three weeks of history.
- **Time-in-state** is measured from the recorded episode's start, and
  resets only when the colour changes, not on a sub-state change.
- **Green/non-green totals** keep counting live seconds, so percentages stay
  honest.
- **Input needed** = a red or orange episode.
- **Bell.** It rings when an input-needed episode starts after the person
  last visited the agent (`visited_at`, persisted). Nothing else rings it:
  no TUI memory of transitions and no first-sight rule (the d679fff
  stopgap goes).
- **Overdue yellow.** A yellow episode whose wakeup is overdue, or whose
  obligations have been silent past their bound, escalates to red with an
  `overdue` badge, so red is trustworthy in both directions.

Episodes are appended to `episodes_<agent>.jsonl`, one line per closed or
merged episode, rotated by size. The timeline and history read this instead
of the 2 s status CSV. The CSV is kept for one release for compatibility
and dropped in 0.7.

## Publishing: `engine.sock`

A Unix stream socket at `~/.overcode/sessions/<tmux_session>/engine.sock`,
mode 0600. The protocol is newline-delimited JSON; every message has `t`
(the type) and `seq`.

Engine to view:
- `hello {version, seq}`, then `snapshot {seq, agents: {id: AgentView}, fleet: {...}}`.
- `delta {seq, agents: {id: partial AgentView}, removed: [id]}`. These are
  changes only. A quiet tick sends nothing, and a `ping {seq}` goes out
  every 5 s.
- `bell {agent, episode}` is a discrete event, so views never derive it.

View to engine:
- `subscribe {topics}`
- `visible {bool}`: the view is or isn't being looked at (tmux client
  attached, terminal focused)
- `focus {agent}`

Rules:
- Each subscriber has a bounded outbound queue. A view that falls behind
  is disconnected and reconnects for a fresh snapshot, so a slow view
  never slows the engine.
- `AgentView` is a versioned dataclass in `engine_protocol.py`, shared by
  both sides.
- Applying every delta to a snapshot must equal the next snapshot. A
  property test enforces this.
- Over SSH, `ssh -L` forwards a Unix socket, so a sister could subscribe the
  same way. 0.6.0 keeps the HTTP sister API; moving sisters onto the socket
  is a 0.7 candidate.

## Attendance

Attended means at least one subscriber with `visible = true`, or a sister API
request in the last 15 s. A TUI reports its visibility from the tmux
attached-client check it already does, now sent over the socket.

What goes away:
- the `tui_attended` touch file
- the keypress-heartbeat rule
- the TUI's `unattended_status` tick

The daemon's own `session_attached` count remains a fallback for a TUI from
an older version that isn't on the socket.

**Unattended means event-driven recording at near-zero cost.** The engine
still stats the hook files every 2 s and records every transition exactly,
rings the bell and enforces budgets. It skips git, burn and all pane
captures except ambiguous agents every 10 s. Budget:
`test_unattended_idle_fleet_is_nearly_free`, for 50 idle agents, ≤ 0.5% of
a core.

## Restart and replay

The engine persists, per agent, the byte offset of the event log it has
consumed and its open episode. On start it replays each log from that
offset before serving, so transitions that happened while it was down still
reach history and the bell.

The engine starts with the first `launch` or TUI, and a lifecycle check
restarts it if it dies. A view that finds no socket shows a "engine not
running" banner and starts it. It never falls back to computing status
itself.

## What the TUI loses

These go:
- `fast_status` detection for non-focused agents
- the `slow_stats` sweep and git-diff sweep (git moves to the engine at 15 s)
- `daemon_status` polling
- `unattended_status`
- the stats executor
- the TUI-side bell and stall logic
- `select_capture_sessions`
- the pane-change gate

The TUI keeps:
- rendering
- input
- the focused-pane preview capture
- sister polling, as long as sisters stay on HTTP

Expected: the TUI's idle CPU becomes rendering only, and the fleet pays for
status and stats once.

As built (step 3):
- Every per-agent value a row draws is a `SessionDaemonState` field: besides
  status, episodes, stats, git and burn, the engine publishes the
  transcript values beside the token columns (`stats_available`,
  `work_median_seconds`, `context_window`, `file_subagent_count`), the
  pane-derived counts, `pr_number`, the attention fields behind the 🔔
  (`input_needed_since`, `visited_at`), and `time_base`/`time_base_at`
  instead of the green/non-green accumulators, which grow every tick.
- The TUI keeps one capture, the focused agent's pane at 250 ms, for that
  row's pane-derived columns (the preview pane only shows sisters and jobs).
- The mean spin (μ) stays in the TUI: one incremental read of the history
  CSV a second. It moves to the episode log with the CSV in 0.7.
- The bell rings when the engine confirms an input-needed episode (G after
  it starts). A Stop inside the detector's sticky-green window (#448, 1.5 s)
  shows when the window ends: the wake scan re-detects the agent then.
- Measured idle, 10 mock agents, private tmux server, top pane 200x23: the
  TUI used 5.4–5.5% of a core before and 4.8–4.9% after. What is left is
  Textual's rendering (the 250 ms row clock), the 10 Hz event-loop probe
  and the focused capture; the removed work grew with fleet size and
  activity, which an idle fleet does not show.

## Build order (each step green on its own)

1. `engine_protocol.py` (AgentView, messages, snapshot/delta application)
   with property tests.
2. The engine socket server in the daemon, publishing what the daemon
   already computes, behind the state file. The TUI subscribes, but still
   reads the file when the socket is absent.
3. Move ownership into the engine: the wake scan, pane-check selection, git
   and stats cadences, attendance. Delete the TUI-side computation.
4. The recorded layer and bell. Turn the replay harness into an engine
   harness (feed hook events and frames, assert recorded episodes and
   bells), and add the episode log.
5. Restart replay. Budgets: engine tick attended/unattended, socket fan-out
   at 50 agents × 3 views, TUI idle CPU.

## Tests that guard it

- Contract constants against the freshness table.
- Protocol property test: deltas applied to a snapshot equal the next
  snapshot.
- Engine replay harness: the existing `status_replay` scenarios, asserting
  recorded episodes, merges at G, and bells.
- Restart replay: kill the engine mid-scenario and restart it; the
  episodes must be identical to an uninterrupted run.
- Scale budgets: unattended idle fleet, attended tick, socket fan-out, TUI
  idle.
- e2e (container tier): a TUI attaches, detaches and reattaches; engine
  restart; socket missing → banner, then autostart.
