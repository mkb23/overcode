# billing-codes-dialog: every code's spend in the TUI, renamed in place

## What changed

- `tui_widgets/billing_codes_modal.py` (new): `BillingCodesModal` (palette
  "Billing codes…", Settings group) and `load_code_rows(ledger)`. One row
  per code (merged ones folded away), by total: name, total, this month,
  last month, agents, running, source; totals underneath; the focused
  code's previous names and id in the tip line.
  - `Enter`/`r` renames in place, checked as typed (empty, id-shaped, an
    unregistered legacy string refused; another code's previous name warned
    but allowed, as the registry allows it). Another code's current name
    offers a merge; a second `Enter` confirms ("merge X into Y? press Enter
    again"); any edit resets the confirm.
  - `/` filters as you type (names and previous names), `a`/`→` drills
    into the code's agents (name, all-time spend, running), `Esc`/`q` close.
- `ledger.Ledger.report`: `rows=` (one read of the ledger for several
  periods) and `agents=` (per-code, per-agent spend). Additive; callers
  unchanged.
- `tui_actions/dialogs.py`: `action_open_billing_codes`; the ledger is read
  in a worker once per open (`totals(save=False)` + three `report`s over
  one `iter_rows`), never per key and never polled. Rename/merge run in a
  worker: `codes.rename` / `codes.merge`, then `fill_billing_codes` (the
  agents' name copies, as `overcode billing rename` does) and
  `signal_activity`, which wakes the daemon for a full tick that publishes
  the new names to the engine's views. A rename updates the row in place;
  a merge reads the figures again.
- Docs: tui-guide (palette table, Billing codes section with the dialog's
  keys), configuration (Billing Codes).

## Calls made

- No default hotkey: `B` is the cost budget, `ctrl+b` is tmux's prefix;
  nothing free reads as "billing". Palette only (shows in help).
- `Enter` is rename (as asked), so the optional drill-down is `a`/`→`
  rather than `Enter`.
- Agents/running columns are `totals()`'s segment counts (what
  `overcode billing` shows), not live-agent counts.
- Unregistered legacy strings show as rows (source "legacy", orange) but
  cannot be renamed; doctor already flags them.
- Local codes only: a sister's codes are renamed on its machine.
- The modal is a fixed 100-wide dialog; a test renders every mode at
  120/160/200/260 columns and checks every line is exactly the inner width
  (the layout matrix covers table rows, not dialogs).

## Tests

- `tests/unit/test_billing_codes_tui.py` (37): `load_code_rows` (months,
  members, merge, one ledger read), the dialog's keys and validation, the
  merge confirm, filter, drill-down, widths, palette/help, and two pilot
  tests (`make_tui`): rename reaches the registry and sessions.json name
  copies; merge with the double Enter re-reads the figures.
- Full unit suite: 9413 passed, 17 skipped. ruff clean.
