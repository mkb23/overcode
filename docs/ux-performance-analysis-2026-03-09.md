# Overcode UX Performance Analysis

**Date:** 2026-03-09
**Method:** 5 parallel research agents exploring the full codebase (TUI, daemon, data structures, web API, CLI)
**Context:** Power users running overcode for weeks/months notice degradation as session counts grow and data accumulates

---

## Executive Summary

Overcode does O(N) work on every tick regardless of whether anything changed. This compounds across 5 layers:

1. **TUI** -- 250ms timer captures all panes, recomputes all columns, re-renders all widgets (even unchanged ones)
2. **Daemon** -- 10s cycle does full JSON rewrite, re-parses history CSV, re-captures all panes
3. **Data files** -- CSV/JSON grow unbounded (10MB+ history, 3MB+ presence log, no pruning ever called)
4. **Web API** -- 5s poll returns full state for all agents including pane content, no deltas/ETags
5. **CLI** -- Subprocess checks and config YAML parsed from disk on every invocation

The scaling cliff hits around 20 sessions. By 50 sessions the TUI is doing 100+ context builds/sec, 2000+ ANSI regex strips/sec, and multiple unbounded file reads per tick.

The fix pattern is consistent: make everything change-driven -- only capture what changed, only render what's different, only write when values differ, and prune historical data so I/O doesn't grow with uptime.

---

## Tier 1: The Big Scaling Cliffs (Highest Impact)

These are the issues that cause the "it was fine for a week, now it's sluggish" experience.

### 1. 250ms blanket pane capture -- O(N) tmux subprocess calls
- `tui.py:462` fires every 250ms, capturing pane content for ALL sessions
- Each capture = tmux subprocess + 550 lines of ANSI output
- **At 20 sessions = 80 captures/sec. At 100 = 400/sec.**
- **Fix:** Selective capture -- only focused session + sessions whose content hash changed. Idle sessions can skip.

### 2. Column width recomputation -- O(N^2) every 250ms
- `tui.py:1206-1207` rebuilds column contexts for all widgets, computes width matrix every tick
- Column widths only actually change on resize, detail level change, or session add/remove
- **Fix:** Cache column widths. Invalidate only on structural changes (resize, session count change, detail toggle).

### 3. Unbounded data file growth -- no pruning anywhere
- `agent_status_history.csv`: 10MB+ (142K lines), parsed every 1s when baseline enabled
- `presence_log.csv`: 3.3MB+ (87K lines), never rotated
- `archive.json`: 281KB+, grows indefinitely
- `sessions.json`: `claude_session_ids` accumulates all historical IDs
- `clear_old_history()` exists but **is never called**
- **Fix:** Call `clear_old_history()` on daemon start. Add retention policies (7-day rolling for CSV, 90-day for archive). Cap `claude_session_ids` to last N entries.

### 4. Full JSON state rewrite every daemon cycle
- `monitor_daemon.py:732-761` serializes entire `monitor_daemon_state.json` every 10s
- With 50+ agents, this is 100KB+ of JSON rewritten to disk every cycle
- Multiple session updates within a cycle each trigger separate writes
- **Fix:** Batch all updates per cycle, write once. Consider append-only format or SQLite for high-agent-count deployments.

### 5. Web API returns full state on every 5s poll
- `web_api.py:61-75` captures pane content for ALL agents on every `/api/status` request
- No ETag/delta support -- 50-100KB payload every 5 seconds
- Sequential pane captures (not parallelized)
- Single-threaded `HTTPServer` -- one slow request blocks all clients
- **Fix:** Make pane_content optional (`?include_pane=true`), add ETag support, switch to `ThreadingHTTPServer`, parallelize captures.

---

## Tier 2: Death by a Thousand Cuts (Medium Impact, Easy Wins)

### 6. Widget refresh without change detection
- `tui.py:1208-1209` calls `.refresh()` on ALL widgets after every status update
- `session_summary.py:429` rebuilds entire `Text` object from scratch each time
- **Fix:** Cache render result per widget. Only rebuild if status, stats, or detail level actually changed.

### 7. ANSI stripping on every render cycle -- 2000+ regex calls/sec
- Pane content stripped via regex on capture, then re-stripped on render, then re-stripped for status detection
- At 10 sessions x 50 lines x 4/sec = 2000 regex ops/sec
- **Fix:** Strip once on capture, store plain text alongside ANSI version. Never re-strip.

### 8. History file parsed 3 times per interval
- `tui.py:960` parses for stats (every 5s)
- `daemon_status_bar.py:72` parses for baseline (every 1s)
- Timeline update parses again (every 30s)
- Same file, different call sites, no sharing
- **Fix:** Move mean_spin computation into `monitor_daemon_state.json` so TUI reads a pre-computed value instead of re-parsing CSV.

### 9. Uncompiled regex patterns in hot paths
- `status_detector.py:396-416` (`_is_shell_prompt`) compiles regex patterns on every call
- `status_detector.py:442` (`_matches_approval_patterns`) same issue
- Called for every session every 10s in daemon, every 250ms in TUI
- **Fix:** Pre-compile at module level. Trivial change, measurable improvement.

### 10. 550-line pane captures when 50 would suffice
- `status_detector.py:83` captures 550 lines but status detection only uses last ~10
- More data = more ANSI to strip, more regex to run
- **Fix:** Reduce to 100-150 lines for status detection. Only capture full content for focused pane preview.

### 11. Dependency checks subprocess on every CLI invocation
- `dependency_check.py:27-47` calls `tmux -V` subprocess every time
- `dependency_check.py:50-73` calls `claude --version` subprocess every time
- 50-100ms overhead per invocation
- **Fix:** Cache results with 30s TTL (write to temp file or env var).

### 12. Config loaded from YAML on every access
- `config.py:26-39` reads and parses YAML from disk on every `load_config()` call
- Called from 10+ sites per command invocation
- **Fix:** Cache with TTL or load once per process and invalidate on file change.

---

## Tier 3: Polish & Architectural (Lower Urgency, High Payoff Long-term)

### 13. Terminated sessions cache grows unbounded
- `tui.py:317` -- killed sessions accumulate in `_terminated_sessions` dict forever
- No TTL, no GC. After a month: hundreds of stale Session objects in memory
- **Fix:** TTL of 24 hours, or clear on TUI restart.

### 14. Session list reload every 10s regardless of changes
- `tui.py:460` loads all session JSONs from disk, sorts, filters -- even if nothing changed
- **Fix:** Check session count or modification time first. Skip if unchanged. Increase interval to 30s.

### 15. Tree metadata computed even when tree sort disabled
- `tui.py:1630` runs O(N x depth) tree computation unconditionally
- **Fix:** Gate behind `if sort_mode == "by_tree"`.

### 16. Sister polling is sequential with no timeout protection
- `sister_poller.py:59` polls sisters one at a time
- 3 unreachable sisters = 15s delay
- **Fix:** Parallelize with `ThreadPoolExecutor`. Add 3-5s timeout per sister.

### 17. Reactive attribute cascades
- `session_summary.py:54-57` has reactive watchers that fire redundant refreshes
- Toggling `emoji_free` fires 50 watchers -> 50 renders simultaneously
- **Fix:** Batch reactive changes. Set all properties, then call `refresh()` once.

### 18. Claude config read/written per hook operation
- `claude_config.py:35-50` loads JSON on every `add_hook`/`remove_hook` call
- `hooks.py:44-48` installs 5 hooks = 5 separate read-modify-write cycles
- **Fix:** Load once, batch modifications, write once.

### 19. No HTTP compression on web API
- Large JSON responses sent uncompressed
- **Fix:** Add gzip middleware. 50-70% size reduction.

### 20. No event-driven alternatives to polling
- Monitor daemon polls tmux every 10s even for idle sessions
- Hook-based detection exists but falls back to polling every 120s
- **Fix:** Use `kqueue` file watches on hook state files. Use tmux hooks for pane change events. Skip idle sessions entirely.

---

## Priority Implementation Roadmap

### Phase 1: Quick Wins (1-2 days, immediate feel)

| # | Fix | Est. Effort | Impact |
|---|-----|-------------|--------|
| 9 | Pre-compile regexes | 30 min | 5% daemon perf |
| 10 | Reduce capture lines to 150 | 30 min | 10% per capture |
| 7 | Strip ANSI once, cache result | 1 hr | 40% fewer regex ops |
| 15 | Gate tree metadata by sort mode | 30 min | Skip O(N x D) work |
| 13 | Add TTL to terminated sessions | 1 hr | Fix memory leak |

### Phase 2: Core Scaling Fixes (3-5 days, noticeable at 20+ sessions)

| # | Fix | Est. Effort | Impact |
|---|-----|-------------|--------|
| 1 | Selective pane capture | 4 hrs | 50-80% fewer captures |
| 2 | Cache column widths | 3 hrs | Eliminate O(N^2) per tick |
| 6 | Render result caching | 3 hrs | 40% fewer renders |
| 8 | Pre-compute mean_spin in daemon | 3 hrs | Eliminate 1/sec CSV parse |
| 4 | Batch state file writes | 2 hrs | 80% fewer disk writes |

### Phase 3: Data Hygiene (1-2 days, prevents degradation over weeks)

| # | Fix | Est. Effort | Impact |
|---|-----|-------------|--------|
| 3 | Call `clear_old_history()`, add retention | 2 hrs | Prevent 10MB+ CSV |
| 3 | Prune archive.json >90 days | 1 hr | Prevent unbounded growth |
| 3 | Cap `claude_session_ids` | 30 min | Fix session JSON bloat |
| 14 | Change-detect session list reload | 2 hrs | 80% fewer disk reads |

### Phase 4: Web & Network (2-3 days, multi-client scenarios)

| # | Fix | Est. Effort | Impact |
|---|-----|-------------|--------|
| 5 | Optional pane_content + ETag | 3 hrs | 90% bandwidth reduction |
| 5 | ThreadingHTTPServer | 30 min | Unblock concurrent clients |
| 16 | Parallelize sister polling | 2 hrs | 5-10x faster with multiple sisters |
| 19 | Gzip compression | 1 hr | 50-70% payload reduction |

### Phase 5: Architectural (ongoing, long-term snappiness)

| # | Fix | Est. Effort | Impact |
|---|-----|-------------|--------|
| 20 | kqueue/inotify for hook state | 4 hrs | Eliminate polling for hook users |
| 17 | Batch reactive updates | 3 hrs | Eliminate cascade renders |
| 4 | SQLite for state (optional) | 8 hrs | O(1) updates instead of full rewrite |

---

## Detailed Agent Reports

This analysis was produced by 5 specialized agents running in parallel:

- **tui-perf**: TUI render loop, widget re-renders, timer intervals (12 bottlenecks found)
- **data-bloat**: JSON state growth, session accumulation, unbounded collections (5 critical growth patterns)
- **daemon-polling**: Monitor daemon cycles, tmux captures, status detection (9 optimization opportunities)
- **web-api**: API payload sizes, polling vs push, endpoint efficiency (15 issues found)
- **cli-startup**: Import overhead, startup latency, command UX (4 bottlenecks, ~400-500ms total savings)

Line numbers reference the codebase as of 2026-03-09 and may drift as the code evolves.
