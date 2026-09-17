# Overcode 0.5.2 Release Notes

0.5.2 fixes mouse-wheel and PageUp scrolling for full-screen agent backends in the `overcode tmux` split: opencode draws on the alternate screen and scrolls its own transcript, so the bottom-pane bindings now hand those gestures to the program instead of forcing an empty, frozen tmux copy mode. Claude Code's inline scrollback behaves exactly as before, and a pane left stuck in copy mode is released by the next wheel tick.

## Also in 0.5.2

**TUI selection stays on the agent when the list re-orders (#471).** In the
status and value sort modes the highlight used to stay on the *row* while a
different agent slid into it, so the TUI and the synced tmux pane drifted
apart until you tapped `j`/`k`. Selection is now anchored to the agent id
across every re-sort, including the periodic refresh.

**Folds survive restarts (#464).** Parents collapsed with `X` in tree mode
are persisted per session and restored when the TUI comes back.

**Model metadata for the long tail (#473).** A transcoded models.dev
catalog (~1,000 text models) now backs the context-window and pricing
lookups behind the curated tables, so GLM, Kimi, DeepSeek, Qwen, MiniMax and
friends get a real `CTX%` and `$` instead of a dash. Lookups use the
freshest copy on the machine — `overcode models refresh` writes one under
`~/.overcode/cache`, opencode's own cache is read when present — with a
bundled snapshot as the offline fallback. No unattended network by
default; `model_metadata.auto_refresh: true` opts the daemon in. New
`overcode models info|lookup|refresh` commands, and `doctor` nudges when
the catalog is over 90 days old.

**opencode `CTX%` matches opencode's console (#469).** opencode agents now
divide by the `limit.context` in opencode's own cached models.dev catalog —
the same figure its "N% used" uses — with the bundled snapshot as fallback.
The per-backend rules are written up in docs/backends.md.

**Default backend surfaced in `config init` / `config show` (#470).** The
`new_agent_defaults.backend` key (already honoured by `overcode launch`, the
`n` modal and the `G` defaults modal) is now in the generated template and
the `config show` output.

**History rotation streams (#468, #465).** `agent_status_history.csv`
rotation no longer loads the whole file to rotate it — a legacy 2 GB file
is archived in one streaming pass instead of being materialised in the
daemon's memory. The opt-in `status_changes.csv` diagnostic gets the same
hard cap as `event_loop_timing.csv`.
