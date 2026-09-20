# opencode2 pane corpus

Plain-text `tmux capture-pane -p` captures from **real** opencode2
`v0.0.0-dev-19272` sessions (Linux, an OpenAI-compatible provider,
120-column pane), taken Sep 16 2026 while building the
`Opencode2Backend` pattern set. Provider and model names in these
captures have been replaced with placeholders (`acme/acme-llm-1`).

These ground `OPENCODE2_PATTERNS` in `src/overcode/backends/opencode2_patterns.py`
and are replayed through `PollingStatusDetector` by
`tests/unit/test_status_detector_opencode2.py`. opencode2 is a rolling dev
preview — when its TUI chrome drifts, re-capture these files and the pattern
tests will tell you what broke.

| File | State | Expected status |
|---|---|---|
| `idle_fresh.txt` | fresh launch, banner + empty centred input box | `waiting_user` |
| `idle_after_response.txt` | turn finished, prompt back | `waiting_user` |
| `busy.txt` | mid-generation, spinner + `esc interrupt` footer | `running` |
| `permission_required.txt` | `△ Permission required` dialog (`shell: ask`) | `waiting_user` (permission) |
| `command_menu.txt` | slash-command autocomplete open | `waiting_user` |
| `error_api_key.txt` | provider error rendered in the transcript (`Error: Provider request failed with HTTP 404`) | `waiting_user` |
| `interrupted.txt` | double-Escape mid-generation, turn abandoned (`· interrupted` pill) | `waiting_user` |
| `exited_shell.txt` | after graceful exit — bare shell prompt (v2 prints no farewell block) | `terminated` |

Capture notes:

- All eight files were captured the same day on one machine with the same
  procedure: `tmux -L oc2cap` sessions at 120x40 running
  `opencode2 --standalone` in scratch directories, prompts kept minimal.
  They supersede the Sep 15 research captures in `/tmp/opencode/v2tui/`:
  `capture_idle.txt` was really an idle-after-turn state (its "fresh" label
  was wrong), and `capture_busy.txt` turned out to be the permission dialog
  with a transient spinner — the reason `busy.txt` was recaptured with a
  long generation ("write a 300-line poem").
- `permission_required.txt` was captured with a project `opencode.json`
  forcing `{"action": "shell", "resource": "*", "effect": "ask"}` under
  v2's `"permissions"` (plural) key — a v1-style `"permission"` key is
  skipped by v2 with a "configuration normalization diagnostic" log line
  and no dialog ever appears; pressing
  Enter on it approved the preselected "Allow once" (the `approve_keys`
  verification) and the command ran to completion.
- `error_api_key.txt` was produced by switching the session model to a
  project-configured bogus provider whose model the endpoint rejects (HTTP
  404) — the box renders `Error: Provider request failed with HTTP 404` in
  the transcript and the turn ends without consuming tokens. A
  key-validation error was not reachable against this endpoint, so the 404
  transcript error is the corpus's provider-error state.
- `interrupted.txt`: v2 keeps v1's interrupt affordance exactly — first
  Escape arms (`esc again to interrupt`), second abandons the turn and the
  finished pill gains the same `· interrupted` suffix v1 had.
- `exited_shell.txt` is unedited (the v1 corpus's equivalent was the only
  hand-fixed file there). Note the missing farewell block: v1 printed
  `Continue  opencode -s ses_…` after `/exit`; v2 prints nothing after
  `/exit`. A bare `C-c`, by contrast, kills the v2 process *and* prints a
  `Session <title>` / `Continue  opencode2 -s ses_…` farewell block above
  the shell prompt (observed live Sep 17 2026, dev-19272) — the
  `terminated` verdict comes from the shell prompt either way.
