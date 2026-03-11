# Overcode Code Quality Audit

**Date:** 2026-03-11
**Scope:** All `.py` files in `src/overcode/`, `src/overcode/cli/`, `src/overcode/tui_actions/`, `src/overcode/tui_widgets/`
**Issues found:** 213 (77 active, 106 completed, 27 deferred, 3 informational)

## Summary by Category

### Active — Pure Refactoring & Behaviour-Tightening (77 issues)

| Category | Count |
|----------|-------|
| DRY / Duplicated Logic | 28 |
| Extract-When-Complex (inline blocks → named functions) | 14 |
| Silent Exception Swallowing | 1 |
| Separation of Concerns | 12 |
| Long Functions / God Methods | 6 |
| Testability / Dependency Injection | 6 |
| Conditional Mapping Smell | 3 |
| Miscellaneous / Dead Code | 7 |

### Completed — Fixed in Refactoring PR (106 issues)

Fixed across 12 batches addressing duration centralization, exception handling, TUI action extraction, pid/tmux utilities, status constants, web layer, config/settings, CLI decomposition, launcher/session_manager, detectors, and miscellaneous cleanup.

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

## Top 10 Priorities (Updated)

1. **`tui.py` decomposition** — Very large file (~1800+ lines) mixing composition, rendering, data, timers (issue #121)
2. **`render` method in `daemon_status_bar.py` is ~230 lines** — single method building entire bar (issue #156)
3. **Modal pattern duplication** — 5 modals share identical show/hide/focus/key-handling logic (issue #178)
4. **`_update_short_summary` / `_update_context_summary` near-identical** — `summarizer_component.py` (issue #14)
5. **`web_control_api.py` restart/handover/DI** — (issues #5-7, #74)
6. **`monitor_daemon.py` decomposition** — Very large file (~52KB) (issues #52-53)
7. **Handover instruction string duplication** — `session.py` and `web_control_api.py` (issue #147)
8. **CLI hooks/perms/skills install/uninstall duplication** — (issues #108-114)
9. **`history_reader.py` refactoring** — JSONL parsing, separation of concerns, testability (issues #44-46)
10. **`command_bar.py` state machine** — 90-line if/elif chain (issue #154)

---

## Active Issues by File

### `src/overcode/web_control_api.py`

**5.** `web_control_api.py:109-147` — `restart_agent` rebuilds the claude command inline. This command-building logic also exists in `launcher.py`. **[DRY]**

**6.** `web_control_api.py` — `transport_all` contains a ~20-line handover instruction string literal that is duplicated from `tui_actions/session.py`. **[DRY]**

**7.** `web_control_api.py` — Multiple functions create `SessionManager()` inline rather than accepting it as a parameter. Prevents testing without real file I/O. **[Testability]**

### `src/overcode/time_context.py`

**13.** `time_context.py:275-296` — `generate_time_context` loads daemon state then searches it twice (once for session, once for presence). The load + search could be a single `_load_daemon_state` + `_find_session_in_state` call pair. Also, `_load_daemon_state` duplicates path construction from settings module. **[DRY]**

### `src/overcode/summarizer_component.py`

**14.** `summarizer_component.py:199-249` — `_update_short_summary` and `_update_context_summary` are near-identical methods. They differ only in: mode string ("short" vs "context"), max_tokens (50 vs 75), which fields to update, and which timestamp dict to record. Should be a single `_update_summary(mode, max_tokens, ...)` method. **[DRY]**

### `src/overcode/summarizer_client.py`

**17.** `summarizer_client.py:131` — Prompt templates use `{status}` placeholder. Verify that the `.format()` call actually passes `status` — potential silent KeyError if not. **[Miscellaneous]**

### `src/overcode/tmux_manager.py`

**23.** `tmux_manager.py:203-243` — `_attach_bare` is ~40 lines of subprocess calls that could be extracted into a standalone tmux bare-attach utility. _(Partially addressed via batch 12 — `attach_bare` extracted to tmux_utils, but some standalone logic may remain.)_ **[Extract-When-Complex]**

### `src/overcode/status_constants.py`

**29.** `status_constants.py` — `emoji_or_ascii` function and `EMOJI_ASCII` dict are intermingled with status constants. These are display-layer concerns that belong in a rendering module. **[Separation of Concerns]**

**117.** `status_constants.py` — `DEFAULT_CAPTURE_LINES` is a magic number used by both status detection and summarization. Document why 50 is the right value. **[Miscellaneous]**

### `src/overcode/status_patterns.py`

**42.** `status_patterns.py` — `is_status_bar_line`, `is_command_menu_line`, `is_prompt_line` each begin with `patterns = patterns or DEFAULT_PATTERNS` boilerplate. This default-argument pattern should be handled once in a decorator or base function. **[DRY]**

### `src/overcode/history_reader.py`

**44.** `history_reader.py` — `read_session_file_stats` is ~80 lines of JSONL parsing with inline try/except for each line. The JSONL-line-by-line parsing pattern could be a generator. **[Extract-When-Complex]**

**45.** `history_reader.py` — `get_session_stats` is ~120 lines doing I/O + aggregation. Should separate "find the right file" from "aggregate stats from parsed data". **[Separation of Concerns]**

**46.** `history_reader.py` — Multiple free functions delegate to `_default_history` singleton. This global singleton pattern makes testing harder. **[Testability]**

### `src/overcode/monitor_daemon.py`

**52.** `monitor_daemon.py` — Very large file (~52KB). The main daemon loop, session update logic, and metric computation should be split into separate modules. **[Long Function]**

**53.** `monitor_daemon.py` — Pane capture and ANSI stripping is done inline in the daemon loop. This is the same capture logic used by `summarizer_component._capture_pane` and `status_detector`. **[DRY]**

### `src/overcode/web_server_runner.py`

**66.** `web_server_runner.py:88-89` — `sys.stdout = open(os.devnull, 'w')` — Redirects stdout/stderr to devnull but never closes the file handles. Minor resource leak. **[Miscellaneous]**

**67.** `web_server_runner.py:96-103` — Error cleanup block re-imports settings functions that were already imported in the try block. The imports should be at function scope. **[Extract-When-Complex]**

### `src/overcode/web_api.py`

**71.** `web_api.py` — `_calculate_presence_efficiency` is ~110 lines with a complex sampling loop. The sampling algorithm should be extracted and tested independently. **[Long Function]**

**72.** `web_api.py` — `get_analytics_sessions` creates `SessionManager()` inline (not injected). **[Testability]**

### `src/overcode/web_control_api.py` (continued)

**74.** `web_control_api.py` — Many functions create `SessionManager()` inline rather than using dependency injection. **[Testability]**

### `src/overcode/sister_controller.py`

**83.** `sister_controller.py` — Every method passes `(sister_url, api_key, agent_name)` as separate arguments. Should use a bound `SisterClient(url, api_key)` pattern where agent_name is per-call. **[Extract-When-Complex]**

### `src/overcode/usage_monitor.py`

**85.** `usage_monitor.py` — `_get_access_token` has two separate try/except blocks for keychain vs file. Could be a try-chain or loop over providers. **[Extract-When-Complex]**

### `src/overcode/bundled_skills.py`

**93.** `bundled_skills.py` — Skill content is large string literals inline. For maintainability, these could be loaded from `.md` files at build time or at least stored as separate variables. **[Separation of Concerns]**

---

### `src/overcode/cli/agent.py`

**98.** `agent.py` — `list_cmd` has inline session filtering and formatting (~30 lines). The filter logic is business logic embedded in CLI. **[Separation of Concerns]**

**99.** `agent.py` — `send_cmd` has a special-case mapping for "enter", "escape", "1"-"5" that should be a constant dict. **[Conditional Mapping]**

**100.** `agent.py` — `show_cmd` calls `rprint` with inline Rich formatting for each field. The formatting could be extracted to a `render_session_detail()` function. **[Extract-When-Complex]**

**101.** `agent.py` — `revive_cmd` has ~20 lines of inline session restart logic. **[Extract-When-Complex]**

**102.** `agent.py` — `instruct_cmd` loads presets inline and maps preset names. **[Extract-When-Complex]**

**103.** `agent.py` — `fork_cmd` has ~40 lines of inline logic for building fork context and launching. **[Extract-When-Complex]**

**104.** `agent.py` — `report_cmd` has bare `except Exception` at the end. **[Silent Exception]**

**105.** `agent.py` — `cleanup_cmd` has inline filter for done/terminated sessions. **[Extract-When-Complex]**

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

### `src/overcode/tui_actions/session.py`

**147.** `session.py` — Handover instruction string literal (~20 lines) is duplicated in `web_control_api.py`. **[DRY]**

**148.** `session.py` — `action_new_agent` has ~40 lines of inline agent creation logic. **[Extract-When-Complex]**

**149.** `session.py` — `action_kill_agent` has inline cascade kill logic that duplicates `cli/agent.py:kill_cmd`. **[DRY]**

**150.** `session.py` — `action_restart_agent` rebuilds claude command inline. Same logic as `launcher.py` and `web_control_api.py`. **[DRY]**

### `src/overcode/tui_actions/input.py`

**151.** `input.py` — `action_send_instruction` and `action_send_standing_order` are nearly identical methods for sending text to an agent, differing only in the prompt prefix. **[DRY]**

---

### `src/overcode/tui_widgets/command_bar.py`

**154.** `command_bar.py` — Contains a ~90-line if/elif state machine for handling different command modes. Should use a state pattern or dispatch dict. **[Conditional Mapping]**

### `src/overcode/tui_widgets/daemon_status_bar.py`

**156.** `daemon_status_bar.py` — `render` method is ~230 lines building the entire status bar. Should be split into `_render_daemon_status()`, `_render_session_summary()`, `_render_presence_status()`, etc. **[Long Function]**

### `src/overcode/tui_widgets/daemon_panel.py`

**163.** `daemon_panel.py` — `render()` builds Rich Text inline with repeated style patterns. Could use a template or structured builder. **[Extract-When-Complex]**

### `src/overcode/tui_widgets/help_overlay.py`

**166.** `help_overlay.py` — Keybinding data is hardcoded as inline strings. If keybindings change, this help text must be manually updated. Consider generating from the actual binding definitions. **[Separation of Concerns]**

---

### Modal pattern duplication (cross-cutting)

**178.** All five modal widgets (`NewAgentDefaultsModal`, `AgentSelectModal`, `SummaryConfigModal`, `SisterSelectionModal`) share identical patterns for: `show()/hide()` with focus save/restore, `on_key` with j/k/space/enter/escape handling, `_hide()` with `remove_class("visible")` + focus restore. Extract a `ModalBase` class. **[DRY]**

**179.** All modals have the same `_previous_focus` save/restore logic with `except Exception: pass`. This is 4x duplicated code. **[DRY]**

**180.** All modals have `_app_ref: Optional[Any]` field that is only used for focus management. The Textual framework provides `self.app` already. **[Miscellaneous]**

---

### `src/overcode/summary_columns.py`

**187.** `summary_columns.py:913-1003` — `build_cli_context` is ~90 lines of argument mapping into `ColumnContext`. The function has 22 parameters. Should use a builder pattern or kwargs. **[Long Function]**

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

## Completed Issues

The following 104 issues were addressed in the refactoring PR, organized by batch.

### Batch 1 — Duration + Presence

- ~~**1.** `_shared.py:40-70` — `_parse_duration` is the canonical copy but has no tests and is duplicated in 3 other places. **[DRY]**~~ → Centralized to `src/overcode/duration.py`
- ~~**2.** `monitoring.py:158-178` — Inline duration parsing that duplicates `_parse_duration` from `_shared.py`. **[DRY]**~~ → Centralized to `src/overcode/duration.py`
- ~~**3.** `command_bar.py` — Contains its own `_parse_duration` implementation (3rd copy). **[DRY]**~~ → Centralized to `src/overcode/duration.py`
- ~~**4.** `web_control_api.py:347-357` — `_parse_frequency` is the 4th duration parser. **[DRY]**~~ → Centralized to `src/overcode/duration.py`
- ~~**10.** `presence_logger.py:185-193` — `state_to_name` maps duplicated in multiple files. **[DRY]**~~ → Centralized to `status_constants.PRESENCE_STATE_NAMES`
- ~~**12.** `presence_logger.py` — `format_presence` duplicates presence state name formatting. **[DRY]**~~ → Centralized to `status_constants.PRESENCE_STATE_NAMES`
- ~~**73.** `web_api.py:167` + `web_api.py:555` — State name mapping duplicated within the same file. **[DRY]**~~ → Centralized to `status_constants.PRESENCE_STATE_NAMES`
- ~~**193.** Presence state mapping appears in 5 files. **[DRY]**~~ → Centralized to `status_constants.PRESENCE_STATE_NAMES`

### Batch 2 — Exception Handling

- ~~**15.** `summarizer_component.py:274` — `except Exception as e:` catches all exceptions in `_capture_pane`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**16.** `summarizer_client.py:169` — Bare `except Exception` in `summarize()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**55.** `monitor_daemon_state.py:238-243` — Inner `except OSError: pass` silently swallows unlink failures. **[Silent Exception]**~~ → Tightened exception handling
- ~~**63.** `web_server.py:522-523` — `_log_to_file` has bare `except Exception: pass`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**64.** `web_server.py:608-609` — `stop_web_server` has bare `except Exception: pass`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**65.** `web_server_runner.py:33-34` — `except Exception: pass` in `log()` function. **[Silent Exception]**~~ → Tightened exception handling
- ~~**68.** `web_api.py:72-75` — Nested `except Exception: pass` in `get_status_data`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**86.** `usage_monitor.py` — `_fetch_usage` catches a broad exception tuple. **[Silent Exception]**~~ → Tightened exception handling
- ~~**91.** `hook_handler.py:88-94` — `except (json.JSONDecodeError, IOError): return` silently swallows malformed hook input. **[Silent Exception]**~~ → Tightened exception handling
- ~~**161.** `preview_pane.py:76` — `except Exception: pass` silently swallows all errors. **[Silent Exception]**~~ → Tightened exception handling
- ~~**164.** `fullscreen_preview.py:89` — `except Exception: pass` in content widget update. **[Silent Exception]**~~ → Tightened exception handling
- ~~**165.** `fullscreen_preview.py:113` — `except Exception: pass` in `hide()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**167.** `new_agent_defaults_modal.py:104` — `except Exception: pass` in `_hide()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**168.** `new_agent_defaults_modal.py:123-131` — Two more `except Exception: pass` in `show()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**169.** `agent_select_modal.py:88-89` — `except Exception: pass` in `_hide()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**170.** `agent_select_modal.py:118-125` — Two more `except Exception: pass` in `show()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**171.** `summary_config_modal.py:178` — `except Exception: pass` in `_update_live_summaries`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**172.** `summary_config_modal.py:252-253` — `except Exception: pass` in `_hide()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**173.** `summary_config_modal.py:283-291` — Two more `except Exception: pass` in `show()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**176.** `sister_selection_modal.py:116-117` — `except Exception: pass` in `_hide()`. **[Silent Exception]**~~ → Tightened exception handling
- ~~**177.** `sister_selection_modal.py:145-153` — Two more `except Exception: pass` in `show()`. **[Silent Exception]**~~ → Tightened exception handling

### Batch 3 — TUI Actions DRY

- ~~**138.** `daemon.py:61-64` — `except NoMatches: pass` silently swallows missing daemon panel. **[Silent Exception]**~~ → Extracted `_log_to_daemon_panel`
- ~~**139.** `daemon.py:96-100` — Same `except NoMatches: pass` pattern repeated. **[Silent Exception]**~~ → Extracted `_log_to_daemon_panel`
- ~~**140.** `daemon.py:153-156` — Same `except NoMatches: pass` pattern. **[Silent Exception]**~~ → Extracted `_log_to_daemon_panel`
- ~~**141.** `daemon.py:179-183` — Same pattern again (4th occurrence). **[DRY]**~~ → Extracted `_log_to_daemon_panel`
- ~~**142.** `daemon.py:199-202` — Same pattern (5th occurrence). **[DRY]**~~ → Extracted `_log_to_daemon_panel`
- ~~**143.** `daemon.py:205-208` — Same pattern (6th occurrence). **[DRY]**~~ → Extracted `_log_to_daemon_panel`
- ~~**144.** `view.py` — Multiple toggle actions follow identical pattern. **[DRY]**~~ → Extracted `_toggle_widget`
- ~~**145.** `view.py` — Repetitive `try: query_one(...) except NoMatches: pass` pattern. **[DRY]**~~ → Extracted `_toggle_widget`
- ~~**146.** `session.py` — Repetitive focused-widget-check pattern. **[DRY]**~~ → Extracted `_get_focused_session`
- ~~**152.** `input.py` — `action_send_enter`, `action_send_escape`, `action_send_number` all follow the same pattern. **[DRY]**~~ → Extracted `_send_keys_to_focused`

### Batch 4 — PID/Dependency/Tmux Utilities

- ~~**24.** `pid_utils.py` — `is_process_running` and `get_process_pid` share identical PID file read + `os.kill` logic. **[DRY]**~~ → Extracted `_read_pid_file` helper
- ~~**25.** `pid_utils.py:266-308` — `stop_process` duplicates SIGTERM→SIGKILL escalation from `daemon_utils`. **[DRY]**~~ → Unified via pid_utils delegation
- ~~**26.** `daemon_utils.py` — Stop closure's SIGTERM→SIGKILL logic duplicated by `pid_utils.stop_process`. **[DRY]**~~ → Unified via pid_utils delegation
- ~~**94.** `dependency_check.py:27-73` — `check_tmux` and `check_claude` are nearly identical. **[DRY]**~~ → Extracted `_check_executable`
- ~~**95.** `dependency_check.py:76-109` — `require_tmux` and `require_claude` are near-identical wrappers. **[DRY]**~~ → Extracted `_require_executable`
- ~~**18.** `tmux_utils.py:53-54` + `tmux_utils.py:121-122` — Both functions construct `tmux_cmd` independently. **[DRY]**~~ → Extracted `_build_tmux_cmd`
- ~~**19.** `tmux_utils.py:79-80` — `except subprocess.SubprocessError: print(...)` uses print instead of logging. **[Silent Exception]**~~ → Replaced with logging
- ~~**20.** `tmux_utils.py:98-99` — Same print-based error reporting. **[Silent Exception]**~~ → Replaced with logging

### Batch 5 — Status Constants

- ~~**27.** `status_constants.py` — `STATUS_SYMBOLS` triplicate data from `STATUS_EMOJIS` + `STATUS_COLORS`. **[DRY]**~~ → Consolidated `STATUS_SYMBOLS` as single source
- ~~**115.** `status_constants.py` — Three parallel dicts mapping the same keys. **[DRY]**~~ → Consolidated `STATUS_SYMBOLS` as single source
- ~~**28.** `status_constants.py` — Predicate functions should be frozensets. **[Extract-When-Complex]**~~ → Replaced with frozensets
- ~~**116.** `status_constants.py` — Predicate functions add indirection without value. **[Extract-When-Complex]**~~ → Replaced with frozensets
- ~~**36.** `status_detector.py:47-51` — Re-exports status constants as class attributes for backward compatibility. **[Separation of Concerns]**~~ → Removed backward-compat re-exports

### Batch 6 — Web Layer

- ~~**59.** `web_server.py:114-140` — `_serve_dashboard` and `_serve_analytics_dashboard` are nearly identical. **[DRY]**~~ → Extracted `_serve_content` + route tables
- ~~**60.** `web_server.py` — `_serve_chartjs` follows the same serve-static-content pattern. **[DRY]**~~ → Extracted `_serve_content` + route tables
- ~~**61.** `web_server.py` — `do_GET` is a long if/elif URL routing chain. **[Conditional Mapping]**~~ → Route table
- ~~**69.** `web_api.py:120-129` — `get_single_agent_status` repeats pane capture pattern. **[DRY]**~~ → Extracted `_capture_agent_pane`
- ~~**70.** `web_api.py` — `_build_agent_info` is ~100 lines building a dict inline. **[Long Function]**~~ → Decomposed `_build_agent_info`
- ~~**75.** `data_export.py` — `_build_sessions_table`, `_build_timeline_table`, `_build_presence_table` share identical patterns. **[DRY]**~~ → Extracted `_build_table`
- ~~**76.** `data_export.py` — `_session_to_record` duplicates field mapping. **[DRY]**~~ → Extracted `_build_table`
- ~~**203.** `web_control_api.py` — `do_POST` has a long if/elif chain for URL routing. **[Conditional Mapping]**~~ → Route table

### Batch 7 — Config/Settings

- ~~**89.** `claude_config.py:57-63` — `has_hook` and `add_hook` duplicate iteration. **[DRY]**~~ → Extracted `_modify_settings`
- ~~**90.** `claude_config.py` — Load-deepcopy-modify-save pattern repeated 4 times. **[DRY]**~~ → Extracted `_modify_settings`
- ~~**48.** `config.py` — Many getters follow identical `load_config()` → get nested key → return default pattern. **[DRY]**~~ → Extracted `_get_config_value`
- ~~**49.** `settings.py:443-486` — `TUIPreferences.load` is ~45 lines of manual dict-to-dataclass mapping. **[Extract-When-Complex]**~~ → Auto-mapped fields
- ~~**50.** `settings.py:488-520` — `TUIPreferences.save` mirrors the manual mapping in reverse. **[DRY]**~~ → Auto-mapped fields
- ~~**51.** `settings.py` — `get_default_standing_instructions` exists in both `settings.py` and `config.py`. **[DRY]**~~ → Deduplicated
- ~~**87.** `logging_config.py:58-81` — Duplicated `StreamHandler` creation in both branches. **[DRY]**~~ → Deduplicated StreamHandler creation

### Batch 8 — CLI

- ~~**96.** `agent.py` — `launch_cmd` is ~100 lines mixing validation, metadata, and launch. **[Long Function]**~~ → Extracted oversight policy parsing, post-launch operations, cleanup logic
- ~~**97.** `agent.py` — `kill_cmd` inline cascade kill logic. **[Extract-When-Complex]**~~ → Extracted
- ~~**106.** `daemon.py` — `monitor_cmd` and `supervisor_cmd` share nearly identical structure. **[DRY]**~~ → Extracted `_daemon_control` pattern
- ~~**107.** `daemon.py` — Both commands share repeated start/stop/status pattern. **[DRY]**~~ → Extracted `_daemon_control` pattern

### Batch 9 — Launcher/Session Manager

- ~~**30.** `launcher.py` — `launch()` and `launch_fork()` share ~150 lines of copy-pasted logic. **[DRY]**~~ → Extracted `_prepare_and_launch`
- ~~**31.** `launcher.py` — Claude command construction done inline. **[DRY]**~~ → Extracted `_build_claude_command`
- ~~**32.** `launcher.py` — Session metadata dict construction is a large inline block. **[Extract-When-Complex]**~~ → Extracted
- ~~**34.** `session_manager.py` — File locking with `fcntl.flock` done inline in multiple methods. **[Extract-When-Complex]**~~ → Extracted `_locked_state` context manager
- ~~**35.** `session_manager.py` — `to_dict`/`from_dict` could use `dataclasses.asdict`. **[Extract-When-Complex]**~~ → Simplified

### Batch 10 — Detectors

- ~~**37.** `status_detector.py` — `detect_status` is ~180 lines with 6+ detection phases. **[Long Function]**~~ → Decomposed into phase methods
- ~~**118.** `status_detector.py` — Same as #37. **[Long Function]**~~ → Decomposed into phase methods
- ~~**38.** `status_detector.py` — Multiple methods follow similar "scan lines from bottom" structure. **[DRY]**~~ → Extracted `_scan_from_bottom`
- ~~**119.** `status_detector.py` — `_is_shell_prompt` has hardcoded patterns overlapping with other files. **[DRY]**~~ → Deduplicated shell prompt patterns
- ~~**120.** `status_detector.py` — ANSI strip + check could be a single `clean_line` utility. **[Extract-When-Complex]**~~ → Extracted `clean_line`
- ~~**39.** `hook_status_detector.py:229-233` — Shell prompt detection patterns duplicate from `status_detector.py`. **[DRY]**~~ → Deduplicated shell prompt patterns
- ~~**40.** `hook_status_detector.py:242-286` — `_is_sleep_in_pane` and `_extract_sleep_duration_from_context` share similar scanning. **[DRY]**~~ → Consolidated sleep detection
- ~~**41.** `hook_status_detector.py:203-240` — `_detect_session_end_status` does multiple distinct things. **[Extract-When-Complex]**~~ → Consolidated sleep detection

### Batch 11 — Miscellaneous

- ~~**56.** `supervisor_daemon.py:211-263` — Shared tmux capture-pane subprocess logic. **[DRY]**~~ → Extracted shared tmux capture
- ~~**57.** `supervisor_daemon.py:357-427` — Complex inline log parsing (~70 lines). **[Extract-When-Complex]**~~ → Extracted standalone intervention counter
- ~~**58.** `supervisor_daemon_core.py` — `should_launch_daemon_claude` appears unused. **[Miscellaneous]**~~ → Removed dead code
- ~~**84.** `notifier.py` — Similar subprocess-call structure in two notification methods. **[DRY]**~~ → Extracted `_run_notification_cmd`
- ~~**80.** `sister_poller.py:192-267` — `_agent_to_session` is 75 lines with manual field mapping. **[Long Function]**~~ → Cleaned up
- ~~**81.** `sister_poller.py:222` — Uses `__import__("datetime")` inline. **[Miscellaneous]**~~ → Normal import
- ~~**82.** `sister_poller.py:154-160` — Error handling resets 5 fields individually. **[Extract-When-Complex]**~~ → Extracted `_reset_sister_state`
- ~~**77.** `follow_mode.py:126-250` — `follow_agent` is ~125 lines. **[Long Function]**~~ → Extracted helpers
- ~~**78.** `follow_mode.py` — `_poll_for_report` duplicates report checking. **[DRY]**~~ → Extracted helpers
- ~~**79.** `follow_mode.py` — `_emit_new_lines` has complex deduplication logic. **[Extract-When-Complex]**~~ → Extracted helpers
- ~~**92.** `standing_instructions.py` — `load_presets` and `save_presets` both call `ensure directory exists`. **[DRY]**~~ → Extracted `_ensure_presets_dir`
- ~~**88.** `daemon_logging.py` — Inline log coloring logic mixes logging with presentation. **[Separation of Concerns]**~~ → Extracted `_line_style`
- ~~**181.** `summary_columns.py:252-258` — `import unicodedata` at function scope on every call. **[Extract-When-Complex]**~~ → Module-level import
- ~~**182.** `summary_columns.py:528-569` — Inline time formatting with repeated if/elif. **[DRY]**~~ → Deduplicated
- ~~**183.** `summary_columns.py:572-606` — Next heartbeat time computation should be standalone. **[Extract-When-Complex]**~~ → Deduplicated heartbeat
- ~~**184.** `summary_columns.py:744-767` — `render_heartbeat_plain` duplicates computation. **[DRY]**~~ → Deduplicated heartbeat
- ~~**186.** `summary_columns.py:353-363` — Duplicated fallback return for "no data" cases. **[DRY]**~~ → Deduplicated context
- ~~**188.** `summary_columns.py:934-940` — Permissiveness mode emoji mapping duplicated. **[DRY]**~~ → Deduplicated permissiveness emoji

### Batch 12 — Tmux Dedup

- ~~**8.** `implementations.py:142-185` — `RealTmux.send_keys` is a verbatim copy of `TmuxManager.send_keys`. **[DRY]**~~ → Extracted `send_keys_to_pane` into tmux_utils
- ~~**21.** `tmux_manager.py:132-183` — `send_keys` duplicated in `implementations.py`. **[DRY]**~~ → Extracted `send_keys_to_pane` into tmux_utils
- ~~**9.** `implementations.py:266-293` — `RealTmux._attach_bare` is a near-copy of `TmuxManager._attach_bare`. **[DRY]**~~ → Extracted `attach_bare` into tmux_utils
- ~~**22.** `tmux_manager.py:309-341` — `window_exists` reimplements `_get_window` fallback logic. **[DRY]**~~ → Simplified to delegate to `_get_window`

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
