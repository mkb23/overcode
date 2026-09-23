# Overcode 0.5.5 Release Notes

0.5.5 is a performance release. It fixes the monitor TUI and daemon code
paths whose cost grew without bound with fleet size, transcript size or
uptime, and makes opencode agents report their status accurately (#474).

The scaling work started from the report of the TUI sitting at 110 % CPU
for fifteen hours. The cause was the status bar's burn-rate figure, which
re-parsed every live agent's Claude transcript from the first byte once a
second. Once a pass took longer than a second, the passes overlapped and
the process pinned a core for the rest of its life. The audit behind this
release is in `docs/design/scaling-audit-2026-09-22.md`. It traces that
path and every other one like it to ten root causes, and none of the fixes
polls less often while someone is watching. Transcripts are now read
incrementally, so a warm burn-rate tick over 50 MB of transcripts fell
from 120 ms to 9 ms. Every periodic TUI worker is guarded against running
over itself: a call that arrives while one is running is coalesced rather
than stacked or dropped. The TUI's timers are also offset so they no
longer all fire in the same instant.

`sessions.json` and the daemon state file are re-read only when their size
or modification time changes, which takes a read of a 2,000-entry
`sessions.json` from 74 ms to under 0.01 ms. The daemon writes
`sessions.json` once per tick and only when something changed, where it
used to rewrite the whole file twice per agent every two seconds. At 50
agents that cuts a steady-state tick from 201 reads and 100 writes to 2
reads and 1 write. Terminated sessions move to an append-only
`archive.jsonl` after an hour (`session_archive.terminated_grace_seconds`
in `config.yaml`), so `sessions.json` no longer grows forever. An existing
`archive.json` is migrated on first access. The daemon holds one tmux
client open and captures only panes whose content signature changed. The
TUI does the same for non-focused panes, so an idle fleet of 50 now costs
it 8 tmux commands a second instead of 48. `agent_status_history.csv` is
written when a status changes, plus a 60 s keepalive, rather than on every
tick, so the file is 27 times smaller and the timeline reads it in
milliseconds. The hook event log is capped by size, and `presence_log.csv`
is read from its end.

Two behaviours are new while nobody is watching. The TUI pauses its
timers when no tmux client is attached. The daemon stretches its loop from
2 s to 10 s once no client is attached, no keypress has arrived for 60 s,
and no TUI or web status request has come in recently
(`monitor_daemon.interval_unattended_seconds` sets the length). Status
history is written on change, so the timeline has no gaps. Attaching again
brings everything back to full speed within about two seconds.

`scripts/bench_scaling.py` benchmarks the per-tick hot paths on a
synthetic fleet. `tests/scale/` turns the audit's budgets into tests; they
are opt-in with `OVERCODE_SCALE_TESTS=1`. One of those budgets, the
daemon's `_publish_state`, still measures about 23 ms against 20 ms.

For opencode, a `task` sub-agent's own turn no longer marks the parent as
finished halfway through the task, though the sub-agent's permission
prompts still show on the parent. A provider error now stays visible as
*error*, with its reason, instead of being overwritten by the idle event
that follows it a millisecond later. A double-Escape interrupt is not
counted as an error. An agent that quits with `/exit`, which emits no
event at all, now shows as *terminated*: the detector sees the bare shell
prompt in its pane. Recorded event streams from opencode v1.18.29 and a
reference description of the expected behaviour
(`tests/opencode_oracle.py`) now check the plugin on every test run.
