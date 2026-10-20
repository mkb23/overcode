# name-policy: auto-naming P1 (#542)

Sections 2 and 2a of `docs/design/auto-naming.md`, with Mike's change of
spec (no edit-distance check, every rule opt-in, advice is the policy).

## What changed

- `Session.name_source` (`default` / `parent` / `auto` / `human`) and
  `name_forced`, in sessions.json and the archive. `name_source.py` holds
  the constants, the marker and the legacy rule.
- Set at every entry point: CLI launch/fork (`human`, or `parent` with
  `OVERCODE_SESSION_ID`), CLI fork with no `-n` and the TUI fork prefill
  (`default`), the new-agent modal (its `auto` flag: untouched basename
  `default`, typed `human`), CLI/TUI rename (`human`). Launcher, web API
  and overagent default to `human`.
- `naming_policy.py`: model-free `check(name, ctx) -> Verdict`,
  `choose(candidates, ctx) -> name` (for the P2 namer), `NamingContext.
  from_fleet`, `normalise`, `suggest`, advice loading, `llm_check`
  (one summarizer call, 5 s, fail-open; `SummarizerClient.complete` added).
- `naming:` config (`config.get_naming_config`, warns on unknown keys);
  `~/.overcode/prompts/naming.md` loader in `summarizer_prompts.py`;
  project advice `<repo>/.overcode/naming.md`.
- CLI: `launch`/`fork` check `-n` from inside an agent (exit 3,
  `--force-name`, `--json`); `overcode name check|policy`;
  `rename --auto|--lock`; `list --json`; `~name` in `overcode list`.
- TUI: dim name for `default`, `~` for `auto` (takes a cell from the name,
  width unchanged); name hover (source, forced, previous names); Ctrl+N
  "Named by" row; palette "Let overcode name it" / "Keep this name".
- Skill text, docs (configuration Naming, cli-reference, TUI guide,
  children), design doc marked built.

## Decisions

- Legacy records: `default` only when the name is exactly the modal's
  default for the record's directory (basename, basename2..99,
  basename_new) or ends in `-fork`/`-forkN`; everything else `human`.
- Hard checks (never forced): base pattern, reserved `daemon_claude`,
  taken = any record in sessions.json with that name or alias (sister
  agents excluded). A parent relaunching an existing name now gets exit 3
  instead of the old "already exists" no-op.
- Recent archived names: `rules.recent_archived_hours` (0 = off).
- `naming.max_length` (18) is the namer's target only; the check uses
  `rules.max_length` (unset by default).
- Suggestions: mechanical repair (normalise if a pattern fails, drop
  forbidden words / repo prefix, cut at a word), then a number only for a
  collision (`wiki1` taken → `wiki2`; `auth` → `auth-2`), never to satisfy
  `require`.
- `no_repo_prefix` applies while the repo column would show: the fleet
  (with the new agent) spans more than one repo.
- `naming.md` is the namer's full template (`{advice}` placeholder); the
  advice itself is `naming.advice` + the project file. `name policy` prints
  the advice first.
- `--json` on launch/fork sends human lines to stderr (decorator, no
  re-indent of the shared launch body).
- Palette title shortened to "Let overcode name it": the doc's title is one
  cell too wide for the palette (its width test).
- No engine change: the TUI reads names from sessions.json records, as it
  already does for `name`/`previous_names`.

## Not done

- The namer itself, triggers, notices (P2+). `enabled`, `first_after`,
  `max_renames`, `cost_cap_per_day`, `provider` are parsed but unused.
- `rename()` doesn't take a `name_source`; callers set it after the rename
  (keeps clear of the restartless-rename branch). P2 should pass it into
  `rename_session` atomically.

## Tests

9306 passed, 17 skipped (full unit suite); ruff clean. New: test_naming_policy,
test_name_source, test_naming_cli, test_name_marker_tui; additions to the
layout matrix, rename modal, new-agent modal and launch-result tests.
