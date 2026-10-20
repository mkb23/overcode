# billing-merge-ux: merging is an explicit action, and works in bulk

Merging 19 `shirka*` codes into `shirka` meant renaming each one onto
`shirka` and confirming twice. Now rename only renames, and merge is its
own action that takes many codes at once.

## What changed

- `codes.py`: `merge_many(whats, into)`: one write, refuses (nothing
  merged) an unknown code, the target itself, or an unknown target (it never
  makes one). `merge` and `collapse` both go through the same `_fold`.
  `rename` onto a taken name now says "taken … (merge CODE --into NAME)".
- Dialog (`m`, `billing_codes_modal.py`):
  - Rename refuses another code's current name as you type: "taken by
    'shirka' — press M to merge into it". `Enter` drops back to the list
    with that message. No merge path is left in rename.
  - `M` = Merge into…: the marked codes, else the highlighted one. A picker
    (mode `merge`) lists unmerged codes other than the sources, with names
    that start with the query first (previous names match too). `↑↓` +
    `Enter`; an exact typed name wins; an unknown name says "merge never
    makes a code (r renames)". The confirm says "merge 19 codes ($2,832.88,
    32 agents) into shirka? ↵ confirm · esc cancel" (`esc` goes back to the
    picker, and typing goes back to filtering). Afterwards: "✓ merged 19
    codes into shirka · undo: m then u".
  - `*` marks every markable code shown (after a `/` filter); pressing it
    again when they're all marked unmarks them.
  - Border: `r rename · M merge · c collapse · u unmerge · / filter ·
    * mark shown · space mark · ? · esc`; `?` puts the other keys in the tip line.
  - `MergeRequested` now carries `code_ids`/`names`; the app handler calls
    `merge_many`.
- CLI: `overcode billing merge CODE... --into TARGET [--dry-run] [-y]`.
  `merge A B` (no `--into`, exactly two) still means A into B. Patterns
  (`*?[`) are matched with fnmatchcase against current names of unmerged codes,
  excluding the target. `--dry-run` lists names, spend and agents. It asks
  first only when a pattern picked the codes.
- Docs: tui-guide (keys table, the merge paragraph, a "family of codes"
  recipe), cli-reference, configuration, advanced-features. Palette keywords.

## Calls made

- Without `--into`, more than two positionals is an error, not "the last
  one is the target". That avoids a typo silently merging codes.
- The default code can be a target but not a source of `M` (consistent with
  marks/collapse: a merged-away default would leave `billing.default_code`
  naming a previous name).
- After a refused rename, `M` on the same code starts the picker on the
  refused name.
- `esc` in the confirm goes back one step (to the picker), not all the way
  to the list.
- The CLI confirm is per pattern only, like collapse with filters. Named
  codes merge without asking.

## Tests

- `tests/unit/test_billing_merge.py` (33): `merge_many` single write,
  refusals, rename hint; CLI old form, multi, glob, dry-run, confirm,
  merged codes skipped, refusals; dialog `*`, M on focused/marked, picker
  filtering/order, exact name, no new codes, default not a source, hints,
  widths at 120–260 in every picker state; a `make_tui` pilot test (filter,
  `*`, unmark, `M`, confirm, registry + sessions.json).
- `test_billing_codes_tui.py`: the old rename-to-merge tests became
  rename-refused + `M` tests.
- Full unit suite: 9738 passed, 17 skipped. ruff clean.
