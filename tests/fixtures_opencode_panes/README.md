# opencode pane corpus

Plain-text `tmux capture-pane -p` captures from **real** opencode v1.18.19
sessions (macOS, `openai/gpt-4o-mini`, 120-column pane), taken while building
the `OpencodeBackend` pattern set (Phase 4 of
`docs/design/agent-agnostic-backends-opencode.md`).

These ground `OPENCODE_PATTERNS` in `src/overcode/backends/opencode.py` and are
replayed through `PollingStatusDetector` by
`tests/unit/test_status_detector_opencode.py`. When opencode's TUI chrome
drifts, re-capture these files and the pattern tests will tell you what broke.

| File | State | Expected status |
|---|---|---|
| `idle_fresh.txt` | fresh launch, banner + empty input box | `waiting_user` |
| `idle_after_response.txt` | turn finished, prompt back | `waiting_user` |
| `busy.txt` | mid-generation, spinner + `esc interrupt` | `running` |
| `permission_required.txt` | `△ Permission required` dialog (`bash: ask`) | `waiting_user` (permission) |
| `command_menu.txt` | slash-command autocomplete open | `waiting_user` |
| `error_api_key.txt` | provider auth failure rendered in a red box | `waiting_user` |
| `tool_execution.txt` | `→ Read` / `✱ Glob` tool blocks, turn finished | `waiting_user` |
| `exited_shell.txt` | after `/exit` — farewell block + shell prompt | `terminated` |
| `interrupted.txt` | double-Escape mid-generation, turn abandoned | `waiting_user` |

`interrupted.txt` was captured in Phase 6 (same opencode version, wider pane)
to fill the one gap Phase 4 left: the pane opencode renders *after* a
keyboard interrupt. The load-bearing detail is that the finished-turn footer
pill gains a suffix — `▣  Build · GPT-4o mini · interrupted` — which is what
`OPENCODE_PATTERNS.interrupt_prompt_markers` matches. The partial response
stays on screen unmarked, and the marker persists indefinitely.

`exited_shell.txt` is the only hand-edited file: the real capture carried an
unexpanded `%n@%m` zsh prompt from the scratch harness, replaced here with a
conventional `user@host ~/dir %` prompt. Note the farewell block — opencode
prints `Continue  opencode -s ses_…`, which is the cheapest place to learn a
session ID without the SQLite store (relevant to Phase 5).

## `v1.18.29/` — second corpus (Sep 21 2026, #474)

Verbatim captures from opencode **v1.18.29** (160-column pane, same model),
taken by the live scenario walk that also produced
`tests/fixtures_opencode_events/`. Replayed by `TestCorpus1_18_29` in the
same test file with the *same* pattern set — the tripwire that ten opencode
releases did not move the chrome — plus two panes the first corpus lacked:

| File | State | Expected status |
|---|---|---|
| `subagent_settled.txt` | a `task` sub-agent turn finished (`✓ General Task … ↳ 2 toolcalls`) | `waiting_user` |
| `permission_required_subagent.txt` | the sub-agent's `bash` asking permission (`⠹ General Task …` above the dialog) | `waiting_user` (permission) |

`exited_shell.txt` here is a real capture 5 s after `/exit` (only the
hostname was replaced); it also grounds `HookStatusDetector`'s dead-shell
check, since opencode emits no bus event on exit.
