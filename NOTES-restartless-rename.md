# restartless-rename (auto-naming P0, design §1)

Renaming an agent no longer stops it. An agent is its session id; the env
name (`OVERCODE_SESSION_NAME`) is only the name it was launched under.

## What changed

- `agent_identity.py` (new): `in_agent`, `current_agent` (by
  `OVERCODE_SESSION_ID`; by launch name, alias-aware, only with no id),
  `find_in_daemon_state`.
- Hook handler: session-id sync, conversation owner and budget check find
  the agent by id. Enhanced context prints the record's current name.
- `overcode report`, `overcode bash`'s agent link, a child's parent at
  launch, and the "is an agent" tests in `activity_log` / `view_control`
  all go by id.
- Exact-name lookups: `follow` and the TUI fork-name check follow aliases;
  `list` / `show` read daemon state by id; `doctor --fix`
  (`AgentHealth.session_id`), supervisor intervention counts and the
  daemon's subtree cost rollup key by id.
- `@overcode_id` window option (`tmux_utils.AGENT_ID_OPTION`): set at
  launch, fork and revive, at rename, and by the daemon once at start for
  older windows (`TmuxInterface.set_window_option`). The hook's pane
  fallback reads it in one `display-message` and keeps the window-name
  parse as a fallback.
- `rename()`: an agent with `agents/<id>/` is renamed in place (window,
  record, notice). No busy refusal, no exit, no relaunch. `--force` is
  accepted and only affects pre-0.6 agents, which keep the restart path
  (`_rename_with_restart`). `source=` keyword threaded through, not
  stored. CLI and TUI pass `source="human"`.
- TUI rename dialog: the Force row shows only for a pre-0.6 agent.
- Docs: cli-reference rename, TUI guide Ctrl+N, design §1 marked built.
- Live tier: step 6 renames mid-run, sends a prompt under the new name and
  checks the Stop lands; for claude-code and hermes it also checks the
  notice is consumed and the agent answers with its new name.

## Decisions

- "0.6+" means the agent has its own directory. Every launch since
  per-agent directories creates it and sets `OVERCODE_SESSION_ID`.
- An id with no record is no agent. There is no name fallback, because the
  launch name may belong to another agent by then.
- The daemon sets `@overcode_id` unconditionally (idempotent, one tmux
  call per live agent per daemon start) instead of reading it first.
- `monitor_daemon_state.get_session_by_name` stays exact; its CLI callers
  moved to `get_session(id)`. Daemon state has no aliases to follow.
- The in-place rename still bundles an older overcode's name-keyed files
  under the old name, as before.
- A pre-existing isolation bug in
  `test_rename.py::test_user_prompt_submit_prints_the_note` (it failed
  when run alone, also on base) is fixed with `OVERCODE_DIR`.

## Left undone / follow-ups

- `bundled_skills.py` still tells agents to run
  `overcode list "$OVERCODE_SESSION_NAME"`. After a rename that resolves
  through the alias (with a note), but it would hit the wrong agent if
  another agent later takes the old name. Section 7 / P4 (CLI name
  alignment) is the place to fix it.
- The devcontainer wrapper keeps the container named at launch, as the
  design says.

## Tests

- Unit suite: 9223 passed, 17 skipped (5 m 50 s). ruff clean.
- `OVERCODE_LIVE_BACKENDS=claude-code` live tier: 1 passed (34 s),
  including the mid-run rename step. Other backends were not run.
