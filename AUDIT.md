# Overcode Code Quality Audit

**Date:** 2026-03-11
**Scope:** All `.py` files in `src/overcode/`, `src/overcode/cli/`, `src/overcode/tui_actions/`, `src/overcode/tui_widgets/`
**Issues found:** 213 (186 active, 27 deferred)

## Summary by Category

### Active — Pure Refactoring & Behaviour-Tightening (186 issues)

| Category | Count |
|----------|-------|
| DRY / Duplicated Logic | 45 |
| Extract-When-Complex (inline blocks → named functions) | 24 |
| Silent Exception Swallowing | 27 |
| Separation of Concerns | 20 |
| Long Functions / God Methods | 18 |
| Testability / Dependency Injection | 15 |
| Conditional Mapping Smell | 12 |
| Query with Side Effects | 8 |
| Business Logic in UI | 10 |
| Miscellaneous / Dead Code | 7 |

### Deferred — Algorithmic / Behavioural Changes (27 issues)

| Category | Count |
|----------|-------|
| Performance (caching, O(N²)→O(N), reduced computation) | 18 |
| DRY (fix changes timing/behaviour) | 3 |
| Memory Management (GC, unbounded growth) | 2 |
| Separation of Concerns (fix changes event loop) | 2 |
| Testability (fix changes instantiation pattern) | 1 |
| Validation (adds new error behaviour) | 1 |

---

## Top 10 Priorities

1. **`_parse_duration` duplicated 4 times** — `_shared.py`, `monitoring.py`, `command_bar.py`, `web_control_api.py` (issues #1-4)
2. **`launch()` and `launch_fork()` share ~150 lines** — `launcher.py` (issue #30)
3. **`send_keys` logic duplicated verbatim** between `implementations.py` and `tmux_manager.py` (issue #8)
4. **`detect_status` is ~180 lines** doing 6+ distinct detection phases — `status_detector.py` (issue #118)
5. **Presence state-to-name mapping in 3+ files** — `presence_logger.py`, `time_context.py`, `web_api.py` (issues #10-12)
6. **`render` method in `daemon_status_bar.py` is ~230 lines** — single method building entire bar (issue #156)
7. **`STATUS_SYMBOLS` triplicates** data from `STATUS_EMOJIS` + `STATUS_COLORS` — `status_constants.py` (issue #115)
8. **20+ bare `except Exception: pass`** scattered across web_server, web_api, summarizer, TUI modals (issues #58-80)
9. **`_update_short_summary` / `_update_context_summary` near-identical** — `summarizer_component.py` (issue #14)
10. **Modal pattern duplication** — 5 modals share identical show/hide/focus/key-handling logic (issue #178)

---

## Active Issues by File

### `src/overcode/cli/_shared.py`

**1.** `_shared.py:40-70` — `_parse_duration` is the canonical copy but has no tests and is duplicated in 3 other places. Should be the single source of truth with explicit re-exports. **[DRY]**

### `src/overcode/cli/monitoring.py`

**2.** `monitoring.py:158-178` — Inline duration parsing that duplicates `_parse_duration` from `_shared.py`. **[DRY]**

### `src/overcode/tui_widgets/command_bar.py`

**3.** `command_bar.py` — Contains its own `_parse_duration` implementation (3rd copy). Should import from `_shared.py`. **[DRY]**

### `src/overcode/web_control_api.py`

**4.** `web_control_api.py:347-357` — `_parse_frequency` is the 4th duration parser. Different name but same logic. **[DRY]**

**5.** `web_control_api.py:109-147` — `restart_agent` rebuilds the claude command inline. This command-building logic also exists in `launcher.py`. **[DRY]**

**6.** `web_control_api.py` — `transport_all` contains a ~20-line handover instruction string literal that is duplicated from `tui_actions/session.py`. **[DRY]**

**7.** `web_control_api.py` — Multiple functions create `SessionManager()` inline rather than accepting it as a parameter. Prevents testing without real file I/O. **[Testability]**

### `src/overcode/implementations.py`

**8.** `implementations.py:142-185` — `RealTmux.send_keys` is a verbatim copy of `TmuxManager.send_keys` from `tmux_manager.py:132-183`. Both have identical `!`-prefix and `/`-prefix special handling with same sleep delays. **[DRY]**

**9.** `implementations.py:266-293` — `RealTmux._attach_bare` is a near-copy of `TmuxManager._attach_bare` from `tmux_manager.py:203-243`. Differs only in lacking the `set-hook` approach for `destroy-unattached`. **[DRY]**

### `src/overcode/presence_logger.py`

**10.** `presence_logger.py:185-193` — `state_to_name` maps `{0: "asleep", 1: "locked", ...}`. Same mapping exists in `time_context.py` and `web_api.py`. **[DRY]**

**12.** `presence_logger.py` — `format_presence` at L200+ duplicates presence state name formatting from `time_context.py`. **[DRY]**

### `src/overcode/time_context.py`

**13.** `time_context.py:275-296` — `generate_time_context` loads daemon state then searches it twice (once for session, once for presence). The load + search could be a single `_load_daemon_state` + `_find_session_in_state` call pair. Also, `_load_daemon_state` duplicates path construction from settings module. **[DRY]**

### `src/overcode/summarizer_component.py`

**14.** `summarizer_component.py:199-249` — `_update_short_summary` and `_update_context_summary` are near-identical methods. They differ only in: mode string ("short" vs "context"), max_tokens (50 vs 75), which fields to update, and which timestamp dict to record. Should be a single `_update_summary(mode, max_tokens, ...)` method. **[DRY]**

**15.** `summarizer_component.py:274` — `except Exception as e:` in `_capture_pane` catches all exceptions including `KeyboardInterrupt`. Should catch specific exceptions from tmux capture. **[Silent Exception]**

### `src/overcode/summarizer_client.py`

**16.** `summarizer_client.py:169` — Bare `except Exception` in `summarize()`. No logging of what failed; silently returns None. **[Silent Exception]**

**17.** `summarizer_client.py:131` — Prompt templates use `{status}` placeholder. Verify that the `.format()` call actually passes `status` — potential silent KeyError if not. **[Miscellaneous]**

### `src/overcode/tmux_utils.py`

**18.** `tmux_utils.py:53-54` + `tmux_utils.py:121-122` — Both `send_text_to_tmux_window` and `get_tmux_pane_content` independently construct `tmux_cmd` from `OVERCODE_TMUX_SOCKET` env var. Extract to a `_build_tmux_cmd()` helper. **[DRY]**

**19.** `tmux_utils.py:79-80` — `except subprocess.SubprocessError as e: print(...)` — uses print instead of logging. Caller has no way to know what failed. **[Silent Exception]**

**20.** `tmux_utils.py:98-99` — Same print-based error reporting for Enter key failure. **[Silent Exception]**

### `src/overcode/tmux_manager.py`

**21.** `tmux_manager.py:132-183` — `send_keys` has complex special-case handling for `!` and `/` commands with timing delays. This is also duplicated in `implementations.py`. Both should delegate to a shared function. **[DRY]**

**22.** `tmux_manager.py:309-341` — `window_exists` reimplements the same fallback logic as `_get_window` (name lookup → digit index fallback). Should just call `_get_window` and check if result is None. **[DRY]**

**23.** `tmux_manager.py:203-243` — `_attach_bare` is ~40 lines of subprocess calls that could be extracted into a standalone tmux bare-attach utility. **[Extract-When-Complex]**

### `src/overcode/pid_utils.py`

**24.** `pid_utils.py` — `is_process_running` and `get_process_pid` share identical PID file read + `os.kill(pid, 0)` logic. The core "read PID file and check if process alive" should be a single helper. **[DRY]**

**25.** `pid_utils.py:266-308` — `stop_process` duplicates the SIGTERM→wait→SIGKILL escalation pattern from `daemon_utils.create_daemon_helpers`. **[DRY]**

### `src/overcode/daemon_utils.py`

**26.** `daemon_utils.py` — `create_daemon_helpers` returns closures that capture `pid_file_path`. The stop closure's SIGTERM→SIGKILL logic is duplicated by `pid_utils.stop_process`. **[DRY]**

### `src/overcode/status_constants.py`

**27.** `status_constants.py` — `STATUS_SYMBOLS` is a dict mapping status→(emoji, color). But `STATUS_EMOJIS` and `STATUS_COLORS` are separate dicts mapping status→emoji and status→color respectively. This is triple-mapping the same data. Should be a single `StatusInfo` namedtuple or dataclass. **[DRY]**

**28.** `status_constants.py` — Many trivial predicate functions like `is_green_status`, `is_waiting_status`, `is_not_running_status` each check membership in a hardcoded set. These should be frozen sets as module-level constants with a single `status in GREEN_STATUSES` pattern. **[Extract-When-Complex]**

**29.** `status_constants.py` — `emoji_or_ascii` function and `EMOJI_ASCII` dict are intermingled with status constants. These are display-layer concerns that belong in a rendering module. **[Separation of Concerns]**

### `src/overcode/launcher.py`

**30.** `launcher.py` — `launch()` and `launch_fork()` share ~150 lines of nearly copy-pasted logic for session creation, tmux window setup, prompt sending, and metadata writing. Extract shared logic into a `_prepare_and_launch()` helper. **[DRY]**

**31.** `launcher.py` — Claude command construction (building the `claude` CLI argument list) is done inline in `launch()`. Same logic is partially reimplemented in `web_control_api.py:restart_agent`. Extract to a `build_claude_command()` function. **[DRY]**

**32.** `launcher.py` — The session metadata dict construction (name, tmux_window, parent, start_time, etc.) is a large inline block. Extract to `_build_session_metadata()`. **[Extract-When-Complex]**

### `src/overcode/session_manager.py`

**34.** `session_manager.py` — File locking with `fcntl.flock` is done inline in multiple methods (`_load`, `_save`, `update_session`, etc.). Extract a context manager like `with self._locked_state():`. **[Extract-When-Complex]**

**35.** `session_manager.py` — `Session` dataclass has many optional fields with default None. The `to_dict`/`from_dict` methods could use `dataclasses.asdict` instead of manual dict construction (like `MonitorDaemonState` does). **[Extract-When-Complex]**

### `src/overcode/status_detector.py`

**36.** `status_detector.py:47-51` — Re-exports status constants as class attributes for backward compatibility. This is a layering violation; consumers should import from `status_constants` directly. **[Separation of Concerns]**

**37.** `status_detector.py` — `detect_status` is ~180 lines with 6+ distinct detection phases (shell prompt check, permission request, question detection, sleep detection, error detection, running detection). Each phase should be its own method. **[Long Function]**

**38.** `status_detector.py` — `_extract_permission_request`, `_extract_question`, `_extract_last_activity` follow similar "scan lines from bottom, match patterns, return first match" structure. Could share a `_scan_lines_for_pattern(lines, patterns)` helper. **[DRY]**

### `src/overcode/hook_status_detector.py`

**39.** `hook_status_detector.py:229-233` — Shell prompt detection patterns duplicate those from `PollingStatusDetector._is_shell_prompt` in `status_detector.py`. **[DRY]**

**40.** `hook_status_detector.py:242-286` — `_is_sleep_in_pane` and `_extract_sleep_duration_from_context` share similar pane content scanning patterns. **[DRY]**

**41.** `hook_status_detector.py:203-240` — `_detect_session_end_status` is ~37 lines doing multiple distinct things (check pane content, detect shell prompt, detect error, determine final status). **[Extract-When-Complex]**

### `src/overcode/status_patterns.py`

**42.** `status_patterns.py` — `is_status_bar_line`, `is_command_menu_line`, `is_prompt_line` each begin with `patterns = patterns or DEFAULT_PATTERNS` boilerplate. This default-argument pattern should be handled once in a decorator or base function. **[DRY]**

### `src/overcode/history_reader.py`

**44.** `history_reader.py` — `read_session_file_stats` is ~80 lines of JSONL parsing with inline try/except for each line. The JSONL-line-by-line parsing pattern could be a generator. **[Extract-When-Complex]**

**45.** `history_reader.py` — `get_session_stats` is ~120 lines doing I/O + aggregation. Should separate "find the right file" from "aggregate stats from parsed data". **[Separation of Concerns]**

**46.** `history_reader.py` — Multiple free functions delegate to `_default_history` singleton. This global singleton pattern makes testing harder. **[Testability]**

### `src/overcode/config.py`

**48.** `config.py` — Many getter functions follow identical pattern: `load_config()` → get nested key → return default. This could be a single `get_config_value(key_path, default)` function. **[DRY]**

### `src/overcode/settings.py`

**49.** `settings.py:443-486` — `TUIPreferences.load` is ~45 lines of manual dict-to-dataclass mapping. Could use `from_dict` pattern or `dataclasses.fields`-based auto-mapping (like `SessionDaemonState.from_dict`). **[Extract-When-Complex]**

**50.** `settings.py:488-520` — `TUIPreferences.save` mirrors the manual mapping in reverse. **[DRY]**

**51.** `settings.py` — `get_default_standing_instructions` exists in both `settings.py` and `config.py`. **[DRY]**

### `src/overcode/monitor_daemon.py`

**52.** `monitor_daemon.py` — Very large file (~52KB). The main daemon loop, session update logic, and metric computation should be split into separate modules. **[Long Function]**

**53.** `monitor_daemon.py` — Pane capture and ANSI stripping is done inline in the daemon loop. This is the same capture logic used by `summarizer_component._capture_pane` and `status_detector`. **[DRY]**

### `src/overcode/monitor_daemon_core.py`

**54.** `monitor_daemon_core.py` — Well-structured pure logic. No significant issues.

### `src/overcode/monitor_daemon_state.py`

**55.** `monitor_daemon_state.py:238-243` — `except BaseException: try: os.unlink(tmp_path) except OSError: pass; raise` — The outer `except BaseException` is correct (for atomic write cleanup), but the inner `except OSError: pass` silently swallows unlink failures. At minimum, log it. **[Silent Exception]**

### `src/overcode/supervisor_daemon.py`

**56.** `supervisor_daemon.py:211-263` — `is_daemon_claude_done` and `_has_daemon_claude_started` share identical tmux capture-pane subprocess logic. Extract a shared `_capture_daemon_pane()` method. **[DRY]**

**57.** `supervisor_daemon.py:357-427` — `count_interventions_from_log` has complex inline log parsing (~70 lines). Should be extracted into a standalone function with tests. **[Extract-When-Complex]**

### `src/overcode/supervisor_daemon_core.py`

**58.** `supervisor_daemon_core.py` — `should_launch_daemon_claude` appears unused (replaced by `determine_supervisor_action`). Dead code. **[Miscellaneous]**

### `src/overcode/web_server.py`

**59.** `web_server.py:114-140` — `_serve_dashboard` and `_serve_analytics_dashboard` are nearly identical (load template, set headers, write response). **[DRY]**

**60.** `web_server.py` — `_serve_chartjs` follows the same serve-static-content pattern as the dashboard methods. All three should share a `_serve_template(name, content_type)` helper. **[DRY]**

**61.** `web_server.py` — `do_GET` is a long if/elif URL routing chain. Should use a route table dict. **[Conditional Mapping]**

**63.** `web_server.py:522-523` — `_log_to_file` has bare `except Exception: pass`. Log write failures are silently lost. **[Silent Exception]**

**64.** `web_server.py:608-609` — `stop_web_server` has bare `except Exception: pass` around PID file cleanup. **[Silent Exception]**

### `src/overcode/web_server_runner.py`

**65.** `web_server_runner.py:33-34` — `except Exception: pass` in `log()` function. If logging fails, there's no fallback. **[Silent Exception]**

**66.** `web_server_runner.py:88-89` — `sys.stdout = open(os.devnull, 'w')` — Redirects stdout/stderr to devnull but never closes the file handles. Minor resource leak. **[Miscellaneous]**

**67.** `web_server_runner.py:96-103` — Error cleanup block re-imports settings functions that were already imported in the try block. The imports should be at function scope. **[Extract-When-Complex]**

### `src/overcode/web_api.py`

**68.** `web_api.py:72-75` — Nested `except Exception: pass` in `get_status_data`. Silently swallows errors during pane capture for individual sessions. **[Silent Exception]**

**69.** `web_api.py:120-129` — `get_single_agent_status` repeats the pane capture pattern from `get_status_data`. **[DRY]**

**70.** `web_api.py` — `_build_agent_info` is ~100 lines building a dict inline. Should be split into sub-functions for status info, time info, cost info, etc. **[Long Function]**

**71.** `web_api.py` — `_calculate_presence_efficiency` is ~110 lines with a complex sampling loop. The sampling algorithm should be extracted and tested independently. **[Long Function]**

**72.** `web_api.py` — `get_analytics_sessions` creates `SessionManager()` inline (not injected). **[Testability]**

**73.** `web_api.py:167` + `web_api.py:555` — State name mapping `{0: "asleep", 1: "locked", ...}` duplicated within the same file. **[DRY]**

### `src/overcode/web_control_api.py`

**74.** `web_control_api.py` — Many functions create `SessionManager()` inline rather than using dependency injection. **[Testability]**

### `src/overcode/data_export.py`

**75.** `data_export.py` — `_build_sessions_table`, `_build_timeline_table`, `_build_presence_table` share identical empty-data-check and array-building patterns. **[DRY]**

**76.** `data_export.py` — `_session_to_record` duplicates field mapping from `web_api.py:_session_to_analytics_record`. **[DRY]**

### `src/overcode/follow_mode.py`

**77.** `follow_mode.py:126-250` — `follow_agent` is ~125 lines. Should extract the status-check loop, report-polling, and output-streaming into separate functions. **[Long Function]**

**78.** `follow_mode.py` — `_poll_for_report` duplicates report checking and status updating from `follow_agent`. **[DRY]**

**79.** `follow_mode.py` — `_emit_new_lines` has complex deduplication logic that should be a standalone tested function. **[Extract-When-Complex]**

### `src/overcode/sister_poller.py`

**80.** `sister_poller.py:192-267` — `_agent_to_session` is a 75-line function building Session objects with manual field mapping. Should use a dict→Session factory. **[Long Function]**

**81.** `sister_poller.py:222` — Uses `__import__("datetime")` inline instead of a normal import. **[Miscellaneous]**

**82.** `sister_poller.py:154-160` — `_poll_sister` error handling resets 5 fields individually. Should use a `_reset_sister_state()` method. **[Extract-When-Complex]**

### `src/overcode/sister_controller.py`

**83.** `sister_controller.py` — Every method passes `(sister_url, api_key, agent_name)` as separate arguments. Should use a bound `SisterClient(url, api_key)` pattern where agent_name is per-call. **[Extract-When-Complex]**

### `src/overcode/notifier.py`

**84.** `notifier.py` — `_send_terminal_notifier` and `_send_osascript` have similar subprocess-call-with-error-handling structure. Could share a `_run_notification_cmd(cmd)` helper. **[DRY]**

### `src/overcode/usage_monitor.py`

**85.** `usage_monitor.py` — `_get_access_token` has two separate try/except blocks for keychain vs file. Could be a try-chain or loop over providers. **[Extract-When-Complex]**

**86.** `usage_monitor.py` — `_fetch_usage` catches a broad exception tuple. Should catch specific HTTP/JSON errors. **[Silent Exception]**

### `src/overcode/logging_config.py`

**87.** `logging_config.py:58-81` — Console handler setup has duplicated `StreamHandler` creation in both the rich and non-rich branches, and again in the fallback. **[DRY]**

### `src/overcode/daemon_logging.py`

**88.** `daemon_logging.py` — `SupervisorDaemonLogger.daemon_claude_output` has inline log coloring logic that mixes logging with presentation. **[Separation of Concerns]**

### `src/overcode/claude_config.py`

**89.** `claude_config.py:57-63` — `has_hook` loads settings, iterates hooks. Then `add_hook` at L71-76 does the same iteration to check existence before adding. The duplication could be avoided by having `add_hook` call `has_hook`. **[DRY]**

**90.** `claude_config.py` — `add_permission` at L143-157 and `remove_permission` at L159-178 both load settings, deepcopy, modify, save. This load-deepcopy-modify-save pattern is repeated in `add_hook`, `remove_hook`, `add_permission`, `remove_permission`. Should be a `_modify_settings(mutator_fn)` helper. **[DRY]**

### `src/overcode/hook_handler.py`

**91.** `hook_handler.py:88-94` — `except (json.JSONDecodeError, IOError): return` silently swallows malformed hook input. Should at minimum log to stderr for debugging hook issues. **[Silent Exception]**

### `src/overcode/standing_instructions.py`

**92.** `standing_instructions.py` — `load_presets` and `save_presets` both call `ensure directory exists`. Minor duplication. **[DRY]**

### `src/overcode/bundled_skills.py`

**93.** `bundled_skills.py` — Skill content is large string literals inline. For maintainability, these could be loaded from `.md` files at build time or at least stored as separate variables. **[Separation of Concerns]**

### `src/overcode/dependency_check.py`

**94.** `dependency_check.py:27-47` + `dependency_check.py:50-73` — `check_tmux` and `check_claude` are nearly identical (find executable, run version command, parse output). Should be `_check_executable(name, version_flag, parser)`. **[DRY]**

**95.** `dependency_check.py:76-91` + `dependency_check.py:94-109` — `require_tmux` and `require_claude` are near-identical wrappers. Could be `_require_executable(name, error_class, install_hint)`. **[DRY]**

---

### `src/overcode/cli/agent.py`

**96.** `agent.py` — `launch_cmd` function is ~100 lines. The argument validation, session metadata construction, and actual launch call should be separated. **[Long Function]**

**97.** `agent.py` — `kill_cmd` inline block (~15 lines) for cascade kill logic should be its own function. **[Extract-When-Complex]**

**98.** `agent.py` — `list_cmd` has inline session filtering and formatting (~30 lines). The filter logic is business logic embedded in CLI. **[Separation of Concerns]**

**99.** `agent.py` — `send_cmd` has a special-case mapping for "enter", "escape", "1"-"5" that should be a constant dict. **[Conditional Mapping]**

**100.** `agent.py` — `show_cmd` calls `rprint` with inline Rich formatting for each field. The formatting could be extracted to a `render_session_detail()` function. **[Extract-When-Complex]**

**101.** `agent.py` — `revive_cmd` has ~20 lines of inline session restart logic. **[Extract-When-Complex]**

**102.** `agent.py` — `instruct_cmd` loads presets inline and maps preset names. **[Extract-When-Complex]**

**103.** `agent.py` — `fork_cmd` has ~40 lines of inline logic for building fork context and launching. **[Extract-When-Complex]**

**104.** `agent.py` — `report_cmd` has bare `except Exception` at the end. **[Silent Exception]**

**105.** `agent.py` — `cleanup_cmd` has inline filter for done/terminated sessions. **[Extract-When-Complex]**

### `src/overcode/cli/daemon.py`

**106.** `daemon.py` — `monitor_cmd` and `supervisor_cmd` share nearly identical structure (check running, start/stop, print status). Could share a `_daemon_control(daemon_type, ...)` helper. **[DRY]**

**107.** `daemon.py` — Both commands import `spawn_daemon` and `is_*_running` inline. The start/stop/status pattern is repeated. **[DRY]**

### `src/overcode/cli/hooks.py`

**108.** `hooks.py` — `install_cmd` and `uninstall_cmd` share the same ClaudeConfigEditor load/iterate/display pattern. **[DRY]**

**109.** `hooks.py` — Error display uses inline `rprint` with Rich markup. **[Extract-When-Complex]**

### `src/overcode/cli/perms.py`

**110.** `perms.py` — Nearly identical structure to `hooks.py` (install/uninstall with ClaudeConfigEditor). The shared pattern should be extracted. **[DRY]**

### `src/overcode/cli/skills.py`

**111.** `skills.py` — `install_cmd` and `uninstall_cmd` share the pattern of iterating skills, checking existence, and printing status. **[DRY]**

### `src/overcode/cli/sister.py`

**112.** `sister.py` — API key masking logic (`key[:4] + "..." + key[-4:]`) is duplicated from other display code. **[DRY]**

### `src/overcode/cli/config.py`

**113.** `config.py` — Manual config formatting with inline `rprint` calls. The formatting should be a separate render function. **[Separation of Concerns]**

### `src/overcode/cli/budget.py`

**114.** `budget.py` — Inline subtree spend computation (~15 lines). This business logic should be in `session_manager` or a budget module. **[Separation of Concerns]**

---

### `src/overcode/status_constants.py` (continued)

**115.** `status_constants.py` — `STATUS_SYMBOLS`, `STATUS_EMOJIS`, and `STATUS_COLORS` are three parallel dicts mapping the same keys. Merge into a single source. **[DRY]**

**116.** `status_constants.py` — Predicate functions (`is_green_status`, `is_waiting_status`, etc.) could be replaced by exported frozensets (`GREEN_STATUSES = frozenset({...})`). The functions add indirection without value. **[Extract-When-Complex]**

**117.** `status_constants.py` — `DEFAULT_CAPTURE_LINES` is a magic number used by both status detection and summarization. Document why 50 is the right value. **[Miscellaneous]**

### `src/overcode/status_detector.py` (continued)

**118.** `status_detector.py` — `detect_status` is a single ~180-line method. Should be decomposed into `_detect_terminated`, `_detect_permission_request`, `_detect_question`, `_detect_sleep`, `_detect_error`, `_detect_running`. **[Long Function]**

**119.** `status_detector.py` — `_is_shell_prompt` has hardcoded prompt patterns. These overlap with patterns in `hook_status_detector.py` and `status_patterns.py`. **[DRY]**

**120.** `status_detector.py` — ANSI stripping is done inline with `strip_ansi(line).strip()`. The `strip_ansi` import is used in many files; the pattern is correct per MEMORY.md but the stripping-then-checking could be a single `clean_line(line)` utility. **[Extract-When-Complex]**

---

### `src/overcode/tui.py`

**121.** `tui.py` — Very large file (~1800+ lines). The TUI app class mixes composition, rendering, data loading, and timer management. Should be further decomposed into action mixins or helper modules. **[Long Function]**

### `src/overcode/tui_logic.py`

**131.** `tui_logic.py` — Duplicated parent→children map construction. This mapping is also built in `session_manager.get_descendants`. **[DRY]**

**132.** `tui_logic.py` — Sort functions are pure business logic but live in a TUI module. Should be in a shared module so CLI `list` command can reuse them. **[Separation of Concerns]**

### `src/overcode/tui_helpers.py`

**133.** `tui_helpers.py` — Many functions are thin wrappers around `status_constants` functions. The wrappers add no logic. Consider re-exporting or direct import instead. **[DRY]**

**134.** `tui_helpers.py` — `format_duration` is yet another duration formatting function (different from `_parse_duration` — this formats seconds→string). Its logic is duplicated in `presence_logger` and `time_context`. **[DRY]**

### `src/overcode/tui_render.py`

**135.** `tui_render.py` — `render_session_summary_line` is ~125 lines with many parameters. This has been partially superseded by the declarative `summary_columns.py` system but still exists. **[Long Function]**

**136.** `tui_render.py` — `render_presence_indicator` duplicates `state_icons`/`state_colors` maps from `presence_logger` and `web_api`. **[DRY]**

---

### `src/overcode/tui_actions/daemon.py`

**138.** `daemon.py:61-64` — `except NoMatches: pass` — Silently swallows the case where daemon panel doesn't exist. Should at minimum log. **[Silent Exception]**

**139.** `daemon.py:96-100` — Same `except NoMatches: pass` pattern repeated. **[Silent Exception]**

**140.** `daemon.py:153-156` — Same `except NoMatches: pass` pattern. **[Silent Exception]**

**141.** `daemon.py:179-183` — Same pattern again. Four occurrences of the same try/except/pass block. Extract a `_log_to_daemon_panel(message)` helper that handles the NoMatches case. **[DRY]**

**142.** `daemon.py:199-202` — Same pattern, 5th occurrence. **[DRY]**

**143.** `daemon.py:205-208` — Same pattern, 6th occurrence. **[DRY]**

### `src/overcode/tui_actions/view.py`

**144.** `view.py` — Multiple toggle actions follow identical pattern: query widget, toggle display property, save prefs, notify. Should have a `_toggle_widget(widget_id, widget_class, pref_key)` helper. **[DRY]**

**145.** `view.py` — Repetitive `try: query_one(...) except NoMatches: pass` pattern. **[DRY]**

### `src/overcode/tui_actions/session.py`

**146.** `session.py` — Repetitive focused-widget-check pattern: `focused = self.focused; if not isinstance(focused, SessionSummary): return`. This guard appears in nearly every action. Extract a `_get_focused_session()` helper. **[DRY]**

**147.** `session.py` — Handover instruction string literal (~20 lines) is duplicated in `web_control_api.py`. **[DRY]**

**148.** `session.py` — `action_new_agent` has ~40 lines of inline agent creation logic. **[Extract-When-Complex]**

**149.** `session.py` — `action_kill_agent` has inline cascade kill logic that duplicates `cli/agent.py:kill_cmd`. **[DRY]**

**150.** `session.py` — `action_restart_agent` rebuilds claude command inline. Same logic as `launcher.py` and `web_control_api.py`. **[DRY]**

### `src/overcode/tui_actions/input.py`

**151.** `input.py` — `action_send_instruction` and `action_send_standing_order` are nearly identical methods for sending text to an agent, differing only in the prompt prefix. **[DRY]**

**152.** `input.py` — `action_send_enter`, `action_send_escape`, `action_send_number` all follow the same pattern: get focused session, call `tmux.send_keys`. Should share a `_send_to_focused(keys)` helper. **[DRY]**

### `src/overcode/tui_actions/navigation.py`

**153.** `navigation.py` — Clean implementation. No significant issues.

---

### `src/overcode/tui_widgets/command_bar.py`

**154.** `command_bar.py` — Contains a ~90-line if/elif state machine for handling different command modes. Should use a state pattern or dispatch dict. **[Conditional Mapping]**

**155.** `command_bar.py` — Contains its own `_parse_duration` (3rd copy). **[DRY]** (same as #3)

### `src/overcode/tui_widgets/daemon_status_bar.py`

**156.** `daemon_status_bar.py` — `render` method is ~230 lines building the entire status bar. Should be split into `_render_daemon_status()`, `_render_session_summary()`, `_render_presence_status()`, etc. **[Long Function]**

### `src/overcode/tui_widgets/session_summary.py`

_(No active issues — all moved to deferred)_

### `src/overcode/tui_widgets/preview_pane.py`

**161.** `preview_pane.py:76` — `except Exception: pass` silently swallows all errors during content update. **[Silent Exception]**

### `src/overcode/tui_widgets/status_timeline.py`

_(No active issues — #162 moved to deferred)_

### `src/overcode/tui_widgets/daemon_panel.py`

**163.** `daemon_panel.py` — `render()` builds Rich Text inline with repeated style patterns. Could use a template or structured builder. **[Extract-When-Complex]**

### `src/overcode/tui_widgets/fullscreen_preview.py`

**164.** `fullscreen_preview.py:89` — `except Exception: pass` silently swallows errors in content widget update. **[Silent Exception]**

**165.** `fullscreen_preview.py:113` — `except Exception: pass` in `hide()` when restoring previous focus. **[Silent Exception]**

### `src/overcode/tui_widgets/help_overlay.py`

**166.** `help_overlay.py` — Keybinding data is hardcoded as inline strings. If keybindings change, this help text must be manually updated. Consider generating from the actual binding definitions. **[Separation of Concerns]**

### `src/overcode/tui_widgets/new_agent_defaults_modal.py`

**167.** `new_agent_defaults_modal.py:104` — `except Exception: pass` in `_hide()` when restoring focus. **[Silent Exception]**

**168.** `new_agent_defaults_modal.py:123-124` + `new_agent_defaults_modal.py:128-131` — Two more `except Exception: pass` blocks in `show()`. **[Silent Exception]**

### `src/overcode/tui_widgets/agent_select_modal.py`

**169.** `agent_select_modal.py:88-89` — `except Exception: pass` in `_hide()`. **[Silent Exception]**

**170.** `agent_select_modal.py:118-119` + `agent_select_modal.py:123-125` — Two more `except Exception: pass` in `show()`. **[Silent Exception]**

### `src/overcode/tui_widgets/summary_config_modal.py`

**171.** `summary_config_modal.py:178` — `except Exception: pass` in `_update_live_summaries`. This swallows errors during live preview updates. **[Silent Exception]**

**172.** `summary_config_modal.py:252-253` — `except Exception: pass` in `_hide()`. **[Silent Exception]**

**173.** `summary_config_modal.py:283-284` + `summary_config_modal.py:290-291` — Two more `except Exception: pass` in `show()`. **[Silent Exception]**

### `src/overcode/tui_widgets/sister_selection_modal.py`

**176.** `sister_selection_modal.py:116-117` — `except Exception: pass` in `_hide()`. **[Silent Exception]**

**177.** `sister_selection_modal.py:145-146` + `sister_selection_modal.py:151-153` — Two more `except Exception: pass` in `show()`. **[Silent Exception]**

---

### Modal pattern duplication (cross-cutting)

**178.** All five modal widgets (`NewAgentDefaultsModal`, `AgentSelectModal`, `SummaryConfigModal`, `SisterSelectionModal`) share identical patterns for: `show()/hide()` with focus save/restore, `on_key` with j/k/space/enter/escape handling, `_hide()` with `remove_class("visible")` + focus restore. Extract a `ModalBase` class. **[DRY]**

**179.** All modals have the same `_previous_focus` save/restore logic with `except Exception: pass`. This is 4x duplicated code. **[DRY]**

**180.** All modals have `_app_ref: Optional[Any]` field that is only used for focus management. The Textual framework provides `self.app` already. **[Miscellaneous]**

---

### `src/overcode/summary_columns.py`

**181.** `summary_columns.py:252-258` — `render_status_symbol` does `import unicodedata` at function scope on every call. Should be a module-level import. **[Extract-When-Complex]**

**182.** `summary_columns.py:528-569` — `render_oversight_countdown` has inline time formatting with repeated if/elif for seconds/minutes/hours. This is the same pattern as `format_duration` from `tui_helpers.py`. **[DRY]**

**183.** `summary_columns.py:572-606` — `render_heartbeat` is ~35 lines computing next heartbeat time. The next-heartbeat-time computation should be a standalone function. **[Extract-When-Complex]**

**184.** `summary_columns.py:744-767` — `render_heartbeat_plain` duplicates the next-heartbeat-time computation from `render_heartbeat`. **[DRY]**

**186.** `summary_columns.py:353-363` — `render_context_usage` has duplicated fallback return for two different "no data" cases (lines 361 and 363 are identical). **[DRY]**

**187.** `summary_columns.py:913-1003` — `build_cli_context` is ~90 lines of argument mapping into `ColumnContext`. The function has 22 parameters. Should use a builder pattern or kwargs. **[Long Function]**

**188.** `summary_columns.py:934-940` — Permissiveness mode emoji mapping (`bypass→🔥, permissive→🏃, normal→👮`) is duplicated from `tui_render.py` and `cli/monitoring.py`. **[DRY]**

---

### `src/overcode/interfaces.py`

**189.** `interfaces.py` — Re-export module exists solely for backward compatibility. Add a deprecation warning or migration timeline. **[Miscellaneous]**

### `src/overcode/implementations.py` (continued)

**190.** `implementations.py` — `RealTmux` duplicates session/window lookup logic from `TmuxManager`. Two classes do the same thing with slightly different caching strategies. Should be unified. **[DRY]**

**191.** `implementations.py:134` — `if isinstance(captured, list): return '\n'.join(captured)` — This type check on every capture call suggests the API contract is unclear. **[Extract-When-Complex]**

### `src/overcode/mocks.py`

**192.** `mocks.py` — Mock implementations don't validate arguments at all. For example, `MockTmux.send_keys` accepts any session/window without checking existence (L36-37 always appends). This means tests can pass with invalid state. **[Testability]**

---

### Cross-file duplication (consolidated)

**193.** Presence state mapping `{0: "asleep", 1: "locked", 2: "idle", 3: "active", 4: "tui_active"}` appears in `presence_logger.py`, `time_context.py`, `web_api.py` (twice), and `tui_render.py`. Should be a single `PRESENCE_STATE_NAMES` dict in `status_constants.py`. **[DRY]**

**194.** Pane capture pattern (get tmux pane content, strip ANSI, filter blank lines) appears in `status_detector.py`, `hook_status_detector.py`, `summarizer_component.py`, `monitor_daemon.py`, and `web_api.py`. Should be a single `capture_clean_pane(session, window, lines)` function. **[DRY]**

**195.** File atomic write pattern (write to temp file, fsync, rename) appears in `implementations.py:RealFileSystem.write_json`, `monitor_daemon_state.py:MonitorDaemonState.save`, and `session_manager.py`. Should use a shared `atomic_write(path, content)` utility. **[DRY]**

**196.** PID file management (write PID, read PID, check if alive, kill) is spread across `pid_utils.py`, `daemon_utils.py`, and `web_server.py`. Should be consolidated into `pid_utils.py`. **[DRY]**

**197.** Session→dict mapping for JSON serialization appears in `session_manager.py:Session.to_dict`, `data_export.py:_session_to_record`, `web_api.py:_session_to_analytics_record`, `web_api.py:_build_agent_info`. Each has slightly different field selections. **[DRY]**

**198.** Claude command argument construction appears in `launcher.py:launch()`, `launcher.py:launch_fork()`, `web_control_api.py:restart_agent`, and `tui_actions/session.py:action_restart_agent`. **[DRY]**

**199.** `SessionManager()` instantiation (with default tmux_session) appears inline in `web_api.py`, `web_control_api.py`, `cli/agent.py`, `cli/budget.py`, and `follow_mode.py`. None of these accept a pre-built SessionManager. **[Testability]**

**200.** Git diff stats computation (subprocess call to `git diff --stat`) appears in at least two locations. Should be a single `get_git_diff_stats(directory)` function. **[DRY]**

---

### Additional issues

**201.** `tui_actions/daemon.py:106-142` — `action_toggle_summarizer` is ~37 lines mixing UI logic (widget updates) with business logic (client lifecycle management). The summarizer enable/disable logic should be in `SummarizerComponent`. **[Separation of Concerns]**

**202.** `tui_actions/session.py` — `action_handover_all` builds a complete handover prompt inline with status data fetching + string formatting. The prompt construction is business logic. **[Separation of Concerns]**

**203.** `web_control_api.py` — `do_POST` has a long if/elif chain for URL routing (similar to `web_server.py:do_GET`). Both should use a route table. **[Conditional Mapping]**

**205.** `summary_columns.py:56` — `_tool_emojis` does `from .status_constants import emoji_or_ascii` at function scope. Module-level import would be cleaner. **[Miscellaneous]**

**206.** `tui_widgets/command_bar.py` — The `_STATES` or state transitions are implicit in if/elif chains. An enum + transition table would be more maintainable. **[Conditional Mapping]**

**209.** `tui_widgets/session_summary.py` — The widget stores both a `Session` object and individual reactive attributes copied from it (`status`, `cost`, etc.). This dual-source-of-truth risks staleness. **[Separation of Concerns]**

**210.** `summary_columns.py:84-88` — `ColumnContext.session` and `ColumnContext.stats` are typed as `object` instead of their actual types. This loses type safety. **[Miscellaneous]**

**211.** `tui_actions/view.py` — `action_cycle_detail_level` and `action_cycle_sort_mode` have inline cycle logic (`current_index = modes.index(current); next = modes[(index+1) % len(modes)]`) that should be a `_cycle(current, options)` utility. **[DRY]**

**212.** `hook_handler.py:107-127` — The `UserPromptSubmit` handler does budget checking + time context generation. These are two unrelated responsibilities in a single if block. **[Separation of Concerns]**

---

## Notes

- Issues are numbered sequentially across all files. Numbers are stable — deferred issues retain their original numbers.
- Line numbers are approximate and may shift with code changes.
- Category tags: **[DRY]** = Don't Repeat Yourself, **[Silent Exception]** = bare except or overly broad catch, **[Extract-When-Complex]** = inline block that should be a named function, **[Long Function]** = function >40 lines doing multiple things, **[Separation of Concerns]** = mixing layers, **[Testability]** = hard to test due to inline dependencies, **[Conditional Mapping]** = if/elif chain mapping between representations, **[Miscellaneous]** = dead code, unclear types, resource leaks, etc.

---
---

# Deferred: Algorithmic / Behavioural Changes

The following 27 issues involve performance optimizations, caching, memory management, polling/timer changes, or validation additions that alter observable behaviour. They are deferred to avoid mixing structural refactoring with behavioural changes.

---

### `src/overcode/presence_logger.py`

**11.** `presence_logger.py:399-429` — `read_presence_history` reads entire CSV file into memory without caching. Contrast with `StatusHistoryFile` which has an efficient caching strategy. **[Performance — add caching]**

### `src/overcode/session_manager.py`

**33.** `session_manager.py` — `get_descendants` uses O(N^2) I/O, loading all sessions then iterating children repeatedly. Should build a parent→children index once. **[Performance — O(N^2)→O(N)]**

### `src/overcode/status_history.py`

**43.** `status_history.py` — `clear_old_history` reads entire history file into memory, filters, rewrites. For large histories this is expensive. Could use a streaming approach or periodic compaction. **[Performance — streaming]**

### `src/overcode/config.py`

**47.** `config.py` — `load_config()` is called by every `get_*` function without any caching. Each call does file I/O + YAML parsing. At minimum, cache with a TTL. **[Performance — add caching]**

### `src/overcode/web_server.py`

**62.** `web_server.py:579-585` — `start_web_server` has blocking `time.sleep` polling loop to check if server started. **[Behaviour — polling pattern]**

### `src/overcode/tui.py`

**122.** `tui.py:462` — 250ms blanket pane capture captures all widgets every cycle, O(N). Should capture only visible/changed panes. **[Performance — selective capture]**

**123.** `tui.py:1206` — Column width recomputation is O(N^2) per cycle without caching. **[Performance — O(N^2)→cached]**

**124.** `tui.py:1208` — Widget refresh cascade renders all widgets every 250ms even if unchanged. Should use dirty-tracking. **[Performance — dirty-tracking]**

**125.** `tui.py:460` — Session list reload every 10s does full JSON load + sort even if nothing changed. Should use file mtime check. **[Performance — mtime check]**

**126.** `tui.py:205-206` — ANSI stripping on every render cycle (regex strip on 2000+ lines/sec at 10 sessions). Should cache stripped output. **[Performance — caching]**

**127.** `tui.py:707` — Session cache has 1s TTL but timer fires every 250ms, so the cache is effectively unused. **[Performance — TTL fix]**

**128.** `tui.py:317` — Terminated sessions are never GC'd. Memory grows unbounded with uptime. **[Memory — GC]**

**129.** `tui.py:1004` — Sister polling timers are independent and don't scale with session count. **[Performance — polling scaling]**

**130.** `tui.py:1630` — Tree metadata recompute is ungated — runs O(N*depth) even when tree sort is disabled. **[Performance — gate computation]**

### `src/overcode/tui_actions/daemon.py`

**137.** `daemon.py:49` — `time.sleep(1.0)` blocks the Textual event loop while waiting for Monitor Daemon to start. Should use `self.set_timer(1.0, callback)` instead. **[Behaviour — event loop blocking]**

### `src/overcode/tui_widgets/daemon_status_bar.py`

**157.** `daemon_status_bar.py:72-78` — History file parsed on every 1s render cycle when `baseline_minutes > 0`. Should cache parsed data with mtime check. **[Performance — caching]**

**158.** `daemon_status_bar.py` — Duplicates history parsing also done in `tui.py:960`. Three parses per 5 seconds for the same data. **[Performance — dedup changes timing]**

### `src/overcode/tui_widgets/session_summary.py`

**159.** `session_summary.py` — `shutil.get_terminal_size()` called on every render. Should be cached or obtained once per resize event. **[Performance — caching]**

**160.** `session_summary.py:54-57` — Reactive attribute watchers fire unnecessarily when values haven't actually changed. Should compare old/new before triggering refresh. **[Performance — change detection]**

### `src/overcode/tui_widgets/status_timeline.py`

**162.** `status_timeline.py` — `shutil.get_terminal_size()` called per-access rather than cached. Same issue as session_summary.py. **[Performance — caching]**

### `src/overcode/tui_widgets/summary_config_modal.py`

**174.** `summary_config_modal.py:73` — `_col_effective` does a linear scan `next((c for c in SUMMARY_COLUMNS if c.id == col_id), None)` on every call. With N columns, this is O(N) per call. Should use a dict lookup. **[Performance — O(N)→O(1)]**

**175.** `summary_config_modal.py:133` — Same linear scan pattern in render method, called per-row per-render. **[Performance — O(N)→O(1)]**

### `src/overcode/summary_columns.py`

**185.** `summary_columns.py:626-649` — `render_status_plain` does a reverse lookup through `ALL_STATUSES` to find the status name from the symbol. This is O(N) and fragile — the context should already carry the status name. **[Performance — reverse lookup]**

### `src/overcode/web_api.py`

**204.** `web_api.py` — `get_status_data` creates a `StatusDetector` instance on every call. Should reuse or cache. **[Performance — instance reuse]**

### `src/overcode/monitor_daemon_state.py`

**207.** `monitor_daemon_state.py:200` — `update_summaries` iterates `self.sessions` twice for green/non-green counting. Could be a single pass. **[Performance — double iteration]**

### `src/overcode/web_server.py`

**208.** `web_server.py` — `OvercodeHandler.tmux_session` is a class variable mutated from outside (`web_server_runner.py:80`). This is global mutable state. **[Behaviour — initialization pattern]**

### `src/overcode/config.py`

**213.** `config.py` — YAML config loading uses `yaml.safe_load` but doesn't validate the schema. Invalid config keys are silently accepted and ignored. **[Behaviour — adds new validation]**
