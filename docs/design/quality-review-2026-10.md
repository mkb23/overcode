# Code quality review, October 2026

A review of release/0.6.0 at 7288967 (2026-10-10). It covers four areas,
each read in depth: the daemon and engine, the TUI, backends and status
detection, and the CLI with sessions, the launcher and the test suite. It
also includes measured metrics. Every item below cites file:line evidence
in the reviewers' notes. Line numbers are as of that commit.

Reproduce the numbers with `make metrics` (complexity, dead code, lint,
size) and `make coverage` (unit suite, branch and thread coverage).

## Metrics

| | |
|---|---|
| Source | 45.5k SLOC in 172 modules (70.9k lines with comments and blanks) |
| Tests | 7,272 unit tests passing in about 150 s, about 99k lines of test code |
| Coverage, unit suite | 80.5% of lines, 74.6% of branches |
| Lint (ruff) | 144 issues: 23 in src, 121 in tests, mostly unused imports |
| Types (mypy) | 1,159 errors; 556 are `attr-defined`, from the TUI action mixins |
| Complexity | 233 functions ranked C or worse (cyclomatic): 180 C, 34 D, 9 E, 10 F |
| Duplication | 35 blocks of 8 or more lines, mostly opencode vs opencode2 stats and job_manager vs session_manager |
| Dead code | About 15 confirmed (see below). Vulture's 320 at 60% confidence are mostly Click and Textual callbacks |

Coverage before this review was understated. `.coveragerc` had
`concurrency = multiprocessing` without `thread`, so nothing on a
background thread was measured: the engine socket, the watchdog, the TUI's
`@work` workers and the presence logger. With that fixed, engine_socket.py
went from 48% to 91%.

Weakest coverage among large modules:

| Module | Coverage |
|---|---|
| tui.py (2,656 stmts) | 54% |
| supervisor_daemon.py | 53% |
| cli/split.py | 53% |
| cli/skill_profiles.py | 23% |
| cli/jobs.py | 42% |
| tui_widgets/new_agent_modal.py | 58% |
| cli/monitoring.py | 64% |
| presence_logger.py | 79% |

Most complex functions:

| Function | Complexity | Lines |
|---|---|---|
| `DaemonStatusBar.render` | 98 | 327 |
| `cli/agent.list_agents` | 69 | 308 |
| `AgentLauncher.launch` | 61 | 275 |
| `SupervisorTUI.update_session_widgets` | 51 | |
| `HookStatusDetector.detect_status` | 51 | |
| `cli/doctor.doctor` | 49 | |
| `lifecycle.shutdown` | 45 | |
| `compute_status_detail` | 42 | |
| `MonitorDaemon._detect_and_enrich` | 41 | |

The files changed most often in the last 90 days are also the largest and
least maintainable:

| File | Commits | Lines | Maintainability index |
|---|---|---|---|
| tui.py | 52 | 4,781 | 0 |
| monitor_daemon.py | 43 | 2,622 | 0 |
| summary_columns.py | 24 | 2,221 | 0 |
| session_manager.py | 22 | 2,047 | 0 |

## 1. Fix now: real defects and risks

These are mostly small (S/M), and each one is a bug or an exposure.

1. **`sessions.json` can be lost or rolled back.**
   - What happens:
     - `_state_transaction` truncates and rewrites in place (`session_manager.py:844-901`). A crash between truncate and fsync leaves an empty file, which parses as `{}`, and the next commit writes an empty fleet.
     - The `.bak` is only refreshed on Windows. The live backup is dated 2026-03-11, and corruption recovery copies it over the live file with no lock.
     - The locks block with no timeout.
   - Fix: one `json_store.py` used by both session_manager and job_manager (job_manager duplicates the same 110 lines). It should:
     - lock with a separate lock file and a deadline;
     - write a temp file, fsync it and `os.replace` it, then fsync the directory;
     - keep the backup current;
     - on corruption, move the bad file aside and never overwrite it.
2. **The sister API is open to any web page.**
   - `Access-Control-Allow-Origin: *` covers GET and control preflight, so any page can read `/api/status`, including pane content. With `allow_control` and no key, a page can also send keys to agents.
   - The guard requiring a key off localhost is only in `web_server.run_server`, which nothing calls.
   - Key comparison uses `!=`, not a constant-time compare.
   - Fix: move the guard into the runner, drop the wildcard, require a key whenever control is on, and compare with `hmac.compare_digest`.
3. **A failing state-file write freezes every view's status with no warning.**
   - `_publish_state` saves before it publishes (`monitor_daemon.py:1411/1416`).
   - `_tick_failed` re-raises on the same save.
   - The loop keeps beating, so neither the watchdog nor the views see a problem.
   - Fix: publish to the engine first, wrap the file and history writes, and publish a `state_file_error`.
4. **The PID file is trusted over the lock.**
   - When the flock is held, the PID file's live PID still blocks a start (`pid_utils.py:120-133`).
   - The watchdog's `os._exit` leaves stale PID files.
   - After a PID is reused, the TUI won't start a daemon, and `stop_process` would SIGKILL an unrelated process.
   - Fix: the lock is the only source of truth.
5. **The idle cross-check (added 2026-10-10) depends on the exact marker text.**
   - If a Claude Code release rewords "esc to interrupt", every working Claude agent turns red after 15 s.
   - Fix: only downgrade if the marker was seen since the last `UserPromptSubmit`, and warn in doctor when it never is.
6. **Non-Claude stats readers trust another agent's hook ids.**
   - This is the wrong-session model/effort bug, fixed for Claude in 13dc539, still open for codex, hermes, opencode and opencode2. Each re-implements the hook-id lookup without the ownership filter, then falls back to "newest row in this directory".
   - Fix: share `_own_hook_ids` and an owned-ids-aware fallback.
7. **Polling detector: an idle Claude with "thinking" or "fetching" in its last message reads as running.** Phases 9 and 11 run before the prompt check (`status_detector.py:217-237`).
8. **A permission prompt is red in polling mode and orange in hooks mode** (`status_detector.py:293-302`). Tests pin the wrong colour.
9. **The TUI still decides lifecycle and writes it.**
   - Every 10 s, `launcher.list_sessions()` marks agents terminated, done or oversight, and moves budget between parent and child with `reclaim_budget`, from a TUI worker. The daemon does the same, so there are two writers. `overcode list` and `lifecycle` also write through it.
   - Fix: make it read-only and do these in the daemon tick. That also lets a new agent appear within one TUI tick, not 10 s.
10. **tmux hangs outside the daemon.**
    - `TmuxManager`'s libtmux path is unbounded (launcher, TUI kill/revive, supervisor, web control).
    - `cli/split._tmux` has no timeout, so the recovery tool can hang.
    - `web_control_api` calls bare `tmux` and ignores the socket.
    - libtmux is monkeypatched with no upper version pin.
11. **The supervisor daemon dies on any exception** (`supervisor_daemon.py:790-860`). Wrap it the way the monitor's `_run_tick` is wrapped.
12. **Saving `config.yaml` deletes the user's comments.**
    - `yaml.dump` with a non-atomic write. The cached dict is shared and mutated by callers.
    - Fix: a comment-preserving round trip, or put app-written settings in a separate file. Write atomically.
13. **A grok stale-turn race.**
    - The `promptId` check runs before the lock.
    - grok's `idle_prompt` becomes an unconditional Stop. Route it through `_settle_idle_prompt` as Claude's is.
14. **Published values that don't hold still.**
    - A foreground sleep's `eta_at` moves forward every tick (one delta per tick, and the countdown never counts).
    - Quick ticks publish a parent's `subtree_cost_usd = 0`.
    - The quiet-fleet test can't see it, because `ScriptedDetector` has no `get_status_detail`.
15. **The TUI applies some engine snapshots twice.** `_engine_pending` is never cleared (`tui.py:1001-1010`).
16. **Job retention is never applied.** `cleanup_completed` has no callers, so `jobs.json` grows forever.
17. **Unknown models cost $0**, so budgets never trip for them. Surface "unpriced" in doctor and on the row.
18. **Build and CI traps:**
    - `make clean-sessions`, labelled "(safe)", runs `pkill -9 claude`, which kills the whole fleet.
    - `make test-unit` (`-m unit`) runs 863 of 7,424 tests.
    - CI's `--cov-fail-under=10` gates nothing.
    - `make lint` runs mypy, which has 1,159 errors.
    - There is no default per-test timeout, although pytest-timeout is installed.
    - The `tui-eye` console script imports dev-only `pyte` and `PIL`, so it fails on a normal install.

## 2. CPU without less freshness

19. **The TUI's 250 ms row clock rebuilds every row.**
    - 89% of rebuilds produce identical text.
    - Headless, 20 idle agents: 2.7% CPU, and 0.9% with this loop off. At 40 agents: 3.9% vs 1.8%.
    - Fix: mark the time-dependent columns as `ticks`, cache the other cells per input version, and rebuild only the ticking cells each tick. Also: work out visible columns once per config change, don't call `shutil.get_terminal_size` per row, and don't render a changed row three times.
    - Guard it with a test that compares the cached path to a full render after every scripted delta.
20. **A Textual worker is started 4 times a second for the focused pane capture** (−0.5 points). Use one long-lived thread woken by an Event.
21. **The status bar is rebuilt on every delta.** Each rebuild builds a `MonitorDaemonState` covering every agent through `from_dict`. Rebuild only when fleet fields change.
22. **Delta application copies every agent's dict per delta.** Copy only the agents it touched and compare with `is`.
23. **The `d` panel parses the state file every second, and `render()` globs hook files on the main thread.** Read from the engine fleet instead.
24. **The state file is rewritten with fsync and `indent=2` on every tick and quick tick** (about 140 KB at 50 agents). The design doc says "at most 1/s, only on change". Write it when the delta is non-empty, plus a liveness rewrite, without the indentation.

## 3. Simplify and refactor

Order: the shared foundations first, then the large-file decompositions as
move-only commits.

25. **One status rule table** (L).
    - `HookStatusDetector.detect_status` is a 200-line chain of `if status == X: status = Y`, and `compute_status_detail` re-derives colour from its output. They disagree in places:
      - a child whose turn ended without a Stop is WAITING_OVERSIGHT but RED;
      - ERROR is magenta 🟣 in one model and red in the other;
      - BUSY_SLEEPING's colour differs between hooks and polling.
    - The phase label is overwritten at `:927`, so the logs never say why.
    - Fix: build a `TickContext` (one `now`), run an ordered list of named rules that return `Verdict(color, badges, activity)`, derive the legacy enum from the verdict, and record the winning rule as the phase. Then test the rules one by one.
26. **Split `monitor_daemon.py`.**
    - First initialise every field in `__init__`. Seven attributes exist only as `getattr` defaults, because 23 tests build daemons with `__new__`; add `MonitorDaemon.for_test(...)`.
    - Then extract: `daemon_cadence` (nine sync-interval pairs), `daemon_presence`, `daemon_housekeeping`, `daemon_stats_sync`, `daemon_process_probe` (one `ps` snapshot, not two), `engine_recorders`, `engine_wake` and `engine_view_extras`.
    - What stays in `MonitorDaemon`: `run`, the tick phases, detect-and-enrich, publishing and the watchdog.
    - The daemon also imports git and burn helpers from TUI modules; move them to `git_stats.py` and `burn.py`.
27. **Split `tui.py`.**
    - Key-bound actions stay as mixins; stateful parts become composed objects:
      - `tui_timers` (one scheduler, which the `d` panel and log panel timers also join);
      - `tui_engine_link`;
      - `tui_split` (every tmux side effect, with a null object outside the split, so the `in_split` gate lives in one place instead of 15);
      - jobs, sisters, command bar and dialogs action modules.
    - Workers that read widgets and app state off-thread should take their inputs as arguments instead.
28. **Split `session_manager.py` and `launcher.py`** after `json_store` (1).
    - session_manager: archive, git and focal-repo, and session-id advancement into their own modules. Its hierarchy helpers duplicate `SessionIndex`.
    - launcher: `LaunchError` in place of 17 `print()` calls plus `return None`. Today the TUI shows "Created agent" even when a launch failed. Also a pure `resolve_launch_settings`, and rename, lifecycle and agent I/O modules.
29. **One `paths.py`.**
    - `OVERCODE_STATE_DIR` has three meanings: the sessions dir, the parent of `sessions/`, and the parent of `jobs/`.
    - `OVERCODE_DIR` is ignored in seven places.
    - `config.yaml` has two loaders, one of which caches for the life of the process.
    - The `hook_state_{name}.json` path is built in six places.
30. **Shared code across backends.**
    - `backends/_stats_common.py` for the four copies of hook-id lookup, five `_owned_ids`, the `_iter_jsonl` helpers and so on.
    - opencode and opencode2 stats share about 30% of their lines.
    - The `StatusPatterns` subclasses exist only to widen tail sizes; make those fields.
    - `_NEVER` is defined six times.
    - An `AgentBackendBase` with defaults would replace seven `getattr` probes.
    - Each backend should normalise its own hook dialect (`--backend <name>` on the hook command), rather than `hook_handler` guessing it from the key format.
31. **Restructure `summary_columns.py`.**
    - Each column's metadata is spread over five parallel tables patched on after definition.
    - There are 28 `_plain` renderers.
    - `ColumnContext.mono()` ignores its second argument and has 111 call sites.
    - The "show this column at all" flags are computed in four places, and the TUI and `overcode list` can drift. One `FleetFlags` should replace them.
32. **Simplify the `Session` model.**
    - Stop the duplicate writes of old field names (in place since 2026-08-20), and delete the `__init__` patch and the `claude_*` aliases.
    - Move cpu, rss and sandbox data to daemon state.
    - Split out the sister-only fields.
33. **The tmux layers.**
    - `TmuxManager` becomes a facade over an injected `RealTmux`.
    - Delete the filesystem and subprocess protocols, which have no users.
    - Move `mocks.py` to `tests/`; it currently ships in the wheel.
34. **One clock.** Inject a `Clock` (wall and monotonic) into the daemon, the detectors and history. Store new timestamps timezone-aware and compare in epoch seconds. `FrozenClock` freezes only `datetime`, which caused the quiet-fleet flake.
35. **Later, with a migration:** key per-agent files by session id, not name. Most of the rename machinery exists because of the name keys.

### Confirmed dead code

- Never called anywhere: `web_server.run_server` and `config.get_jobs_retention_hours`.
- `clear_conversation_keys` is defined on all seven backends and called only by tests.
- Called only by tests:
  - `JobManager.cleanup_completed` (but see 16)
  - `worker_guard.is_in_flight`
  - `PaneCaptureGate.last_captured_at`
  - `PresenceSettings` and `TUISettings`
  - `RealFileSystem`/`RealSubprocess` and their mocks
- Daemon state fields:
  - `SessionDaemonState.activity_summary*` and `summarizer_*` have never been set since #123, so the API always serves them empty.
  - `status_detail.legacy_status` is published but never read.
  - `_stats_sync_interval` and the `sync_claude_code_stats` alias.
- `daemon_active_indicators` and `daemon_tool_indicators` in six backends; only the supervisor reads them, and only through the Claude defaults.
- Unused locals:
  - `overagent.py:299 prefer_live`
  - `summary_columns.py:265 simple`
  - `tui.py:4592 parameters`
  - `tui_logic.py:800 any_has_oversight`
- Stale text:
  - the `--diagnostics` message says "press r to refresh", but `r` is now resize;
  - the docstring at the top of `hook_status_detector.py` says hooks are "trusted indefinitely";
  - rows 101, 102 and 110 of `opencode-parity.md` are out of date.

## 4. Tests

36. **A shared pilot harness.**
    - The `tui` fixture is copied across 11 files. It starts every real timer and has no engine, so after 1.5 s the "engine not running" banner shifts every row. That is the cause of the `test_dialog_style` and `test_command_palette` flakes.
    - Fix: `make_tui(sessions, engine=FakeConnectedEngine(...), timers=False)`. The 5.7 s palette test then calls `render()` per item instead of pressing keys.
37. **Tests that count work, not time.**
    - For example: "a quiet fleet over N ticks does at most K row rebuilds", and "one delta causes one apply and no status-bar rebuild".
    - `test_a_quiet_engine_repaints_nothing` only checks `seq`.
    - `ScriptedDetector` should implement `get_status_detail` and `get_last_hook_at`, so the episode and bell paths run in harness ticks.
38. **Less mocking.**
    - `test_cli.py` has 331 `patch()` calls, 61 of which replace the whole `SessionManager`. Every run is now isolated, so use a real `SessionManager` on tmp state.
    - `test_tui_actions_session.py` has 131 MagicMocks, and its sessions are mocks, so typos pass.
    - `test_tmux_manager.py` pins libtmux calls.
    - `test_interfaces.py` tests the test doubles.
39. **Speed.**
    - About 19 rename/restart tests each sleep 0.5 s regardless of `graceful_exit_wait=0`; inject the sleep.
    - The engine-socket liveness tests rely on real 0.3–0.5 s thresholds and would flake under CI load; step `_run` with an injected clock.
    - Set `timeout = 60` in `pytest.ini`.
40. **Coverage targets.** Add replay scenarios for codex, grok and hermes. Today only Claude and opencode are replayed. Cover the polling idle-with-prose case, phase labels, PID reuse, a disk-full publish and the per-agent heartbeat guard.

## Suggested order

1. **Safety (fix now):**
   - Items 1 to 4 and 18 (`json_store`, the sister API, publish ordering, the PID lock, build and CI traps).
   - Item 5 (guard on the marker text).
2. **Correctness of what's shown:**
   - Items 6 to 9, 14 and 15. Item 9 also makes new agents appear faster.
3. **CPU:**
   - Item 19 (the largest single saving), then 20 to 24.
4. **Foundations:**
   - Item 36 (the harness), item 29 (`paths.py`), item 25 (the rule table), item 30 (shared backend code), item 34 (one clock).
5. **Decompositions as move-only commits:**
   - Item 26 (monitor_daemon), item 27 (tui), item 28 (session_manager and launcher), item 31 (summary_columns).

## Progress

| Item | Status | Commit |
|---|---|---|
| 1 sessions.json can be lost | done: `json_store` (atomic replace, lock deadline, live backup, corrupt file kept aside) | 3d097a3 |
| 2 sister API open to web pages | done: no CORS, control needs a key, bind guard on the live path | f0b5742 |
| 3 failed save froze views | done: publish always, `state_file_error` shown | c028790 |
| 4 PID file over the lock | done: the lock decides; stale PIDs never signalled | c96a994 |
| 5 idle check vs reworded marker | done: trusted only once the marker has been seen | ccd8a32 |
| 6 non-Claude wrong-session ids | done: shared ownership-aware lookup, `_stats_common` | b13d90c |
| 7 polling: prose read as running | done: idle prompt first (P8b) | 62bc219 |
| 8 polling: permission red | done: orange; hermes tip and answered-dialog false matches fixed | 62bc219, 971e37b |
| 9 TUI writes lifecycle | done: `list_sessions` read-only, daemon is the single writer, new rows within a tick | 54e734b |
| 10 tmux hangs outside the daemon | done: `overcode tmux`, the resize API, TmuxManager, `attach_bare` and every default client bounded | 5b8b332, c353daa |
| 11 supervisor dies on an exception | done: each pass isolated and logged | (see log) |
| 12 config.yaml loses comments | done: only changed entries rewritten, atomic, copies from the cache | 918ef10 |
| 13 grok stale-turn race | done: checked under the lock; idle_prompt guarded | (see log) |
| 14 values that don't hold still | done: absolute badge `eta_at`, subtree cost over the fleet, quiet-fleet flake | b385d82 |
| 15 snapshot applied twice | done | b385d82 |
| 16 job retention never applied | done: hourly prune in the daemon | (see log) |
| 17 unpriced models | done: doctor flags a budget on an unpriced model | 0777ba5 |
| 18 build and CI traps | done: CI gates, `test-unit`, safe `clean-sessions`, unit timeouts, `tui-eye` | 3d9d7a5, baa7e4b |
| 19 row clock rebuilds every row | done: cached non-ticking cells; 40 idle agents 4.2% → 2.3% CPU | (see log) |
| 20 focused capture: a worker per tick | done: one long-lived capture thread | (see log) |
| 21 status bar rebuilt every delta | done: rebuilt on its inputs only; `refresh_if_changed` | (see log) |
| 22 delta copies every agent | done: copy on write, identity check in the TUI | 3ebd582 |
| 23 `d` panel polls the state file | done: reads the engine fleet; `detection_mode` published; log followed by size | (see log) |
| 24 state file every tick | done: on change plus a 10 s liveness rewrite, compact | 3ebd582 |
| 25 one status rule table | done: `TickContext` → named rules → `Verdict`; legacy enum from one table; phase names the rule; one `now` (part of 34). A scheduled-only agent stays waiting (c4f48a3) | (see log) |
| 26 split monitor_daemon | done: 2,754 → 1,875 lines; cadence, presence, housekeeping, stats sync, process probe (one `ps`), recorders, wake, `git_stats`/`burn`; `for_test()` replaces `__new__` | (see log) |
| 27 split tui.py | done: 4,921 → 2,578 lines; `tui_timers` (one scheduler), `tui_engine_link`, `tui_split` (every tmux side effect; a null one outside the split), action groups; workers take their inputs | (see log) |
| 28 split session_manager / launcher | done: 1,790 → 1,052 and 1,585 → 887 lines; `LaunchError` (a refused launch exits 1; the TUI shows the reason); `resolve_launch_settings` | (see log) |
| 29 paths.py | done: one module builds every path; per-agent file names from one table (`paths.agent_file`); `OVERCODE_DIR` / `OVERCODE_STATE_DIR` honoured everywhere, opencode plugins included; one stat-gated config parse | f2388f4 |
| 30 backend sharing | done: `StatusPatterns` one NEVER and pane windows as fields; `AgentBackendBase` defaults instead of getattr probes; hook dialects (each hook command names its backend); unmapped events to the log only; opencode v1 plugin on the shared core | d872562, 9079c56, 7c6bae1, ba64634, dde2f95 |
| 31 summary_columns | done: metadata on each column; plain text by default; `mono()` gone; one `FleetFlags` for the TUI and `overcode list`; the `overcode.columns` package behind a facade | (see log) |
| 32 Session model | part: canonical names only on write, aliases gone; old files still load | (see log) |
| 33 tmux layers | done: TmuxManager a bounded facade over RealTmux; `MockTmux` moved to tests; a libtmux pin-safety test; `RealTmux.resize_window` fixed (it called a method that never existed) | (see log) |
| 34 one clock | done: `overcode.clock` (wall, epoch, monotonic); injected into the daemon, hook detectors, housekeeping, stats sync, recorders and wake loop; new stamps timezone-aware, rules compare epoch seconds, readers take naive and aware (`from_iso`); `FrozenClock` is a `Clock` the harness injects | 76e19ce, 1e7d49b |
| 35 files by session id | done: per-agent `agents/<id>/` from launch to archive, packed into day bundles write-ahead, an orphan sweep, a slim monthly archive, billing codes and a spend ledger, the 0.5 layout migrated in batches; rename moves no files (branch item35-billing-storage) | (see log) |
| 36 shared pilot harness | done: `make_tui` (tests/tui_harness.py), no timers unless asked, an in-memory engine connected from mount; 17 files moved onto it | (see log) |
| 38 less mocking | part: `test_tui_actions_session` builds real `Session`s | (see log) |
| 39 test speed | done: launcher waits injectable (26 s → 15 s); leaked presence threads stopped (CI unit 937 s timeout → 420 s) | 37c83f3, c217e74 |
| 40 coverage targets | done: codex, grok and hermes replay scenarios (17); the polling-prose, PID-reuse, disk-full and phase-label tests landed with their fixes | 6132d3d and earlier |
| Dead code | done (the confirmed list) | c7c2284 |

Found while doing these: libtmux 0.55+ broke every tmux call on a fresh
install (e31452a); ruff's defaults changed under CI (4d75f85); the scale
tests' timing budgets needed a factor for CI runners (fc7598a); a socket
test only failed on Linux (9bb2a07); `sleep 1m` read as one second
(4048242).

Also: the 250 ms tick no longer queries the DOM to keep focus (dba2254); a
flaky test counted other threads' sleeps (see log).

Still open: 37 (work-counting tests beyond the row cache and status bar), and the rest of
32 (cpu/rss/sandbox into daemon state, sister-only fields) and 38
(test_cli's mocking).

### The local e2e tiers, first recordings (2026-10-12)

Running the live tiers against today's CLIs found real bugs, not only test
drift:

- Claude Code 2.1.29x hands a long paste to the model inside
  `<pasted_content>`, which it is told not to obey: a pasted launch prompt
  was looked at, not acted on. The launch prompt now goes on the command
  line (e39fd58) and a multi-line `overcode send` is typed (06ad288).
- Under bypass permissions an AskUserQuestion fires only PreToolUse, and
  read green while the agent waited on a person (024b076).
- opencode 1.18's "Update Available" modal ate every key sent to an agent;
  agents run with autoupdate off (see log).
- Claude 2.1.296 quits on Enter over the trust dialog's preselected "No,
  exit"; codex 0.156 reworded its trust dialog and draws its input line
  before it, and drops a paste for a few seconds after (2a1709e, 4b3a3aa).

### Scale: 15K sessions a quarter (2026-10-12)

A heavy fan-out user runs about 15K sessions a quarter. Fixed now: "auto"
detection no longer lists every hook file each tick, finished agents cost
the per-tick syncs and history nothing (661bc9c), and children that stopped
without a report are archived once their window is gone (fa4cdaa). Then,
with item 35: per-agent directories by session id for live agents only,
finished agents packed into compressed day bundles kept for good (the
owner's call: permanent retention), a slim monthly archive, and a spend
ledger by billing code. The scale tier (`tests/scale/test_storage_budgets.py`)
holds a synthetic year (60,000 agents) to the live fleet's size on the
hot paths. Still undecided: a max-age cap for children left waiting for
oversight with their window open.

