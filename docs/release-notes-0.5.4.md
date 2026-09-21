# Overcode 0.5.4 Release Notes

0.5.4 is a performance release. It fixes laggy agent switching with opencode
and opencode2 fleets (#476) and cuts the tmux and subprocess load the TUI
and daemon put on a machine while polling.

The opencode stats readers were the cause of #476. The TUI reads each
agent's message scan once a second for the burn rate and every five seconds
for the token columns, and on a large `opencode.db` each read cost up to
115 ms per agent. Two things made it expensive. Every `/new` adds a
conversation id, and the scan fetched all of them through one
`IN (...) ORDER BY ... LIMIT` that SQLite answers by sorting every candidate
row, so the cost grew with the square of the conversation count. And on a
real store an opencode2 assistant row carries its tool output inline,
kilobytes at the median and over a megabyte at the tail, so the scan was
re-fetching and re-parsing hundreds of those on every call. The scan now
issues one index-ordered probe per conversation, selects only the narrow
columns, and parses a row body once per version; a turn still streaming is
never cached. On a generated 2.3 GB store, an opencode2 read with eight
owned conversations fell from 254 ms to 9.6 ms. The profiling and the
alternatives considered, including the TTL cache proposed on the issue,
are in `docs/design/opencode-stats-reader-performance.md`, and two scripts
let a regression be measured on a realistic store:
`scripts/make_opencode_store.py` generates one and
`scripts/bench_opencode_scan.py` times both readers against it.

The polling changes come from a separate look at typing lag in tmux. The
tmux server is single-threaded and serves every command in line with
keystrokes, so what matters is how many commands per second overcode
issues. The `overcode` console script now dispatches `hook-handler` before
importing the CLI, so each hook event starts in about 20 ms instead of 80,
on every tool call of every agent; reinstall to pick up the new entry
point. The TUI still captures the focused agent every 250 ms but rotates
the others through a one-in-four slot and takes their status from the
daemon, so their pane-derived columns can lag by up to a second. The
periodic resize sweep only resizes windows whose size actually changed,
listing sessions costs one tmux command instead of two per agent, git diff
and untracked counts run once per directory every third sweep, and the
daemon reads `.git/HEAD` directly rather than spawning two git processes
per agent every two seconds.
