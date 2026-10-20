# Billing codes: ids with renamable names

Status: proposal, 2026-10-20. Asked for by Mike while deciding auto-naming
(#542, `auto-naming.md`): "billing GUIDs with renamable names … ASAP".

## Why

Today a billing code is a string, and the string is the key:

- `Session.billing_code` defaults to the launch name (`launcher.py:296`),
  and children and forks inherit it.
- Every ledger row carries `billing_code`. `totals.json` folds spend under
  `codes[<string>]`, and `overcode billing` groups by the string.
- Agents launched under the same name roll up into one code on purpose
  (all "shirka" runs; decided 2026-10-12).

So a code can't be renamed. `rebill` moves an agent forward to another
string, and the old spend stays under the old one. Once agents get auto
names, the codes they defaulted to would keep placeholder names
(`overcode-main2`) forever, in a ledger that is kept forever.

## Design

**A code is an entity.** It has an id and a name, and the name can change.
`~/.overcode/ledger/codes.json` holds them:

```json
{"v": 1,
 "codes": {"c_9f2e…": {"name": "shirka", "source": "human",
                         "created": "2026-10-20T10:02:11", "previous_names": ["shirka-old"],
                         "legacy": ["shirka"]}},
 "legacy": {"shirka": "c_9f2e…"}}
```

- **Ids** are `c_` plus a uuid4, minted when the code is created.
- **The name** is unique among codes. It is what people type and see.
  Renaming a code changes only this registry: every report, past and
  future, shows the new name. The old name goes in `previous_names` and
  still resolves on the command line, the way agent aliases do.
- **`source`** is `default` (created implicitly for a launch) or `human`
  (given with `--billing-code`, `billing rename`, or the TUI dialog). This
  is what auto-naming needs: a `default` code that only its own agent tree
  has used follows that agent's first auto name, if the name is free. A
  `human` code never changes by itself.
- **The registry** is written atomically, under the same lock as
  `rebill`. It is small (one entry per code, not per agent).

**Sessions and rows carry the id.**

- `Session.billing_code_id` is the key. `Session.billing_code` (the name)
  stays in sessions.json as a display copy, so an older overcode can still
  read the file.
- New ledger rows carry `code` (the id) and `billing_code` (the name when
  the row was written, for anyone grepping the files). Readers group by
  `code`.
- `totals.json` keys `codes` by id. The `v` bump rebuilds it from the
  ledger once; the fold is already incremental and restartable.

**Resolving a name at launch keeps the roll-up.** `--billing-code X`, or
the default (the launch name), looks X up among current names, then
`previous_names`, then legacy strings. If found, it reuses that id. Only an
unknown name creates a new code: `source: default` when defaulted, `human`
when given. So every "shirka" launch still bills to one code, now one with
an id. Children and forks inherit the parent's id, unchanged from today.

**Legacy rows are mapped, not rewritten.** Closed months are gzipped and
kept forever, so nothing is rewritten. On first start, a migration reads
every distinct `billing_code` string in the ledger, sessions.json and the
archive, mints one code per string with `legacy: [string]`, and fills
`registry.legacy`. A reader that finds a row without `code` maps its string
through `legacy`. So "shirka" rows from before and after the upgrade fold
into the same code. A legacy string stays mapped to its code after the
code is renamed, and a *new* code called "shirka" later gets a fresh id,
so it can't capture the old rows.

**Commands:**

| command | does |
|---|---|
| `overcode billing` | as today, grouped by id, shown by current name; `--ids` adds ids |
| `overcode billing rename OLD NEW` | renames a code (`source` becomes `human`); refuses a name another code has |
| `overcode billing merge A B` | folds code A into B: A's rows report under B, and A becomes an alias of B. Recorded in the registry, not by rewriting rows. |
| `overcode rebill <agent> <code>` | unchanged: forward-only, `close`/`open` rows, now by id |
| `overcode billing codes` | lists codes: name, id, source, agents, spend, previous names |

`merge` exists because codes that should have been one (`api` and `api-v2`)
can't be fixed by renaming once both have spend.

## What changes

- `ledger.py`: rows carry `code`; `_key`, `totals` and `report` group by id;
  a new `codes.py` holds the registry and resolver; `billing_code_of`
  returns the id, and the new `billing_name_of` returns the name.
- `session_manager.py`: `billing_code_id` on `Session`; `rebill` and
  `create_session` resolve through the registry; loaders keep reading
  sessions with only `billing_code`.
- `launcher.py`: resolution at launch and fork; inheritance by id.
- `layout_migration.py`: the legacy mint, run once, idempotent.
- `claude_bg_ledger.py`: background sessions take their agent's id.
- The TUI's billing column and dialog, `overcode list`, `overcode show`,
  and the billing-code palette action: show the name, resolve by name.
- `doctor`: flags a session whose id is missing from the registry, and
  ledger strings with no legacy mapping.

## Tests

- Migration on synthetic 0.6.0 ledger, sessions and archive fixtures:
  legacy rows and new rows fold into one code; totals match the pre-upgrade
  totals to the cent.
- Renaming a code changes every report and none of the ledger files.
- A new code reusing a renamed code's old name gets its own id and none of
  the old spend.
- Merge, then a report over a period that spans the merge.
- `test_upgrade_from_058.py` extended: 0.5.8 → 0.6.1 lands on ids.

## Timing

The ledger first ships to PyPI users in 0.6.1, since 0.6.0 was never
published. Shipping ids in 0.6.1 means no public install ever writes
string-keyed rows outside this machine, and the legacy mapping is only
ever exercised by Mike's own ledger and the 0.5.8 upgrade. This proposal
is to build it on `release/0.6.1` before the release, and re-record both
live tiers after it.

Auto-naming itself (#542) is not needed for 0.6.1 and follows after.
