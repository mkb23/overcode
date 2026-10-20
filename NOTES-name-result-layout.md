# name-result-layout

## What changed

1. **NAME column.** Caps raised by 8 cells: low 42, med 38, high/full 34
   (`NAME_WIDTH_CAPS` in `tui_widgets/session_summary.py`). The app's
   `max_name_width` now measures the whole name cell
   (`SupervisorTUI._name_cell_width`): tree connector (tree sort), 🌙 / `~`
   (`name_lead`), the name and a fold's ` ▶`. Before, it measured
   `len(name)` only, so a nested or `~` name lost its last cells even with
   room under the cap. Still capped; still widened by virtual rows' titles.
2. **The summary keeps 24 cells.** The fleet in the layout matrix overflowed
   120 *and* 160 columns at every detail level, so the summary text was
   cropped away entirely, before this change too. Now
   `columns.layout.grid_room` cuts the column grid at a whole-column edge
   where the summary would get fewer than `SUMMARY_MIN_WIDTH` (24) cells;
   agent rows, virtual rows and the header cut at the same x, and hover
   (`column_at(..., width)`) ignores the cut-off columns. The layout
   invariants now check that every agent row has its ` │ ` and ≥ 24 cells
   of summary.
3. **RES column → `l` result mode.** `task_result` is gone from
   `SUMMARY_COLUMNS` (with its renderers, hover, sort key, fleet flag,
   `ColumnContext` fields and the TUI's `_reserve_result_width`).
   `SUMMARY_CONTENT_MODES` ends with `result`: 📭 unreported, ✅ / ❌ with
   the reason (whitespace collapsed; the kind's meaning when there is no
   reason), ⌛ timeout, coloured by `RESULT_KINDS`; placeholder
   `🏁 (no result)`. Virtual rows: ✅ / ❌ and the result text once
   finished, else `-`. Shared text: `tui_helpers.result_text`.
4. **Old config.** `REMOVED_COLUMNS` in `columns/registry.py`:
   `TUIPreferences.load` drops `task_result` overrides and a
   `col:task_result` sort (back to the default sort); `overcode view
   columns ... task_result` says where it went. An old TUI reading
   `summary_content_mode: "result"` falls back to the first mode (already
   handled by `action_cycle_summary_content`).

## Decisions

- `+8` caps: a nested child costs 2 cells per level of connector, `~` 1,
  🌙 3, a fold 2; 8 covers a depth-2 auto-named child at med without
  taking much from high/full where 40+ columns compete.
- `overcode list` already printed the full result after the activity; it
  keeps doing that (now via `result_text`) and simply loses the RES column.
  Its own name cap (20) is unchanged: the request was about the TUI.
- Sorting by result went with the column (no column, no sort key).
- Palette state labels for `l` shortened to fit seven: `beat`, `cmd`.
- The summary reserve costs columns: a fleet whose grid filled all but a
  few cells of the row now loses its rightmost columns instead of its
  summary (test_emoji_hover moved from 220 to 320 columns for that).
  `SUMMARY_MIN_WIDTH` is one constant if 24 proves too greedy.

## What got squeezed (layout matrix fleet, SVGs via OVERCODE_LAYOUT_SHOTS)

- 120 low/med: everything right of GIT (TOK, context, model, people…) is
  cut; summary ~28 cells.
- 120 high: cut after RPO; 120 full: cut after TYPE (KIND + NAME at 34).
- 160 low: cut inside the people/heartbeat group; med: after TOK; high:
  before the uptime group; full: after UN/PR.
- In every case the nested `└─~fix-flaky-integration-tests-main` is whole
  at low/med and loses its last cell at high/full (35 cells, cap 34).

## Tests

Layout matrix: new `test_nested_names_and_the_result_mode_hold` (tree
sort, nested `~` child with a failure result, all widths × levels × modes,
result text asserted at every width). Cycle order (result last), palette
fit, old prefs with `task_result`, view command message, `overcode list`
result text, virtual-row result mode, `result_text` unit tests.

Full unit suite: 9584 passed, 17 skipped. ruff clean.
