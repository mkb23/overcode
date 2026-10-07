# Agent Development Guidelines

Gotchas for AI agents working on the overcode codebase. Keep this short;
add an entry when something bites.

## Tests never touch the real fleet

- Run the unit suite with `python -m pytest -q tests/unit -p no:cacheprovider`
  (`pytest.ini` puts `src` on the path). `-m e2e` and
  `OVERCODE_SCALE_TESTS=1 ... tests/scale` are the slower tiers.
- The user's `agents` tmux session, the `overcode` split session and
  `~/.overcode` are live. Isolate with `OVERCODE_STATE_DIR`, `OVERCODE_DIR`
  and `OVERCODE_TMUX_SOCKET` (a private tmux server), and run pytest with
  `TMUX`/`TMUX_PANE` unset when you are inside tmux.
- `overcode`, `overcode monitor` and `overcode tmux` call
  `cli.split.open_split`, which creates/switches tmux sessions, respawns the
  monitor pane and finally `exec`s `tmux attach`. A CLI test that reaches it
  unmocked restarts the user's dashboard and replaces the pytest process.
  Patch `overcode.cli.split.open_split`.
- Fixtures are synthetic. Never copy real `~/.overcode` data into the repo.

## The TUI has one layout

- Since 0.6.0 the TUI only runs as the top pane of the `overcode tmux` split
  (`run_tui(session, sync_target)`). There is no standalone mode, no `compact`
  flag, no preview toggle and no pane-sync toggle.
- Everything that acts on tmux (zoom for dialogs and the sister view, window
  switching, agent-window resizing, split resize, detach on `q`) is gated on
  `SupervisorTUI.in_split`, i.e. a linked session was passed. Unit tests build
  the app without one, so they never touch tmux. Keep new tmux side effects
  behind that gate.
- The preview pane is only for sister agents and jobs view.
- Timer cadences in `tui.py` `TIMER_INTERVALS` are the product's freshness
  contract. Fix CPU by doing the work cheaper, never by polling slower.

## Keys, palette and help come from one place

- `SupervisorTUI.BINDINGS` is the `default` key preset. Presets
  (`data/keymaps/*.yaml`) and the user's `keys:` config are deltas
  (`keymap.py`); read keys through `self.keymap`, not `BINDINGS`.
- The command palette registry (`command_palette.COMMANDS`) titles and
  groups actions; the help overlay is built from it. A new action needs a
  palette entry to show up in help.
- Config naming an unknown or removed action or scope must warn, never
  crash. Removed names go in `keymap.REMOVED_ACTIONS` / `REMOVED_SCOPES`.
- Old `tui_preferences.json` and `sessions.json` files must keep loading:
  loaders ignore unknown keys, and `session_manager` migrates old field names.

## Summary line columns

- Columns are registered in `summary_columns.py` (`SUMMARY_COLUMNS`); each
  renders a cell, and `compute_column_widths` / `pad_and_join_cells` align
  them across rows for both the TUI and `overcode list`. Give a column's
  placeholder ("-", no data) the same shape as its value so the column does
  not jump as data arrives.

## Single sources

- Bundled wrapper scripts live only in `wrapper.BUNDLED_WRAPPERS`.
- Pricing lives in `pricing.py`; import from there.
- Tmux interfaces: `protocols.py` (Protocols), `implementations.py` (real),
  `mocks.py` (test doubles).

## Git

- Never `git push --force` / `--force-with-lease` unless the user asks, or
  the branch is one you created this session that nobody else can have
  touched. If you want to, something is off: fetch, look at the remote, ask,
  or use a new branch.
- Never commit API keys, tokens or secrets. Use environment variables or a
  git-ignored `.env`. If one slips into a commit, tell the user at once so
  they can rotate it; it stays in history.
