# Review: transcript archive (shirka S001, Track A), 2026-10-26

Overnight review and hardening of branch `transcript-archive` (base
4e9ea54, itself on overcode main cae594d), against shirka's
`S001_overcode_transcript_archive.md`, `S003_envelope_and_ids.md` and
`C001_concepts.md`. Nothing is pushed or merged, and the live archive and
daemon were not touched. Every check against real data was read-only, and
every real-data write went to a scratch archive under the session
scratchpad.

## Verdict

**Merge, after the re-import step below.** The design matches S001: sweep
as the guarantee, hooks that only enqueue, byte-exact zstd -19 copies,
append / rewrite versioning, a content-free manifest, 700/600, and
find/cat as a stable interface. The branch had one real correctness bug:
agent attribution. On the real data it put a randomly chosen agent on
**396 of 969** main rows. It also had gaps in `find --repo` (removed
`*-easy` copies, clones of local clones) and in permission enforcement.
All of these are fixed and tested. The live `~/.overcode/transcripts` was
produced by the old attribution code, so it should be re-imported once
(steps below).

## Commits on the branch (after 4e9ea54)

| Commit | What |
|---|---|
| 673980c | `--root` / `$OVERCODE_TRANSCRIPTS_DIR`: put the archive elsewhere (needed for safe real-data trials) |
| 513bb47 | Identity: no agent guessed from spend that many records share; the hook's own agent wins |
| a0b67e2 | `find --repo`: clones of local clones, `git_root` (root commit), removed `*-easy` copies, real paths |
| 9668f5e | The sweep keeps the tree 700/600; the daemon log names errors by type only |
| 46f129d | Tests for the S001 acceptance list |
| 7636fee | The sweep removes temp files an interrupted copy left |

## Findings

| # | Severity | Finding | Status |
|---|---|---|---|
| F1 | **High** | **Wrong agent attribution.** `Identities` treated every key of `stats.conversations` (per-conversation spend) as ownership, and the last record read won. In the real records, 13 old `overcode-main`-checkout records (from when the daemon adopted every conversation in a directory, see "wrong-session model/effort") each list the same 392 conversations. Of 1,137 claimed conversations, 1,019 are claimed by more than one record. In the real bootstrap import, 396 of 969 main rows name one of 13 agents at random, along with that agent's billing code and parent. | Fixed (513bb47). Precedence: the agent whose hook queued the file (`OVERCODE_SESSION_ID`), then the agent's own ids (`agent_session_ids`, the active id), then spend only when one record alone has it; otherwise null. On the real records, those 396 rows become null and the other 573 are unchanged. |
| F2 | Medium | **The hook's agent lost to the records.** The `agent`/`agent_id` that SessionEnd/PreCompact put in the queue was used only when the records had nothing, though it is the most direct evidence. | Fixed (513bb47), with that agent's billing code and parent looked up. |
| F3 | Medium | **`find --repo` missed removed `*-easy` copies** (C001 names them). Real case: the session in `~/Code/gamefactorybench-easy` (now gone) has no remote and was not returned for `github.com/G-Research-Innovation/gamefactorybench`. | Fixed (a0b67e2): a remote-less row whose cwd is gone and was `<checkout>-<suffix>` beside a checkout of the repo counts. This is a name heuristic (documented): a removed `~/Code/shirka-notes` that was never shirka would count too. Real check: gamefactorybench 18 → 19 rows; shirka unchanged. |
| F4 | Medium | **A local-path remote was stored as `git_remote`.** Real case: `~/Code/shirka-eval-creative`'s origin is `/Users/mike/Code/shirka-main-repos/creative`, so `git_remote` was an absolute path (C001: absolute paths must not be keys). | Fixed (a0b67e2): a local-path / `file://` remote is followed to that repo's own remote, up to 3 hops. With no upstream, the path is kept as before. |
| F5 | Low | **No root-commit fallback** (C001: repo "falls back to the root commit hash"). Clones with no remote could not be matched. | Fixed (a0b67e2): rows carry `git_root` (a new field, `v` stays 1). A remote-less row matches on root commit, and a nested repo with another root inside a checkout is excluded. Rows from before this commit have no `git_root`, so path containment still applies to them. |
| F6 | Low | **700/600 only on creation.** A directory or file loosened later (a restore, a copy, a manual chmod) stayed loose, and the manifest and lock were not re-chmodded. | Fixed (9668f5e): each sweep walks the archive and restores the modes (`modes_fixed`); the manifest and lock are `fchmod`ed on open. |
| F7 | Low | **Exception text in the daemon log.** The scheduler logged `str(e)` of any exception, so an error that quoted what it read could put transcript text in `~/.overcode` logs. No such error was found, but nothing prevented one. | Fixed (9668f5e): only `ArchiveError` / `OSError` text (paths, codes); otherwise the type alone. `Identities.load` likewise. |
| F8 | Low | **Stale temp files.** The archive thread is a daemon thread, so a daemon stopped mid-copy left `.<name>.tmp-<pid>-<tid>` (and an aborted import left `.import-<pid>/`) in the archive for ever. | Fixed (7636fee): the sweep, which holds the lock, removes them (`tidied`). |
| F9 | Low | **No way to point the archive at a scratch root.** Needed for any safe trial on the real `~/.claude`. | Fixed (673980c): `overcode archive --root DIR …` and `$OVERCODE_TRANSCRIPTS_DIR` (which also moves the daemon's archive and the hook queue). |
| F10 | Low (open) | **Import depends on tar order.** A session's month and the cwd of its cwd-less files (tool-results, file-history) come from its main transcript when that was seen first. A tar that lists `<sid>/…` before `<sid>.jsonl` would file those files by their own mtime, and tool-results would get no cwd (so `find --repo` misses them). The real bootstrap (macOS bsdtar) is fine: 2,840 of 2,840 session rows sit in their main transcript's month, and 102 of 102 tool-results have a cwd. | Open: fixing it needs a two-pass import. Low value for a one-off. |
| F11 | Low (open) | **Byte-identical copies inside one snapshot are deduplicated**, not given a row (for example, a `.superseded-*` copy equal to the live file). That breaks "row count = file count" for such a snapshot. The real bootstrap has none (3,037 = 3,037). | Open, documented. Giving such a copy a row means letting two rows share one stored file, which changes `current_rows`; not worth it tonight. |
| F12 | Info | **S001's "3,036 files"** is off by one: the bootstrap holds 3,027 regular files + 10 hard links = 3,037, plus 3,676 AppleDouble `._*` entries, which are correctly excluded. The import gives 3,037 rows. | Spec note. |
| F13 | Info | First/last timestamps come from a regex scan, not JSON parsing. Checked against a top-level JSON parse of all 1,598 live main and subagent transcripts: cwd, version and branch agree in all of them. `first_ts` differs in 658 files and `last_ts` in 13, every time by under 1 s (nested snapshot timestamps); months agree. | No change. |
| F14 | Info | Deviations from the S001 layout: tool-results are stored versioned (`tool-results/<name>.v1.<ext>.zst`, where S001 shows `<name>.zst`), plus the extra kinds `session-file`, `file-history`, `project-file` and `history`. The manifest uses S001's `git_remote`, not S003's `repo`. | Fine: readers ignore unknown fields. Flag for the shirka side. |
| F15 | Info | The first daemon sweep after the import re-hashes everything (the stat cache is empty after an import): see the trial timings below. | One-off; acceptable. |

What I checked and found sound: append vs rewrite detection (a prefix
hash on the stored version, refused when a set-aside copy shares that
version), a snapshot read of a growing file and its retry on change,
crash-safety order (atomic copy, then the manifest row, with torn last
lines skipped), queue rename-then-drain, the machine-wide `flock`, the
cap (never deletes), secrets counted and never redacted (the real archive
has 12 in 5 files, matching C001's "five transcripts"), and `cat`
choosing the live file's version.

## Tests

| | Unit suite (`python -m pytest -q tests/unit -p no:cacheprovider`) | `test_transcript_archive.py` |
|---|---|---|
| Before (4e9ea54) | 10,077 passed, 17 skipped, 0 failed (8 m 0 s) | 45 |
| After (HEAD) | 10,091 passed, 17 skipped, 0 failed (8 m 10 s) | 59 |

New tests: the archive root override; identity precedence (spend shared
by 13 records, own ids beat spend, the hook's agent wins); repo matching
(local-clone remote, removed `-easy` copy, clone with no remote, nested
repo and non-git neighbour excluded, a local path with no upstream); a
loosened tree re-privatised; 600/700 under `umask 0` across sweep, import,
drain and enqueue; failures logging no content; interrupted-write
leftovers removed; the acceptance tests below.

## S001 acceptance checklist

| Item | Status | Evidence |
|---|---|---|
| Sweep: fixture new / appended / rewritten with correct versioning | ✅ | `TestAcceptanceSweep` (new conversation, an append replaces v1, a rewritten subagent gets v2, a superseded copy gets v2, all byte-exact), plus the original `TestSweep` cases |
| Sweep twice is a no-op | ✅ | Same test: three more sweeps (one `--now`) leave the manifest bytes and every stored file's size and mtime unchanged. Real data: the trial's second sweep captured 0 (2 files deferred because they were being written) |
| Permissions 700/600 enforced | ✅ | `TestPermissions` (existing + loosened-tree repair + `umask 0`). Real scratch archive: 1,186 directories 700 and 4,120 files 600; the live archive also has 0 exceptions |
| No content in manifest or logs | ✅ | `TestNoContentOutsideTheCopies` (marker and fake keys absent from every non-`.zst` file, logs and CLI output; failure paths log types only) |
| Import: row count = snapshot file count | ✅ | Fixture: 13 files → 13 rows, and re-import adds 0. Real bootstrap into a scratch root: 3,037 files → 3,037 rows, 0 failed (S001's 3,036 is off by one, see F12) |
| SessionEnd hook < 200 ms (only enqueues) | ✅ | Two subprocess tests (best of 3), one with 50,000 undrained lines and an unreadable transcript. Measured 30–40 ms per hook process |
| find --repo shirka covers checkout + worktrees | ✅ | Fixture tests (remote, path, worktree path, removed worktree, `-easy`, clone). Real archive: 26 shirka main rows, all in `~/Code/shirka` (in the live archive; 36 after a sweep, i.e. every shirka transcript on the machine, including the `-ids` and `-traces` clones) |
| cat | ✅ | Byte-exact main, `--subagents` order, `--version`, missing session exits 1 |

## Read-only results on the real archive (`~/.overcode/transcripts`)

`overcode archive status --json` (branch code, read-only):

- 971 sessions, 3,037 files, 3,037 manifest rows, all from the one
  bootstrap import at 2026-10-25T12:51Z (197.9 s, 0 failed)
- 1,070,396,048 bytes of transcripts in 217,047,543 bytes of zst (4.9×)
- 12 key-like strings in 5 files
- never swept, 0 queued, 2,550 live files differ from the stat cache (the
  live daemon runs main's code, which has no archive, and an import does
  not fill the stat cache)

Mode check of the live tree (find): 1,161 directories, 4,013 files; 0 directories not 700, 0 files not 600, 0 temp leftovers.

`find --repo` (main rows):

| Query | Rows | Without a remote |
|---|---|---|
| `github.com/G-Research-Innovation/shirka` (also its `git@…`/path spellings) | 26 | 0 |
| `github.com/G-Research-Innovation/gamefactorybench` | 19 (18 before a0b67e2) | 1 (the removed `-easy` copy) |
| `github.com/mkb23/overcode` | 515 | 21 (all removed worktrees of the checkout) |

The shirka sessions in `~/Code/shirka-ids` and `~/Code/shirka-traces`
(separate clones with the same remote) and 8 more in `~/Code/shirka` are
newer than the bootstrap. They appear after the first sweep (see the
trial). Neither of shirka's two worktrees has a Claude session on this
machine yet, so the worktree half of that acceptance item is proven by
the fixtures only.

### Trial: bootstrap import + sweeps of the live `~/.claude` into a scratch archive

I ran a frozen copy of 7636fee with `--root <scratchpad>/real/arch`. It
read `~/.claude` and `~/.overcode` and wrote only to the scratch root.

| Step | Seconds | Seen | Captured | Appended | New versions | Unchanged | Deferred | Failed | Raw → zst bytes |
|---|---|---|---|---|---|---|---|---|---|
| `import bootstrap` | 199 | 3,037 | **3,037 rows** | – | – | 0 | – | 0 | 1,070,396,048 → 217,047,543 |
| `sweep` (first after import) | 81 | 2,551 | 93 | 1 | 1 | 2,457 | 1 | 0 | 74,061,455 → 15,302,373 |
| `sweep` again | 0.08 | 2,551 | **0** | 0 | 0 | 2,549 | 2 | 0 | no-op |
| `sweep --now` | 6.4 | 2,551 | 3 | 3 | 0 | 2,548 | 0 | 0 | appends to transcripts being written right now |
| `sweep --now` again | 1.3 | 2,551 | 2 | 2 | 0 | 2,549 | 0 | 0 | the same, live |

- The import of the real bootstrap gives the same 3,037 rows, bytes,
  zst bytes and 12 secrets as the live archive's import.
- Sweep twice is a no-op on real data. The only non-zero entries are files
  being appended to as I ran (this agent and others): deferred without
  `--now`, appended with it.
- Integrity: all 3,129 current rows' stored copies decompress to their
  manifest `sha256` and `bytes`. 983 of 986 live main transcripts equal
  their archived copy; the other 3 were being written.
- Modes: 1,186 directories all 700, 4,120 files all 600.
- After the sweeps: 988 sessions, 3,129 files, 3,135 manifest rows,
  1,157,668,705 → 234,794,309 bytes. 73 of 987 main rows name an agent
  (the old rule named one on 461 of the bootstrap's 969, 396 of them
  guesses);
  565 carry `git_root`.
- `find --repo github.com/G-Research-Innovation/shirka` (and `~/Code/shirka`):
  **36** main rows (34 in `~/Code/shirka` and its subdirectories, 1
  `shirka-ids`, 1 `shirka-traces`). That is every shirka transcript in
  `~/.claude/projects`. gamefactorybench: 19 (including `-easy`); overcode:
  518 (481 checkout, 23 worktrees, 14 `~/Code/overcode-wt`).
- `cat <shirka session>` gives the same sha256 from the live archive and
  the trial archive.

## Merge recommendation and steps for Mike

1. Read the six commits above (each is small and self-contained), merge
   `transcript-archive` into main and install it. **Don't restart the
   monitor daemons yet**: the running ones are on the old code, which has
   no archive scheduler.
2. **Re-import the live archive once.** It was written with 4e9ea54's
   attribution (F1), and the manifest is append-only, so replace it
   rather than patch it:
   ```bash
   mv ~/.overcode/transcripts ~/.overcode/transcripts.4e9ea54
   mkdir -m 700 ~/.overcode/transcripts
   cp -p ~/.overcode/transcripts.4e9ea54/bootstrap-2026-10-25.tar.zst* ~/.overcode/transcripts/
   overcode archive import ~/.overcode/transcripts/bootstrap-2026-10-25.tar.zst   # ~3.5 min, expect 3037 rows
   overcode archive sweep    # ~80 s, takes what changed since the bootstrap
   overcode archive status
   ```
   Delete `~/.overcode/transcripts.4e9ea54` once you're happy.
   (`cp`, not `mv`, so the old tree stays whole until then.)
3. Restart the monitor daemons so they run the hourly sweep and queue
   drain. Agents pick up the `PreCompact` hook at their next launch.
4. Tell shirka (S002) that rows may carry `git_root`, that tool-results
   are stored versioned and that there are extra kinds (F14), and that
   agent fields are null more often now, because they are no longer guessed.

## Left open

- F10 (import tar-order dependence) and F11 (in-snapshot duplicates get no
  row): low, documented above.
- The `*-easy` match is a name heuristic for removed directories. If
  shirka needs certainty, the authoring index can drop rows whose
  repo-relative paths don't exist in the repo.
- `status` reports `pending` and `queued` but no explicit "gaps" (S001
  lists gaps). `pending` covers files never captured.
- Old contamination in the agent records themselves (13 records' spend
  listing 392 shared conversations) is untouched. It is the "wrong-session"
  leftover, not this branch's to clean.
- `claude_dir()` duplicates `claude_bg.claude_dir()`; harmless.
