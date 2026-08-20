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

`exited_shell.txt` is the only hand-edited file: the real capture carried an
unexpanded `%n@%m` zsh prompt from the scratch harness, replaced here with a
conventional `user@host ~/dir %` prompt. Note the farewell block — opencode
prints `Continue  opencode -s ses_…`, which is the cheapest place to learn a
session ID without the SQLite store (relevant to Phase 5).
