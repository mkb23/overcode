# trip-counter: notes

Trip counters for each agent's spend (Mike, 2026-10-20), plus Q% on the
trip and the burn's Q% mode.

## What changed

- `trip.py` (new): the base (`base_of`), the spend since (`trip_view`),
  Q%'s week rule, SUB$ on trips, `reset` (record write + engine wake).
- Record: `Session.trip_base` (TRIP_FIELDS at the reset, plus `pp7d` and
  `quota_week`) and `trip_reset_at` (ISO). `SessionManager.reset_trips`
  writes them for many ids in one locked write. Old files load (defaults
  None); archived records keep the base.
- Engine: `SessionDaemonState.trip` (`{"reset_at", TRIP_FIELDS…, "pp7d"?}`,
  None if never reset) and `subtree_trip_cost_usd`. Computed from the
  published totals, so it holds still between ticks (test: a reset fleet
  sends no deltas while quiet).
- Rows: TOK, ENRG, $, Q% and SUB$ render the trip with a `↺` lead in the
  cell's leading space (one cell, so no width change; `*` emoji-free —
  `~` already marks an auto name). Sort keys follow what is shown. Hover
  on $/TOK/ENRG: trip, total, when; on Q%: trip and this week's; on SUB$:
  rows' sum and total. Help legend has a `↺` line.
- Actions (palette, so they are in help): Reset trip counter, Reset trip +
  children, Reset all trip counters. CLI: `overcode trip reset <agent>
  [--tree] | --all`, `overcode trip show <agent> [--json]`. `overcode list`
  shows trips; `list --json` gains `spend: {total, trip, trip_reset_at}`.
- Burn in Q% mode: `compute_window_burn(quota_pp_of=…)` prices each
  session's window with `QuotaEngine.window_pp` (the Q% model, at the
  agent's model); published as `quota_pp` in each burn entry; the BURN
  cell shows `🔥0.42%/h` (5-cell number, same width as $/h), `-` without a
  quota view; colour still from $/h; sort by points/h; status bar shows
  `Δ0.84% 🔥0.42%/h`.

## Decisions

- No default key. Free single keys were g D O P Q (v/N are taken by the
  vscode preset); none reads as "trip", and a stray press would hide spend.
  `keys: reset_trip: [...]` binds one.
- The reset writer captures the base: spend from the record's stats in the
  same locked write; Q% points and week from the engine's state file. No
  confirm: nothing is lost (the odometer is untouched).
- Budgets: `$` colours and `budget_exceeded` stay on the total; the plain
  cost reads `↺$0.25/$4.00` (trip against budget, but the budget counts the
  total — said in help/hover/docs).
- SUB$: sum of what the subtree's rows show (trips where reset), `↺` while
  any member has a trip; total in the hover. Equal to "trip-based when all
  reset together", and still meaningful when only some were.
- Q% trip: same week (by the stored week start, ±1 h for drifting reset
  times; by the reset time if no week was known) → `pp − base`; else this
  week's pp. No reading now → keep the base.
- Rates (burn, tokens/s) are not trips. The status bar Σ stays total
  (fleet odometer). Virtual (subagent) rows are unchanged.
- Remote (sister) agents: refused with the shell command to run there.
- Quota burn ignores the long-context tier and per-message models inside
  the window (the window usage is not split by model): priced at the
  agent's current model.

## Not done

- No sister API for a remote reset.
- `overcode list` without a running daemon computes the trip from the
  record (SUB$ stays total there).

## Tests

New: `tests/unit/test_trip.py` (43), `tests/unit/test_burn_quota.py` (9),
the layout matrix's trip × every $ preset × width × level case, a status
bar quota-burn case. Full unit suite: 9635 passed, 17 skipped; ruff clean.
