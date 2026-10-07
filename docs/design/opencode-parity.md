# opencode parity audit (0.6.0)

One goal of 0.6.0 is to make opencode as well supported as Claude Code. This
audit compares **Claude Code**, **opencode (v1, verified at 1.18.19 / 1.18.29)** and
**opencode2 (the 2.0 preview, verified at v0.0.0-dev-19272 / dev-19742)** across
every overcode capability.

Every cell has a verdict and the evidence behind it. The evidence is a
`file:line` (paths relative to `src/overcode/` unless they start with `tests/`,
`docs/` or `Makefile`) or a test name. Verdicts:

- **works**: implemented and covered by a test or a live-verified comment in the code.
- **partial**: works with a known limitation, described in the cell.
- **missing**: the feature doesn't exist for this backend.
- **n/a**: the CLI has no such concept.
- **unverified**: the code exists or could apply, but no capture or test shows how the real CLI behaves.

Cells marked **works†** use a code path that ignores the backend: overcode
drives the agent through tmux or reads its hook-state files. There is no test
specific to that backend.

Audited on 2026-10-07 against `release/0.6.0` (d22c174).

## Summary

Counts cover the 49 capability rows below. The colour-replay row is left out
because it's described in its own section.

| Backend | works | partial | missing | n/a | unverified |
|---|---|---|---|---|---|
| Claude Code | 45 | 2 | 0 | 1 | 1 |
| opencode (v1) | 30 | 8 | 4 | 4 | 3 |
| opencode2 | 22 | 9 | 9 | 4 | 5 |

### Top gaps, ranked by how much a user would notice

1. **A plugin-less agent in a hooks-mode fleet reads "Waiting for first hook event" forever (opencode, opencode2).**
   - The detection mode is chosen per session from the backend's `HOOK_EVENTS` bit and the fleet default (`status_detector_factory.py:99-104`). It doesn't check whether this agent's plugin is actually installed.
   - With no `hook_state` file, `HookStatusDetector.detect_status` returns `waiting_user` without looking at the pane (`hook_status_detector.py:715-718`). A working agent therefore shows red.
   - This happens when the plugin was removed, when `backend_telemetry.opencode: off` is set, or when the agent was launched outside overcode. `overcode doctor` does flag it (`backends/opencode.py:612-634`).
2. **opencode2 ignores `--model` and `--agent`, and `restart --model` too.**
   - The bare v2 TUI rejects both flags. `OPENCODE_MODEL` is exported but has no effect on dev-19272 (`backends/opencode2.py:133-141`).
   - The user's choice is accepted without any message and then not applied.
3. **Cost budgets don't block prompts (opencode, opencode2).**
   - Claude's `UserPromptSubmit` hook refuses the prompt and shows red "rejected" (`hook_handler.py:1203-1230`).
   - The opencode plugins have no way to refuse a prompt, so only heartbeats and the supervisor stop (`monitor_daemon.py:669`, `supervisor_daemon_core.py:117`). A person or the parent can still drive a child past its budget.
4. **A child reporting back can hit a permission prompt (opencode in normal permission mode, when the project asks for bash).**
   - Claude children get `Bash(overcode report *)` pre-allowed (`cli/perms.py:13`).
   - opencode has no per-launch allowlist (`PERMISSION_INJECTION` is unset, `backends/opencode.py:423-429`). In a project whose config sets `permission.bash: ask` (as the live capture harness does, `tests/e2e/test_live_opencode_states.py:84`), a normal-mode child asks before it runs `overcode report`, so the parent sees orange "permission" instead of the report. opencode's default bash permission was not checked here: **unverified** whether this bites in an unconfigured project.
5. **The PR column comes only from the pane (opencode, opencode2).**
   - Claude's PR number comes from transcript pr-link records (`monitor_daemon.py:873-887`). opencode only gets the GitHub URL scrape (`monitor_daemon.py:1797-1803`, `status_patterns.py:858`).
   - The PR is missed once the URL scrolls off screen, or if it never appeared there.
6. **Rename and time context never reach the opencode model.**
   - Claude's hook prints the rename notice and the time-context line to the model's context on each prompt (`hook_handler.py:1231-1236`). The opencode plugins can't do this.
   - A renamed opencode agent keeps calling itself by its old name, for example in `overcode report` reasoning and messages to its parent.
7. **The container e2e tier has no opencode coverage.**
   - `MOCK_OPENCODE` is defined in `tests/container/harness/core.py:10` but no workflow uses it.
   - Budgets, delegation, fork/restart, shutdown/revive and the supervisor are container-tested for Claude only.
8. **opencode2 is still thin.**
   - There's no fork (`backends/opencode2.py:88-91`) and no skill profiles.
   - `/new` session tracking and `session.execution.failed` exist only as code, with no capture behind them (`overcode-telemetry-core.mjs:443-457`, `:590-600`).
   - CTX% has no denominator from opencode's own catalog (the `reported_context_window` field isn't set at `opencode2_stats.py:502-525`).

Also worth knowing:
- No backend has a launch-time reasoning-effort setting. Effort is only displayed.
- What opencode does with the `question` tool, with a background `task`, and with background shells has never been captured.

## 1. Launch, model and personas

| Capability | Claude Code | opencode (v1) | opencode2 | What closing the gap would take |
|---|---|---|---|---|
| Model selection at launch | **works**: `--model` (`backends/claude_code.py:181`) | **works**: `--model provider/model`, passed through as given so a bare name fails loudly (`backends/opencode.py:466-472`) | **missing**: no `--model` on the bare TUI. `OPENCODE_MODEL` is exported but has no effect on dev-19272 (`backends/opencode2.py:133-141`). The docs say so (`docs/backends.md:431`). | Wait for a v2 build that honours the env var or a flag. Until then, write `model` into a per-launch config overlay (`OPENCODE_CONFIG_DIR`-style, unverified for v2), or tell the user at launch that the choice is ignored. |
| `restart --model` | **works** (`cli/agent.py:860-863`; #505 container test `tests/container/workflows/test_shutdown_revive.py:65`) | **works**: the same relaunch path rebuilds the argv with `--model` (`backends/opencode.py:471`) | **missing**: same cause as above | Same as above |
| Reasoning effort | **partial**: read from the transcript (`stats_reader.py:293-302`). There's no launch setting: `LaunchSpec` has no effort field (`backends/base.py:69-110`). | **partial**: shown from the model variant (`backends/opencode_stats.py:267-286`, `:886-889`). No launch setting. | **partial**: shown from the variant (`backends/opencode2_stats.py:289`, `:520`). No launch setting. | Add `LaunchSpec.effort` and map it per backend. Claude: `/effort` or a flag, unverified. opencode: a model variant suffix, unverified. Then add a `--effort` CLI option. |
| Permission modes (bypass / permissive) | **works**: `--dangerously-skip-permissions` / `--permission-mode dontAsk` (`backends/claude_code.py:175-178`) | **partial**: bypass is `--auto` plus `OPENCODE_PERMISSION` allow-all, which beats project deny rules. Permissive is `--auto` alone, so deny rules still win (`backends/opencode.py:477-482`, `:509-556`; `docs/backends.md:100`). | **partial**: `--auto` is all there is. No env override beats a project deny (`backends/opencode2.py:33-42`, `:109-115`). | v2: nothing until the CLI offers an override. v1: by design; documented. |
| Allowed-tools allowlist (`PERMISSION_INJECTION`) | **works**: `--allowedTools` (`backends/claude_code.py:184-185`) | **missing**: no flag exists (`backends/opencode.py:418-421`) | **missing** (`backends/opencode2.py:78-82`) | v1: put the allowlist in `OPENCODE_PERMISSION` as per-tool `allow` rules. The env merge is verified (`backends/opencode.py:528-534`); the per-tool grammar is untested. This would also fix gap 4 (report-back prompts). |
| Personas (`--agent`) | **works** (`backends/claude_code.py:182-183`) | **works**: `AGENT_INJECTION` (`backends/opencode.py:474-475`) | **missing**: no launch-time knob. The persona is only detected afterwards from the stats (`monitor_daemon.py:1010-1030`). | Needs CLI support |
| Skills (overcode skill discoverable, loaded-skill badge) | **works**: `SKILLS` capability. The bundled skill is written to `~/.claude/skills` (`bundled_skills.py:116-148`). Loaded skills are tracked by the hook. | **partial**: no `SKILLS` capability, but opencode 1.18.29 scans `~/.claude/skills` recursively, so the bundled overcode skill is visible (`skill_library.py:67-75`). The plugin tracks `Skill` tool calls into `loaded_skills` (`opencode_plugin/overcode-telemetry.js:242-246`). The skills picker and staleness checks are Claude-only (`bundled_skills.py:116-148`). | **unverified**: v2's skill discovery directories were never checked. The plugin does track `loaded_skills` (`overcode-telemetry-core.mjs:210-224`). | Confirm v2's skill directories live. Set `SKILLS` for v1 once the picker reads opencode's directories. |
| Skill profiles (#499) | **works**: `--plugin-dir` + `skillOverrides` (`backends/claude_code.py:165-171`) | **works**: `OPENCODE_CONFIG_DIR` + `skill` deny rules, verified on 1.18.29 (`backends/opencode.py:541-556`) | **missing**: no `SKILL_PROFILES` capability (`backends/opencode2.py:78-82`) | Check whether v2 honours `OPENCODE_CONFIG_DIR` |

## 2. Standing instructions, heartbeats, context injection

| Capability | Claude Code | opencode (v1) | opencode2 | What closing the gap would take |
|---|---|---|---|---|
| Standing instructions | **works**: supervisor `daemon_claude` sends text through tmux (`supervisor_daemon_core.py:49-54`, `:90-117`; `tests/container/workflows/test_supervisor.py`) | **works†**: same delivery path; the supervisor prompt names the backend (`supervisor_daemon_core.py:52-54`) | **works†** | Add an opencode mock to the container tier (gap 7) |
| Heartbeats | **works**: `monitor_daemon.py:660-705`. In hooks mode it skips the send while the agent is working (`:683-692`). Tests: `tests/container/workflows/test_instructions_heartbeat.py`. | **works†**: the plugin publishes the same `UserPromptSubmit`/`PreToolUse` events the skip check reads (`opencode_plugin/overcode-telemetry.js:411-438`) | **works†**: plain text plus Enter. The autocomplete quirk only affects slash commands (`backends/opencode2.py:152-166`). | Same as above |
| Per-prompt context injection (time context, rename notice) | **works**: hook stdout goes into the model's context (`hook_handler.py:1231-1236`, `:420-429`) | **missing**: the plugin only writes files and can't add text to the prompt | **missing** | Possibly by mutating `output.parts` in the `chat.message` hook. Whether that reaches the model is unverified. |

## 3. Status detection by colour

opencode's colours come from the same `HookStatusDetector`. The plugins write
the `hook_state_<agent>.json` and `hook_events_<agent>.jsonl` files that Claude's
hook handler writes (`opencode_plugin/overcode-telemetry.js:1-63`), and the
pane fallback uses `OPENCODE_PATTERNS` / `OPENCODE2_PATTERNS`.

| Capability | Claude Code | opencode (v1) | opencode2 | What closing the gap would take |
|---|---|---|---|---|
| Working (green) | **works** (`tests/unit/test_status_replay.py::PLAIN_TURN`) | **works**: `chat.message` → UserPromptSubmit, then `tool.execute.before/after` (`opencode_plugin/overcode-telemetry.js:401-438`; fixture `simple_turn.jsonl`, `read_tool.jsonl`) | **works**: `session.inbox.enqueued` / `session.tool.*` (`overcode-telemetry-core.mjs:461-530`; `test_opencode2_plugin.py::test_tool_roundtrip`) | |
| Idle, needs you (red) | **works** | **works**: `session.status{idle}` / `session.idle` → one Stop (`opencode_plugin/overcode-telemetry.js:396-399`, `:505-526`; `test_opencode_plugin_replay.py::test_queued_prompts_publish_one_stop`) | **works**: `session.execution.succeeded` → Stop (`overcode-telemetry-core.mjs:580-588`; `test_turn_end_settles_once`) | |
| Permission prompt (orange) | **works** (`PERMISSION_PROMPT`, `TWO_PERMISSION_PROMPTS`) | **works**: `permission.asked` → PermissionRequest. On allow, `permission.replied` → PreToolUse; on reject → PostToolUse (`opencode_plugin/overcode-telemetry.js:462-503`; fixtures `permission_allow.jsonl`, `permission_reject.jsonl`). Pane patterns: `backends/opencode.py:259-264`. | **works** (`overcode-telemetry-core.mjs:534-578`; `test_permission_roundtrip`) | |
| Plan approval | **partial**: the polling detector matches plan-mode text (`status_patterns.py:161`, `:185-190`; `status_detector.py:318`). The hooks path has nothing that sets the `plan_approval` badge (`status_constants.py:478`), so it's never shown. | **n/a**: opencode has no approval stage (`approval_patterns=[_NEVER]`, `backends/opencode.py:312`). Its "plan" agent is a persona, not an approval gate. | **n/a** (`backends/opencode2_patterns.py:117`) | Claude: map `PreToolUse[ExitPlanMode]` to orange `plan_approval` |
| Question (Claude's `AskUserQuestion`, opencode's `question` tool) | **unverified**: the `ask_question` badge is defined (`status_constants.py:482`) but nothing in overcode sets it. Whether Claude Code fires `PermissionRequest` for the question dialog (which would show orange) or only `PreToolUse` (green while it waits) has no capture or replay scenario. | **unverified**: `question` isn't in `TOOL_NAME_MAP` (`opencode_plugin/overcode-telemetry.js:129-145`); its event shape was never captured | **unverified** | Capture a live `question` call, then map it to red `ask_question` in both plugins and in `hook_status_detector.compute_status_detail` |
| Interrupt (Esc) | **works**: pane marker (`INTERRUPT_THEN_REPROMPT`, `PERMISSION_DENIED`) | **works**: `MessageAbortedError` isn't treated as an error and the idle after it settles to Stop (`opencode_plugin/overcode-telemetry.js:115`, `:528-542`; fixture `interrupt.jsonl`). The pane marker is `· interrupted` (`backends/opencode.py:374`). | **works**: `session.execution.interrupted` → Stop (`overcode-telemetry-core.mjs:580-588`; `test_interrupted_turn_maps_to_stop`) | |
| Errors (red, error badge) | **works**: StopFailure | **works**: `session.error{APIError}` → StopFailure with a reason, which survives the idle that follows (fixture `provider_error.jsonl`; `test_provider_error_stays_visible`). Pane patterns: `backends/opencode.py:324-334`. | **partial**: `session.execution.failed` is handled but was "not observed live" (`overcode-telemetry-core.mjs:590-600`); the v1-style `session.error` path is kept (`:609-622`) | Capture a v2 provider failure |
| Subagents, foreground (`task` tool) | **works** (`FOREGROUND_AGENT`) | **works**: the child session's events are filtered out, so the parent stays inside `Task` (`opencode_plugin/overcode-telemetry.js:353-394`, `:443-453`; fixture `subagent_task.jsonl`; `test_subagent_turn_never_stops_the_parent`) | **works**: the `subagent` tool, with a flat `parentID` (`overcode-telemetry-core.mjs:443-457`; `test_child_subagent_turn_fully_ignored_live_shape`) | |
| Subagents, background (yellow) | **works**: the `subagents` map (`hook_handler.py:254-263`; `hook_status_detector.py:165-175`; `BACKGROUND_AGENT`) | **unverified**: no background mode of `task` was seen in 1.18.29, and the plugin keeps no subagent map | **unverified** | Find out whether opencode can run a task in the background. If it can, publish SubagentStart/Stop from child `session.created` / idle. |
| A child's permission prompt shows on the parent | **works** (`SUBAGENT_PERMISSION`) | **works** (fixture `child_permission.jsonl`; `test_child_permission_ask_surfaces_for_the_parent`) | **works** (`test_opencode2_plugin.py::test_child_permission_ask_surfaces_for_the_parent`) | |
| Background shells (yellow) | **works**: status-bar count plus `bg_task` obligations (`hook_handler.py:45-81`; `BACKGROUND_SHELL`) | **unverified**: `background_bash_count_pattern=_NEVER` (`backends/opencode.py:384-394`). Whether opencode's bash tool can run in the background is unknown. | **unverified** | Same question as background subagents |
| Monitor streams, ScheduleWakeup, wakeups | **works** (`MONITOR_ARMED`, `SCHEDULE_WAKEUP`) | **n/a** per the plugin authors: "no opencode analogue", so obligations are carried forward but never armed (`opencode_plugin/overcode-telemetry.js:257-262`). This hasn't been checked against opencode's tool list. | **n/a** (same claim, `overcode-telemetry-core.mjs:229-232`) | |
| Plugin missing (pane-only fallback) | **works**: the polling detector, or hooks via `--settings` that can't go missing | **partial**: in a polling fleet the pane gives the right colours (`test_status_replay_opencode.py::oc_plugin_missing_polling_fleet`). In a hooks fleet a session with no state file never reads the pane for status, so a working agent shows red (`hook_status_detector.py:699-718`; strict xfail `oc_plugin_missing_hooks_fleet`). Doctor flags the missing plugin and says status "falls back to pane polling", which is only true in a polling fleet (`backends/opencode.py:612-634`; same claim in `prepare_launch`'s docstring, `:494-496`). | **partial**: same (`backends/opencode2.py:206-220`; strict xfail `oc2_plugin_missing_hooks_fleet`) | Have `resolve_session_detection_mode` fall back to polling when a `HOOK_EVENTS` backend's plugin isn't installed, or make the no-state branch delegate to the polling detector |
| Process exit (`/exit`, crash) | **works**: SessionEnd | **works**: no event fires on exit, so the bare shell prompt in the pane is detected instead (`hook_status_detector.py:81-104`, `:741-748`) | **works**: same | |

## 4. Stats and session tracking

| Capability | Claude Code | opencode (v1) | opencode2 | What closing the gap would take |
|---|---|---|---|---|
| Tokens | **works** (`stats_reader.py:112-320`) | **works**: SQLite `session` table, with reasoning folded into output (`backends/opencode_stats.py:853-914`) | **works** (`backends/opencode2_stats.py:471-525`) | |
| Cost | **works**: computed from `pricing.py` | **works**: opencode's stored cost, with a `pricing.py` fallback when it's 0 (`backends/opencode_stats.py:920-938`) | **works** (`backends/opencode2_stats.py:532-550`) | |
| Context % | **works** (`docs/backends.md:768`) | **works**: opencode's own models.dev limit (`backends/opencode_stats.py:133`, `:908`; #469) | **partial**: has a numerator (`backends/opencode2_stats.py:356-362`) but no `reported_context_window`, so it falls back to the curated table or snapshot | Pass `opencode_context_limit(model)` as v1 does |
| Energy | **works**: from tokens and model (`energy.py:185`) | **works†**: same function; `provider/model` names match by substring (`energy.py:105-115`) | **works†** | |
| Burn rate | **works** | **works**: `WindowIndex` (#517) (`backends/opencode_stats.py:562-630`) | **works** (`backends/opencode2_stats.py:627`) | |
| Session id tracking across `/new` | **works**: SessionStart `clear` (`hook_handler.py:306-311`) | **works**: a second root `session.created` is added to `agent_session_ids` (fixture `new_session.jsonl`; `test_new_session_records_second_root`) | **partial**: the code path exists (`overcode-telemetry-core.mjs:443-457`), but there's no `/new` capture or test | Capture v2 `/new` |
| Session id chosen up front | **works**: `--session-id` (`backends/claude_code.py:157-158`) | **missing**: opencode mints `ses_…` ids. The plugin and directory discovery recover them instead (`backends/opencode_stats.py:940-1013`). | **missing** | Low impact. Discovery covers it. |

## 5. Lifecycle

| Capability | Claude Code | opencode (v1) | opencode2 | What closing the gap would take |
|---|---|---|---|---|
| Restart with resume | **works**: `--resume` (`lifecycle.py:52-65`) | **works**: `--session <id>` (`backends/opencode.py:440-444`; `test_backend_matrix.py::test_restart_graceful_exits_and_relaunches_in_place`) | **works** (`backends/opencode2.py:88-91`; `tests/e2e/test_opencode2_backend.py`) | |
| Fork | **works** | **works**: `--session <id> --fork` (`backends/opencode.py:415-416`, gate `launcher.py:679`) | **missing**: `--fork` exists only on `mini`/`run` (`backends/opencode2.py:88-91`) | Needs CLI support, or a `run --fork` bootstrap followed by a TUI resume (unverified) |
| Sleep / wake | **works**: an overcode-side flag (`monitor_daemon_core.py:185`, `supervisor_daemon_core.py:113`) | **works†** | **works†** | |
| Kill | **works** | **works** (`test_backend_matrix.py::test_kill_removes_window_and_state`) | **works** (`tests/e2e/test_opencode2_backend.py`) | |
| Rename | **works**: stop and resume, and the agent is told its new name (`launcher.py:1038`, `:1157`; `hook_handler.py:1236`) | **partial**: the stop/resume works, but the rename notice is only delivered by Claude's hook, so the model never learns its new name | **partial** | Same fix as per-prompt context injection |

## 6. Delegation, oversight, budgets

| Capability | Claude Code | opencode (v1) | opencode2 | What closing the gap would take |
|---|---|---|---|---|
| Child agents (launch with a parent) | **works** (`tests/e2e/test_child_delegation.py`) | **works†**: `-B opencode` on launch (`cli/agent.py:265`) | **works†** | |
| Child reports back (`overcode report`) | **works**: bundled skill plus the `Bash(overcode report *)` allow rule (`bundled_skills.py:49`, `:101-102`; `cli/perms.py:13`) | **partial**: the skill is visible through `~/.claude/skills` (`skill_library.py:67-72`), but with no allowlist a normal-mode child in a project that sets `permission.bash: ask` asks permission to run `overcode report` (default-config behaviour unverified) | **unverified**: skill discovery is unchecked and the permission problem is the same | Add an `OPENCODE_PERMISSION` bash allow rule for `overcode report *` / `overcode show *` (see the allowlist row) |
| Oversight orange (a child stopped without reporting) | **works** (`CHILD_REPORTS_BACK`) | **works**: the plugin's Stop goes to `waiting_oversight` because of the session's parent (`hook_status_detector.py:735-737`; `test_status_replay_opencode.py::oc_child_reports_back`, and `oc_child_interrupted` shows red, not orange) | **works**: same (`oc2_child_reports_back`) | |
| Cost budgets | **works**: the prompt is blocked and shows red "rejected" (`hook_handler.py:1203-1230`), and heartbeats and the supervisor skip the agent (`monitor_daemon.py:669`, `supervisor_daemon_core.py:117`) | **partial**: `budget_exceeded` is computed from the stored cost (`monitor_daemon.py:158-162`) and heartbeats and the supervisor stop, but typed or `overcode send` prompts aren't blocked | **partial** | Gate `overcode send`/instruction delivery on `budget_exceeded` for every backend; the plugin can't refuse a prompt |

## 7. Columns, telemetry, doctor

| Capability | Claude Code | opencode (v1) | opencode2 | What closing the gap would take |
|---|---|---|---|---|
| PR column | **works**: transcript pr-link records (#489, `monitor_daemon.py:873-887`) plus a pane scrape | **partial**: only the pane URL scrape (`monitor_daemon.py:1797-1803`, `status_patterns.py:858`) | **partial** | Run a backend-neutral `gh pr view --json number` for the branch (already refreshed per git context, `monitor_daemon.py:1811`), or scan the SQLite tool output for PR URLs |
| Sandbox column | **works**: `SANDBOX_PROBE` (`monitor_daemon.py:1731`) | **n/a**: opencode has no `/sandbox` | **n/a** | |
| Subscription-usage widget | **works**: `SUBSCRIPTION_USAGE` | **n/a**: no Anthropic usage API | **n/a** | |
| Telemetry opt-out | **n/a**: exempt, because `--settings` leaves nothing on disk (`config.py:430-434`) | **works**: `backend_telemetry.opencode: off` skips the plugin install (`config.py:420-443`; `backends/opencode.py:490-507`); `overcode hooks uninstall-backend` removes it (`backends/opencode.py:636-654`) | **works** (`backends/opencode2.py:121-126`, `:222-277`) | |
| `overcode doctor` checks | **works**: inspects `--settings` | **works**: plugin present (`backends/opencode.py:612-634`), tested version range (`:692`), autoupdate (`:132`), SQLite schema (`backends/opencode_stats.py:218`) | **works**: plugin present (`backends/opencode2.py:206-220`), preview-build warning and schema (`:279-286`, `:331-360`) | |

## 8. Test tiers

| Capability | Claude Code | opencode (v1) | opencode2 | What closing the gap would take |
|---|---|---|---|---|
| Mock e2e matrix (`make test-matrix`) | **works**: Claude's own e2e suite (`tests/e2e/test_hook_status_detection.py` and others, `tests/mock_claude.py`) | **works** (`tests/e2e/test_backend_matrix.py:64`) | **works** (`tests/e2e/test_opencode2_backend.py`; `Makefile:45-46`) | |
| Live smoke (`make test-live`) | **works**, with stats not asserted (`tests/e2e/test_live_backends.py:170-189`) | **works** (`tests/e2e/test_live_backends.py:191-202`, plus `tests/e2e/test_live_opencode_states.py` which also re-captures the event corpus) | **works** (`tests/e2e/test_live_backends.py:203-214`) | |
| Container e2e (`make e2e`) | **works** (`tests/container/workflows/*`, 16 workflows) | **missing**: `MOCK_OPENCODE` is defined but unused (`tests/container/harness/core.py:10`) | **missing** | Parametrize `status_detection`, `delegation`, `budget` and `fork_restart` over `tests/mock_opencode.py` |

## Colour-level replay coverage

`tests/unit/test_status_replay_opencode.py` is the opencode counterpart of
`tests/unit/test_status_replay.py`. `tests/status_replay.py` is now
backend-agnostic: a scenario names its backend and the fleet's detection
mode, the replay samples the real `StatusDetectorDispatcher`, and opencode
records go through the **real bundled plugin** (v1: the exported factory;
opencode2: the `createTelemetry` reducer in `overcode-telemetry-core.mjs`)
via `tests/js/opencode_plugin_replay.mjs`, with the plugin's clock pinned to
the scenario's. The hook files the plugin writes for each record are laid
down at that record's time.

opencode v1 scenarios replay the live v1.18.29 captures in
`tests/fixtures_opencode_events/` with their real timing and draw the
verbatim panes in `tests/fixtures_opencode_panes/v1.18.29/`. The only liberty
is a `hold`, which delays one record to model a person answering a dialog
later than in the capture. opencode2 scenarios use the live SSE envelope
shapes from `test_opencode2_plugin.py` on a synthetic timeline.

| Scenario | Backend | Grounding | Result |
|---|---|---|---|
| `oc_plain_turn` | v1 | `read_tool` | green → red, one change |
| `oc_fresh_launch_text_turn` | v1 | `simple_turn` (7 s of generation, no tool hooks) | red → green → red |
| `oc_quick_reply` | v1 | `new_session` (2 s reply) | green → red |
| `oc_permission_approved` | v1 | `permission_allow`, answered 10 s later | green → orange → green → red; badge `permission`, then `tool` |
| `oc_permission_denied` | v1 | `permission_reject`, answered 10 s later | green → orange → red (with the usual 1.5 s sticky green after the turn ends) |
| `oc_subagent_foreground` | v1 | `subagent_task`, child held 20 s | green throughout the child's own turn; the child id never joins `agent_session_ids` |
| `oc_subagent_permission` | v1 | `child_permission`, answered 8 s later | orange while the child's dialog is up in the parent's pane |
| `oc_interrupt_then_reprompt` | v1 | `interrupt` + `read_tool` | red after a double-Escape; the lingering `· interrupted` pill doesn't hold the next turn red |
| `oc_provider_error` | v1 | `provider_error` | red with an `error` badge that survives the idle in the same millisecond |
| `oc_new_session_mid_run` | v1 | `read_tool` + `new_session` | colours per turn; the new root becomes `agent_session_id` |
| `oc_queued_prompts`, `oc_resumed` | v1 | `queued_prompts`, `resume` | one Stop; a resumed process adopts its conversation |
| `oc_child_reports_back`, `oc_child_interrupted` | v1 | `read_tool`, `interrupt` | oversight orange; an interrupted child is red |
| `oc_plugin_missing_polling_fleet` | v1 | panes only | pane-driven red → green → red |
| `oc_plugin_missing_hooks_fleet` | v1 | panes only | **strict xfail**: stays red while the pane is busy |
| `oc2_plain_turn`, `oc2_quick_reply` | v2 | live envelope shapes | green → red |
| `oc2_permission_approved` / `_denied` | v2 | flat `action` / `resources` permission shape | orange while asked |
| `oc2_subagent_foreground` | v2 | live `subagent` child-session shape | green throughout |
| `oc2_interrupt` | v2 | `session.execution.interrupted` + `interrupted.txt` | red |
| `oc2_execution_failed` | v2 | `session.execution.failed`: **never observed live** | red with `error` |
| `oc2_child_reports_back` | v2 | live shapes | oversight orange |
| `oc2_plugin_missing_hooks_fleet` | v2 | panes only | **strict xfail**, same bug as v1 |

There's no yellow scenario: neither version showed a background shell,
background subagent, Monitor stream or wakeup, and the plugins never arm an
obligation. `tests/e2e/test_live_opencode_states.py` re-captures the v1
corpus if opencode's event vocabulary moves.

`tests/unit/test_episodes.py::TestReplayScenarios` also records three of these
on the episode layer:
- an opencode stall rings once;
- an ~11 s permission prompt mid-turn merges into the green episode as an orange blip;
- the same dialog held past G rings once, for orange.

## Issues worth filing

1. **opencode: a plugin-less agent in a hooks-mode fleet never reads its pane.** `resolve_session_detection_mode` picks hooks for any backend with `HOOK_EVENTS`, whether or not this agent's telemetry plugin is installed. When `hook_state_<agent>.json` is missing, `HookStatusDetector.detect_status` returns `waiting_user` ("Waiting for first hook event") without consulting the pane (`hook_status_detector.py:699-718`). An opencode agent whose plugin was deleted, opted out with `backend_telemetry.opencode: off`, or never installed shows red while it works. `doctor` already detects the missing plugin, but tells the user status "falls back to pane polling", which is only true in a polling fleet. Detection should fall back to the polling detector with `OPENCODE_PATTERNS` in that case. Pinned by the strict xfails `oc_plugin_missing_hooks_fleet` and `oc2_plugin_missing_hooks_fleet` in `tests/unit/test_status_replay_opencode.py`; they XPASS once fixed.

2. **opencode2: `--model`, `--agent` and `restart --model` are accepted without a word, then ignored.** The bare v2 TUI rejects `--model`/`--agent`, and `OPENCODE_MODEL` (which overcode exports) has no effect on dev-19272 (`backends/opencode2.py:133-141`). The user's choice is recorded on the session and shown nowhere as unapplied. At minimum, warn at launch and restart. Better, find a per-launch config overlay that v2 honours.

3. **Cost budgets aren't enforced on prompts for non-Claude backends.** Claude's `UserPromptSubmit` hook blocks prompts once `budget_exceeded` is set (`hook_handler.py:1203-1230`). opencode and opencode2 only stop heartbeats and supervisor nudges. `overcode send`, standing-instruction delivery and the parent's instructions still reach an over-budget child. Gate delivery in overcode itself (send / instruct / heartbeat) on `budget_exceeded` for every backend, and show the same red "rejected" badge.

4. **opencode children can prompt for permission to run `overcode report`.** Claude children get `Bash(overcode report *)`, `overcode show *` and similar pre-allowed (`cli/perms.py:13-15`). opencode has no allowlist flag, but `OPENCODE_PERMISSION` is merged after project config (verified), so overcode could inject `{"bash": {"overcode report *": "allow", ...}}` for every launch. Without it, a normal-mode opencode child in a project that asks for bash shows orange "permission" exactly when it tries to report back. First check opencode's default bash permission: if it is `allow`, this only affects projects that opt into `ask`.

5. **PR column is pane-scrape-only for opencode/opencode2.** Claude gets the PR number from transcript pr-link records (#489). For opencode the number only appears if a `github.com/.../pull/N` URL is on screen when the daemon polls. Use `gh pr view --json number` on the agent's branch, which is backend-neutral, or scan opencode's stored tool output.

6. **Rename and time-context notices never reach opencode models.** `hook_handler` prints the rename notice and the enhanced time-context line to Claude's context on each `UserPromptSubmit`. The opencode plugins only write files. A renamed opencode agent keeps its old name. Investigate whether the `chat.message` hook can append a text part that reaches the model (v1) or the v2 equivalent.

7. **Container e2e tier: add opencode.** `tests/container/harness/core.py` defines `MOCK_OPENCODE` but no workflow uses it. Parametrize the status-detection, delegation, budget, fork/restart and shutdown/revive workflows over the opencode mock so lifecycle regressions are caught for the second-most-used backend.

8. **opencode2 parity follow-ups (captures needed).** Capture and pin with tests: `/new` (second root session id), `session.execution.failed` (a provider error), the `question` tool, and v2 skill discovery directories. Also pass opencode's own context limit as `reported_context_window` in `Opencode2StatsReader` so CTX% matches the v2 console.

9. **Question / plan-approval badges are never produced in hooks mode.** `status_constants.py` defines `ask_question` (red) and `plan_approval` (orange), but `compute_status_detail` never emits them. Nothing pins what colour Claude's `AskUserQuestion` shows while it waits for the user (green if only `PreToolUse` fires); capture it, then map `PreToolUse[AskUserQuestion]` and `PreToolUse[ExitPlanMode]` to them, and do the same for opencode's `question` tool once captured.

10. **Reasoning effort has no launch-time setting.** Effort is shown for Claude, codex, grok and opencode (#497), but `LaunchSpec` has no effort field and the CLI has no `--effort`. Add one and map it per backend: an opencode model variant, a Claude flag or setting, codex `-c model_reasoning_effort`.
