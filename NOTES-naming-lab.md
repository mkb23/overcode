# naming-lab: the naming lab (auto-naming P3, #542)

Section 9 of `docs/design/auto-naming.md` (now with a "Built" paragraph).

## What changed

- `naming_lab.py` (new, UI-free): `load_live` / `load_archived` (the last N
  archived agents, inputs from their day-bundle members), `NamingLabRunner`
  (parallel calls, up to 8; keyed by the rendered prompt, so only what
  changed runs, and A/B or a draft you come back to is free), `LabSpend`
  (the namer's ledger row under `overcode` and the naming daily cap,
  `namer_spend.json`), `rejected` (candidates passed over, short reasons),
  `collided` / `stats` (changed, collisions, mean length).
- `namer.py`: `gather_inputs(files=)`; `live_files` / `bundle_files`,
  `parse_prompts`, `edited_files` (the old readers wrap them).
- Saved advice: `~/.overcode/prompts/naming-advice.md`
  (`summarizer_prompts.save_naming_file` / `reset_naming_file`, atomic).
  `naming_policy.user_advice`: the saved file, else `naming.advice`;
  `load_advice(user=)`. The repo's `.overcode/naming.md` still follows.
- TUI: `tui_widgets/naming_lab.py` `NamingLab`; palette "Naming lab…"
  (Daemons group, next to "AI summary prompts…"), key scope `naming_lab`
  (keymap `SCOPES`, `default_scope_bindings`, `apply_keymap`, help
  section, `overcode keys --scope`); `action_open_naming_lab` in
  `tui_actions/dialogs.py`. Enter twice renames through `_run_rename(...,
  "human")`, then the row is updated in place.
- CLI: `overcode name lab [--archived N] [--advice-file] [--vs] [--csv] [--json]`.
- Docs: TUI guide (Agent names: the lab and its keys; palette table),
  cli-reference (`name lab`), configuration (Naming: the lab, the advice
  file; key scopes), design §9 built.

## Decisions

- **The editor holds the advice**, not the template: owner decision 6 makes
  the advice the policy, and it is what people tune; the template is one
  `Ctrl+O` away. Saving advice to a file (not config.yaml) mirrors the
  summary lab (atomic, delete-on-default) and never rewrites the user's
  config; the file stands in for `naming.advice`.
- Keys: Ctrl+S/R/L as the summary lab; Ctrl+T live/archived and Ctrl+D pin
  A as the design says (Ctrl+D is `priority` because the editor binds it
  to delete-right; Delete still works). Ctrl+N editor ↔ rows, because Tab
  is tmux's pane toggle in the split and never reaches the TUI. Ctrl+O
  advice/prompt, because Ctrl+P is in the vscode preset's forbidden list.
- Inputs are read when the lab opens or switches view (in a worker), not
  polled; the 2 s pass only calls for prompts with no result.
- Archived agents are checked against each other (taken = another archived
  row's name), as if they were the fleet. No bundle → no inputs → shown, not
  run ("no recorded brief or prompts").
- A collision = two agents with the same pick. Stats count the names the
  draft would leave (locked and "keep" rows keep their names).
- `name lab --advice-file` keeps the repo file appended (as the TUI lab and
  the namer do); `name suggest --advice-file` still replaces all advice.
- The lab's results are kept between opens (the runner lives on the app);
  config and spend are re-read at each open.
- The dialog is 160 wide (fleet rows need it), narrower terminals get
  `cols - 2`; all lines are checked at 120/160/200/260.

## Not done

- A's column doesn't flag A's own collisions (the header counts them).
- No per-row "why" wrap: the tip line shows the selected row's candidates
  and the model's reason.

## Tests

- `tests/unit/test_naming_lab.py` (44): engine (picks, rejections with
  reasons and rules, collisions, caching/A-B, retry, advice + repo file,
  archived from bundles, no bundle), spend (ledger rows under `overcode`,
  cap), advice file, dialog drawing (rows, A|B, widths at 120-260 in five
  states, apply confirm), pilot tests with `make_tui` and a stub model
  (open/run/save, Enter renames as human, Ctrl+T/Ctrl+D/Esc twice, no
  model), palette/scope, CLI (JSON + CSV + A/B, archived table, no model).
