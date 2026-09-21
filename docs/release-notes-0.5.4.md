# Overcode 0.5.4 Release Notes

0.5.4 is a performance release for opencode and opencode2 fleets (#476).
Switching between agents in the TUI became laggy on machines with a large
`opencode.db`, and the cause was the stats readers' message scan: the
TUI calls it once a second per agent for the burn rate and every five
seconds for the token columns, and against a real store each call cost
75-115 ms per agent. Two things made it expensive, both fixed at the
source rather than cached over.

**One bounded probe per conversation.** Every `/new` adds a conversation
id to the agent, and the scan used to fetch all of them with one
`WHERE session_id IN (...) ORDER BY ... LIMIT 500*N`. SQLite answers that
through a temp B-tree fed by every candidate row, so the cost grew roughly
quadratically with the number of conversations: about 2 ms with one and
115 ms with eight. The scan now issues one index-ordered probe per
conversation that stops at its own limit and merges the rows in Python.
This also makes the per-session scan limit mean what it always said: the
newest conversations can no longer swallow the whole budget and drop older
ones from the interaction count.

**Row bodies are parsed once.** On a real store an opencode2 assistant row
carries its tool output inline, a kilobyte at the median and over a
megabyte at the tail, and the scan fetched and JSON-parsed 500 of them per
conversation on every call. It now selects only the narrow columns, which
never touch SQLite's overflow pages, and reads a body only when the row's
`time_updated` has changed. A turn that is still streaming is never cached,
so nothing goes stale.

On a generated 2.3 GB store, an opencode2 `get_stats` with eight owned
conversations fell from 254 ms to 9.6 ms and opencode from 23 ms to 6.7 ms.
The profiling, the alternatives considered (including the 60 s TTL cache
proposed on the issue, declined because it would have hidden the quadratic
query and cut opencode's stats freshness from 5 s to 60 s) and the design
are written up in `docs/design/opencode-stats-reader-performance.md`.

Two scripts ship with it. `scripts/make_opencode_store.py --size-gb 2`
generates a store with opencode's real schema, indexes and row-size
distribution, and `scripts/bench_opencode_scan.py` times both readers
against whatever store `OPENCODE_DB` names, so a regression can be measured
on a meaty store rather than the unit fixtures.

## Also in 0.5.4: less tmux and subprocess load from polling

A separate investigation into typing lag in tmux while overcode runs
found that the load is the *number* of tmux commands and subprocesses the
TUI and daemon issue per second, not the size of any one of them: the tmux
server is single-threaded and serves every `capture-pane`, `resize-window`
and `list-windows` in line with keystrokes. These changes cut that count
without touching what the focused agent sees.

- **Hook events start in 20 ms instead of 80 ms.** Every backend runs
  `overcode hook-handler` on every tool call, and the `overcode` console
  script used to import the whole typer CLI first. A new
  `overcode.entrypoint` dispatches the bare `hook-handler` form before the
  CLI, rich or libtmux are imported. Reinstall (`pip install -e .` or `uv
  sync`) to pick up the new console script.
- **Non-focused agents are captured round-robin.** The TUI's 250 ms fast
  path still captures the focused agent every tick, and any agent the
  daemon is not reporting on, but agents the daemon already covers rotate
  through a 1-in-4 slot (about 1 Hz each) and take their status from
  daemon state. Their pane-derived columns can lag by up to a second.
- **The 15 s resize sweep only resizes windows that changed.** One
  `list-windows` reports current sizes; `resize-window` at the same size
  was not free (it fires layout hooks and redraws every client).
- **Listing sessions costs one tmux command.** Terminated-window detection
  and the legacy window-index migration share a single `list-windows`
  instead of two commands per agent every 10 s.
- **Git scans are deduplicated and slower.** Diff and untracked counts run
  once per distinct directory, every third 5 s sweep, rather than per agent
  every sweep; the daemon reads `.git/HEAD` directly instead of spawning
  two git processes per agent every 2 s, and falls back to git for layouts
  it does not recognise.
