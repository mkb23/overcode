# codex pane corpus

Plain-text `tmux capture-pane -p` captures from **real** Codex CLI v0.150.1
sessions (macOS/arm64, ChatGPT-subscription auth, default model
`gpt-5.6-sol` at `high` reasoning, 200x50 pane), taken 2026-08-27 while
verifying Phase 0 of `docs/design/agent-backends-codex-grok.md`.

These will ground `CODEX_PATTERNS` in the future `src/overcode/backends/codex.py`
and be replayed through the status detector the way
`tests/fixtures_opencode_panes/` grounds `OPENCODE_PATTERNS`. When codex's TUI
chrome drifts, re-capture these files and the pattern tests will tell you what
broke.

| File | State | Expected status |
|---|---|---|
| `idle_fresh.txt` | fresh launch, banner + empty `› Ask Codex to do anything` prompt | `waiting_user` |
| `busy.txt` | mid-generation, `• Working (1s • esc to interrupt)` spinner line | `running` |
| `permission_required.txt` | approval dialog for a command outside the workspace (`touch ~/…`) | `waiting_user` (permission) |
| `idle_after_response.txt` | turn finished, prompt box empty and ready again | `waiting_user` |
| `error_bad_model.txt` | inline API error box after launching with an unsupported model name | `waiting_user` |
| `interrupted.txt` | Escape sent mid-generation; `■ Conversation interrupted` marker shown | `waiting_user` |
| `exited_shell.txt` | after `/quit`, back at the shell prompt | `terminated` |
| `trust_dialog.txt` | first-run folder-trust dialog, captured in a directory codex had never visited | `waiting_user` |

## Load-bearing details

**Model name gotcha.** `-m gpt-5.1-codex-mini` (the model this corpus was
originally supposed to use) is **rejected** under ChatGPT-subscription auth:
codex accepts the flag at the CLI-parsing level, prints
`⚠ Model metadata for 'gpt-5.1-codex-mini' not found. Defaulting to fallback
metadata…`, then the turn fails with an inline JSON error box:
`{"type":"error","status":400,"error":{"type":"invalid_request_error","message":"The
'gpt-5.1-codex-mini' model is not supported when using Codex with a ChatGPT
account."}}`. That rendering is what `error_bad_model.txt` captures — it is a
turn-level error, not an argv-parsing error, so a bad `-m` value does not
prevent the TUI from launching. The rest of this corpus uses the account's
default model (`gpt-5.6-sol`) instead, launched with no `-m` flag at all.

**`C-c` is UNSAFE — it kills the codex process outright, with no
confirmation.** This is exactly the opencode lesson repeating. Tested in an
isolated throwaway session (`codex` launched idle, not generating) wrapped so
the exit could be observed (`codex; echo CODEX_EXITED_STATUS_$? > marker`):
one bare `C-c` press terminated the codex process cleanly (exit status 0) in
under 2 seconds, no dialog, no "press again to confirm." The tmux pane froze
on its last-painted frame (looked identical to a live idle screen) while the
underlying process was already gone — `ps -p <pid>` confirmed. **Do not use
`C-c` as codex's interrupt or exit gesture anywhere in the backend.** The safe
interrupt gesture is **Escape**, which produces the `■ Conversation
interrupted - tell the model what to do differently…` marker seen in
`interrupted.txt` and leaves the process alive and the prompt box usable.

**Approval dialog exact chrome** (from `permission_required.txt`):
```
  Would you like to run the following command?

  Environment: local

  Reason: Do you want to allow creating codex_probe_outside_test.txt in your home directory?

  $ touch /Users/mike/codex_probe_outside_test.txt

› 1. Yes, proceed (y)
  2. Yes, and don't ask again for commands that start with `touch /Users/mike/codex_probe_outside_test.txt` (p)
  3. No, and tell Codex what to do differently (esc)

  Press enter to confirm or esc to cancel
```
Approve = `y` or `Enter` (option 1 is default-selected). Reject = `Escape`
(option 3) — this both cancels the command **and** behaves like an interrupt
(the transcript shows the same `■ Conversation interrupted…` marker
afterwards). There is no separate literal `n` reject key; `Escape` is it.

**Trust dialog exact chrome** (from `trust_dialog.txt`):
```
> You are in <dir>

  Do you trust the contents of this directory? Working with untrusted contents
  comes with higher risk of prompt injection. Trusting the directory allows
  project-local config, hooks, and exec policies to load.

› 1. Yes, continue
  2. No, quit

  Press enter to continue
```
`Enter` accepts option 1 (trust) by default. Trust is persisted per-directory
in `~/.codex/config.toml` (`[projects."<path>"] trust_level = "trusted"`) —
revisiting an already-trusted directory shows no dialog at all.

**Approval requirement is command-shaped, not blanket.** Under the default
`on-request` policy with this account's `sandbox_workspace_write.network_access
= true`, benign in-workspace commands (`echo hello`) and even outbound network
calls (`curl https://example.com`) ran with **zero** approval prompt — only a
command that reached **outside the sandboxed workspace** (writing to
`~/codex_probe_outside_test.txt`, i.e. the home directory, not the launch
cwd) triggered the dialog captured here. A pattern set built only from an
in-workspace command would never see `permission_required.txt`'s state.

**`exited_shell.txt` is hand-edited**, same as the opencode corpus: the raw
capture carried an unexpanded `%n@%m %1~ %#` zsh prompt from the scratch
harness (the outer session was a bare `bash` wrapper, not a login shell), and
has been swapped here for a conventional `user@host ~/probe-codex %` prompt.
Both `/quit` and `/exit` exist as slash commands (`/quit` — exit Codex,
`/exit` — exit Codex) and both cleanly return to the wrapping shell; `/quit`
is what this fixture used. `/new` also exists ("start a new chat during a
conversation") as the clear-conversation gesture.

**Pane size**: every capture in this directory used `tmux new-session -x 200
-y 50`, matching the opencode corpus convention.
