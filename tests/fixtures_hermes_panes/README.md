# hermes pane corpus

Plain-text `tmux capture-pane -p` captures from **real** Hermes Agent
v0.21.3 (2026.9.14, upstream 98f758ae) sessions — macOS/arm64, the classic
prompt_toolkit CLI (`hermes --cli`), `openai-api` provider with `gpt-5-mini`,
200x50 pane — taken 2026-09-17 while verifying the hermes backend
(`docs/design/agent-backend-hermes.md`).

These ground `HERMES_PATTERNS` in `src/overcode/backends/hermes.py` and are
replayed through the status detector by
`tests/unit/test_status_detector_hermes.py`, the way
`tests/fixtures_codex_panes/` grounds `CODEX_PATTERNS`. When Hermes's CLI
chrome drifts, re-capture these files and the pattern tests will tell you
what broke.

| File | State | Expected status |
|---|---|---|
| `idle_fresh.txt` | fresh launch, banner + "Welcome to Hermes Agent!" + empty prompt with its rotating placeholder (`❯ Draft a reply to the last email in my inbox`) | `waiting_user` |
| `idle_fresh_yolo.txt` | same, launched with `--yolo` (chrome-identical — no "yolo" badge anywhere) | `waiting_user` |
| `busy.txt` | mid-turn, streaming reasoning; the input line has become `☤ ❯ msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel` | `running` |
| `busy_tool.txt` | mid-turn, a `terminal` tool call in flight (`💻 sleep 40 + 1 command  (  0.5s · ↓ 405 tok)`) | `running` |
| `permission_required.txt` | dangerous-command dialog for `rm -rf ./junkdir_a` (`approvals.mode: manual`) | `waiting_user` (permission) |
| `idle_after_approval.txt` | option `1` (Allow once) taken, command ran, reply boxed, prompt back | `waiting_user` |
| `idle_after_deny.txt` | option `4` (Deny) taken — `[You denied this command — it did not run.]`, agent replied, prompt back | `waiting_user` |
| `interrupted.txt` | single `C-c` while a `sleep 40` tool call was running: `┊ 💻 $  sleep 40 + 1 command  0.8s [exit 130]`, prompt back | `waiting_user` |
| `interrupted_api.txt` | single `C-c` while waiting on the model: `⚡ Interrupted during API call.` / `Operation interrupted: waiting for model response (2.8s elapsed).` | `waiting_user` |
| `command_menu.txt` | `/` typed at the prompt, the slash-command menu open | `waiting_user` |
| `new_confirm.txt` | `/new` sent; its confirmation box (`[1] Approve Once / [2] Always Approve / [3] Cancel`) open, input line reads `⚠ ❯ type 1/2/3, or use ↑/↓ then Enter` | `waiting_user` |
| `idle_resumed.txt` | `hermes --cli --resume <id>`: `↻ Resumed session …` + "Previous Conversation" box, prompt back | `waiting_user` |
| `exited_shell.txt` | after `/quit`: the `Resume this session with: hermes --resume <id>` hint, then the shell prompt | `terminated` |

## Load-bearing details

**The prompt glyph is never bare.** An idle input line is `❯ <placeholder>`
(the placeholder rotates between launches and turns — "Draft a reply to the
last email in my inbox", "Summarize what's in this folder", "Explain this
error and how to fix it", "Research this topic and write me a brief"), so an
exact-line match against `❯` never fires. `HermesStatusPatterns.
is_ready_prompt_line` matches "`❯` alone or `❯ ` + anything", and
`HermesBackend.prompt_ready_line` hands the launcher the same predicate.
The other two spellings of the input line carry a leading glyph and never
match it: `☤ ❯ msg=interrupt · /queue · /bg · /steer · Ctrl+C cancel` while a
turn is in flight, `⚠ ❯ …` while a dialog is open. Selector rows inside a
boxed dialog (`│ ❯ 1. Allow once`) start with `│`.

**"Ctrl+C cancel" is the busy signal.** It sits on the swapped-in input line
for the whole turn; the kaomoji spinner above it (`(◔_◔) formulating...`,
`(｡•́︿•̀｡) pondering...`, `(´･_･`) reflecting...`, …) rotates both face and
verb every few seconds and is only a secondary "thinking" hint.

**The status bar ticks while idle.** ` ☤ gpt-5-mini │ 12.7K/400K │
[░░░░░░░░░░] 3% │ ◎ 98.5% │ ◷ 7.4s │ ↑ 68 t/s │ 5m │ ⏲ 6s │ ✓ 3s` carries
session-elapsed and idle timers that change every second, so `☤ ` is a
`status_bar_prefixes` entry (filtered from the content hash). The bar's
`[░` context segment is present in every live frame and absent from
post-`/quit` scrollback — it is the `input_hint_markers` entry
`_is_shell_prompt` uses to avoid reporting a live REPL as an exited shell.

**A single `C-c` is SAFE; two within 2s force-exit.** Confirmed live on an
idle prompt (process stayed alive, prompt unchanged), mid-API-call
(`interrupted_api.txt`) and mid-tool-call (`interrupted.txt`, the tool
exits 130). Hermes's own docs describe the double-press as its force-exit
gesture, so the backend never sends two: `graceful_exit_keys` is one `C-c`
(settle any turn) then `/quit`.

**Approval dialog exact chrome** (`permission_required.txt`, reached with
`hermes config set approvals.mode manual` — the default `smart` mode has an
auxiliary model pre-screen dangerous commands and only escalates to this
box when it declines):
```
╭────────────────────────────────────────────────╮
│ ⚠️  Dangerous Command                          │
│                                                │
│ rm -rf ./junkdir_a                             │
│                                                │
│ ❯ 1. Allow once                                │
│   2. Allow for this session                    │
│   3. Add to permanent allowlist                │
│   4. Deny                                      │
│                                                │
│ recursive delete                               │
╰────────────────────────────────────────────────╯
  💻 rm -rf ./junkdir_a  (  1.6s · ↓ 270 tok)
  ↑/↓ to select, Enter to confirm  (298s)
 ☤ gpt-5-mini │ 12.3K/400K │ [░░░░░░░░░░] 3% │ … │ 11s │ ⏱ 7s
⚠ ❯
```
`1` + Enter ran the command (`idle_after_approval.txt`); `4` + Enter denied
it and the agent carried on with `[You denied this command — it did not
run.]` (`idle_after_deny.txt`). The `(298s)` countdown ticks, so the
dialog frame is never content-stable — permission detection runs before
the content-changed phase, which is why that ordering matters here.

**`/new` confirms before it clears** (`new_confirm.txt`, default
`tools.slash_confirm.destructive_slash_confirm: true`): the box shares the
"Enter to confirm" chrome with the approval dialog (both are a human's
turn, both `waiting_user`), and `1` + Enter approves it once —
`clear_conversation_keys` sends `/new`, then `1`.

**Model refusals are not approval prompts.** During capture the model
twice declined to run `rm -rf` on its own judgement (asking "Confirm you
want me to run it?") and settled at the prompt — that is `waiting_user`
through the ordinary prompt path, not the permission path; no Hermes
dialog is involved.

**Capture hygiene.** Every file is a verbatim 50-line capture. The scratch
directory path in `idle_fresh.txt`'s banner (`/private/tmp/…/probe-hermes`)
and the session ids are real; nothing was hand-edited.
