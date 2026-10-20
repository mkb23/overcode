# billing-code-ids: billing codes as ids with renamable names

Implements `docs/design/billing-code-ids.md` (now "Status: built"; the
deviations are listed in its "As built" section).

## What changed

- `codes.py` (new): the registry `~/.overcode/ledger/codes.json` — ids
  `c_<uuid4>`, unique current names, `source` default|human,
  `previous_names`, `legacy` strings, `roots` (≤2), `merged_into`. Atomic
  writes under `ledger/.codes.lock`. `resolve` (current → previous →
  legacy, mint only for an unknown name), `adopt` (legacy strings),
  `rename`, `merge`, `canonical`, `name_of`, `find`, and
  `follow_agent_name(code_id, name) -> bool` for the auto-namer (unwired).
- `ledger.py`: rows carry `code` (id) + `billing_code` (name);
  `billing_code_of` → the code key (id, else legacy string),
  `billing_name_of` → name; `inherited_billing` → (id|None, name).
  totals.json v2 folds by code key; `totals()`/`report()` map keys through
  the registry and return codes by id; `Ledger.named()` re-keys by name.
- `session_manager.py`: `Session.billing_code_id`; `create_session` and
  `rebill` resolve names through the registry; codeless children inherit
  their root's id at archive time.
- `launcher.py`: root launches resolve the name (human if
  `--billing-code`, else default); children and forks take the parent's id.
- `layout_migration.py`: `mint_legacy_codes` (one-time, idempotent; ledger,
  live and archive strings) and `fill_billing_codes` now gives ids and
  refreshes stale name copies (renames/merges); the daemon runs it each
  housekeeping pass.
- `claude_bg_ledger.py`: background sessions bill to their agent's id; a
  legacy string and its id are one code (no spurious rebill on upgrade).
- CLI: `overcode billing` is now a group (`--ids`, JSON keyed by id with
  `name`), `billing rename`, `billing merge`, `billing codes`; `rebill`
  unchanged in behaviour. `overcode show` (archived) shows the current name.
- TUI: the billing dialog opens on the name and rebills by name; the
  column shows the engine's name copy (no layout change).
- doctor: `billing_findings` (agent ids missing from the registry, ledger
  strings with no legacy mapping), printed under Storage.
- Docs: configuration (Billing Codes), cli-reference, tui-guide,
  advanced-features, ledger and layout_migration docstrings.

## Tests

- `tests/unit/test_billing_codes.py` (new, 28): registry, follow_agent_name,
  launch roll-up / children / fork, rename (no ledger file changes),
  reusing a renamed code's old name, rebill by name, merge + a report over
  a period spanning it, `billing codes`/`--ids`, 0.6.0 migration (gzipped
  + open month, live/archived records, old totals.json) matching to the
  cent, doctor, claude_bg legacy-vs-id.
- `test_upgrade_from_058.py`: `check_codes` (0.5.8 → ids) in
  `check_upgraded` and its own test; second start mints nothing.
- Existing ledger tests re-keyed through `Ledger.named()`; "codeless"
  fixtures drop `billing_code_id` too.

## Left undone

- `follow_agent_name` is not wired (auto-naming is #542's).
- Live tiers (`make test-live`) not re-recorded.
