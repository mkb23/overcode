# billing-defaults: where a new agent's code comes from; collapsing codes into the default

Mike's decisions of 2026-10-20 (task brief): a setting for a new root
agent's billing code (`name` | `folder` | `default`), and safe bulk
collapsing of codes into the default code, with undo.

## What changed

- `config.py`: `get_billing_defaults()` / `save_billing_defaults()` for
  `billing.new_agent_code` (`name`, the default; `folder`; `default`) and
  `billing.default_code` (`general`). A bad value warns and falls back;
  `get_billing_config` no longer calls the new keys unknown.
  `overcode config init`'s template documents `billing:`.
- `git_context.repo_name(dir)`: the folder mode's name; no subprocess.
- `launcher._root_billing_code` returns `(id, name)` by mode; the session's
  name copy is the code's current name. `--billing-code` still wins;
  children and forks unchanged (inherit).
- `namer._follow_code`: on every auto rename of a root, not just the
  first. `follow_agent_name` already refuses anything but a `default`
  code of exactly one tree, so a hand-renamed code stops following.
- `codes.py`: `merge` records `merge_added` (names it gave the target);
  `unmerge(what)`, `find_merged(what)`, `collapse(whats, into)` (one write;
  creates `into` as `human`; refuses the default itself, nothing merged).
- `ledger.py`: `Ledger.last_activity(rows)` (latest row per code) and
  `collapsible(total, running, last, now, under=, idle_days=)`, shared by
  the CLI and the dialog.
- CLI: `overcode billing collapse [CODE]... [--under] [--idle] [--dry-run]
  [-y]`, `overcode billing unmerge CODE`, `overcode billing codes --merged`.
- TUI, `m` dialog: settings line above the table (+ config warning);
  `space` marks, `$`/`i` mark under $X / idle N days, `c` collapses marked
  or highlighted (second `c` confirms, shows count + spend), `m` shows
  merged codes, `u` on one unmerges (second `u`; says live agents stay),
  `s` opens New agent defaults on its billing rows and comes back. The
  default code is ★ and can't be marked or collapsed; merged rows can't be
  renamed. `load_code_rows` now returns merged codes (`merged_into`) and
  `last`.
- TUI, New agent defaults (`G`): Billing code (cycle) and Default code
  (typed) rows; saved to `billing.*` alongside `new_agent_defaults`.
- TUI, new-agent dialog: the Billing row's default shows the mode's code
  (`(name)`, `(repo)`, `(general)`); typing it means the default.
- Docs: configuration (Billing Codes, sample config), cli-reference,
  tui-guide, design doc "As built".

## Calls made

- `folder` and `default` codes are minted (or flipped to) `human`: that is
  what keeps them from following auto names, with no new registry field.
  A `name`-mode code that a `folder` launch lands on becomes `human` too.
- `folder` = the repository's name; for a git worktree, the *main*
  repository's (via `commondir`), so all worktrees of a project share one
  code. A submodule is its own repository. Outside git: the directory name.
- "Idle" = no running agent on the code and no ledger row for N days (a
  running agent writes checkpoints, so a code with a running agent is
  never idle). "Under $X" = total spend < X.
- CLI collapse asks first only when the filters picked the codes (not for
  named codes); `-y` skips it. `--dry-run` lists and changes nothing.
- Unmerge onto a name another code has taken since gives `name~2`, as the
  legacy mint does, rather than refusing (undo should always work).
  Merges from before `merge_added` existed take back all of the code's names.
- Unmerge in the dialog is via `m` (show merged) then `u` on the row,
  rather than through previous names.
- Keys: `$` and `i` for the mark prompts, `m` toggles merged (mnemonic with
  the dialog's own key), `u` unmerge, `s` settings. `Esc` clears a filter,
  then marks, then closes.
- `s` hides the billing dialog, opens New agent defaults, and reopens the
  billing dialog on save or cancel (the zoom stays on throughout).

## Tests

- `tests/unit/test_billing_defaults.py` (23): config load/save/warnings,
  `repo_name` (repo, worktree, plain dir), the three modes at launch (flag
  wins, child inherits, bad mode warns), collapse/unmerge registry,
  `collapsible`, CLI collapse (dry run, filters + confirm, named codes
  moving live agents, refusals, configured default) and unmerge.
- `tests/unit/test_billing_collapse_tui.py` (46): the dialog's keys,
  confirms, prompts, settings line, widths in every new state at 120–260
  columns; New agent defaults rows; the new-agent Billing row per mode;
  two `make_tui` pilot tests (mark + collapse + unmerge end to end; `s`
  edits settings and returns).
- `test_namer.py`: follows later auto renames; a hand-renamed code stops;
  folder/default codes never follow.
- `test_billing_codes_tui.py`: merged codes are now rows (hidden by default).
