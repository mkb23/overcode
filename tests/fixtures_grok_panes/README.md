# grok pane corpus

Plain-text `tmux capture-pane -p` captures from **real** Grok Build v1.0.5
sessions (macOS/arm64, model `grok-4.6` — the only model this account has
access to; `-m grok-4.5` was attempted per the design doc's cost-conscious
default but that id no longer exists (`grok models` lists only `grok-4.6`) so
every capture silently fell back to the account default), 200×50 tmux pane,
captured 2026-08-27 while doing Phase 0 live verification of
`docs/design/agent-backends-codex-grok.md` §3.

**Config caveat that shapes several captures:** the user's own
`~/.grok/config.toml` sets `[ui] permission_mode = "always-approve"`, which
is why several captures show `always-approve` in the footer pill and no
permission dialog even for a shell command. Every capture that needed to show
*normal* approval behavior was launched with the flag passed explicitly —
**`--permission-mode default`** — which was confirmed live to override the
config (see `permission_required.txt` and the flag-vs-config verdict below).
This is the load-bearing fact for `GrokBackend`: overcode must pass
`--permission-mode` explicitly on every launch, never rely on the default.

These ground `GROK_PATTERNS` in `src/overcode/backends/grok.py` (Phase 3/4)
and are replayed through the status detector the way
`tests/unit/test_status_detector_opencode.py` replays the opencode corpus.

| File | State | Expected status |
|---|---|---|
| `idle_fresh.txt` | fresh launch, welcome banner + empty input, `always-approve` footer (no flag passed) | `waiting_user` |
| `idle_fresh_fullscreen.txt` | fresh launch with `--fullscreen` passed explicitly | `waiting_user` (byte-identical to `idle_fresh.txt` except input placeholder text — see recommendation below) |
| `busy.txt` | mid-generation, `⠼ Waiting for response…` spinner + `[stop]` hint in the status bar | `running` |
| `permission_required.txt` | approval dialog (`--permission-mode default`, `Run the command: echo hello`) — numbered radio options, footer loses the `always-approve` tag | `waiting_user` (permission) |
| `idle_after_response.txt` | turn finished (`Worked for 1.7s`), prompt back, footer keys shrink to `Shift+Tab:mode │ Ctrl+x:shortcuts` | `waiting_user` |
| `error_bad_model.txt` | `grok -p '...' -m totally-bogus-model-id-123` — headless mode raises a clear error; **the interactive TUI does not** (see finding below) | `terminated` (error) |
| `interrupted.txt` | mid-generation, single `Esc` sent — turn ends with `Turn cancelled by user in 4.3s.` inline | `waiting_user` |
| `exited_shell.txt` | after `/quit` — prints a `grok --resume <uuid>` hint, pane back at zsh prompt | `terminated` |
| `trust_dialog.txt` | plain `grok` launch in a **never-before-visited** git-initialized scratch dir, no hooks/MCP configured | `waiting_user` — **no trust dialog appears**; identical chrome to `idle_fresh.txt` |
| `command_menu.txt` | `/` typed, slash-command palette open (`/quit`, `/help`, `/docs`, `/home`, `/delete`, `/new`) | `waiting_user` (not in the required set; captured because it settles the exit/clear verdicts) |

## UI mode recommendation: pass `--fullscreen` explicitly

`idle_fresh.txt` (no flag) and `idle_fresh_fullscreen.txt` (`--fullscreen`
passed) are chrome-identical on this machine, because this account's
`~/.grok/config.toml` has no `[ui] screen_mode` override, so plain `grok`
already defaults to the full alt-screen TUI. **Recommend passing
`--fullscreen` on every overcode-launched grok anyway**: `grok --help`
documents that a user's own `[ui] screen_mode = "minimal"` config preference
would otherwise switch default launches to the `--minimal`
scrollback-native renderer (a fundamentally different chrome — finalized
blocks print into native terminal scrollback instead of a redrawn pane),
which would silently break every `StatusPatterns` regex captured here. Since
`--fullscreen` is documented as "session-scoped only — does not write
config" it is a free, side-effect-free way to pin the chrome
`GROK_PATTERNS` was built against, exactly the way the design doc
anticipated ("recommend passing the explicit flag at launch so chrome is
deterministic").

## Findings that surprised us

- **`-m`/`--model` with an unknown id fails silently in the TUI, loudly in
  headless mode.** Interactive `grok -m <bogus>` shows no error anywhere —
  it just renders the account's real default model in the footer, as if the
  flag had never been passed. `grok -p '<prompt>' -m <bogus>` (headless),
  by contrast, exits 1 with `Couldn't set model '<bogus>': Invalid params:
  "unknown model id". Run 'grok models' to see available models.` —
  captured verbatim in `error_bad_model.txt`. A future `GrokBackend` cannot
  detect a bad `--model` from TUI chrome; validate against `grok models`
  output before launch if this matters.
- **Bare Ctrl-C does not kill the grok process** — it safely interrupts
  generation exactly like Escape (fires `StopCancelled` with
  `cancelTrigger: "ctrl_c"` per the hooks doc). Verified live: sent bare
  `C-c` mid-generation in a disposable tmux session, the TUI returned to an
  idle prompt, and a follow-up prompt in the *same* session got a normal
  response. This is the opposite of the opencode lesson the design doc
  warns about — grok is safe here.
- **A prescribed `--session-id` round-trips exactly as documented.** Minted
  `604c15db-7947-4f74-9e39-2ce699ed370a` via `python3 -c
  "import uuid;print(uuid.uuid4())"`, launched `grok --session-id
  <uuid>`, and it appeared at
  `~/.grok/sessions/%2FUsers%2Fmike%2F.claude%2Fjobs%2Ff6bc7dbe%2Ftmp%2Fprobe-grok/604c15db-7947-4f74-9e39-2ce699ed370a/`.
  Any client-supplied UUID (not just grok's own UUIDv7-shaped ids) is
  accepted.
- **A local input/output token split — and cost — *does* exist on disk,
  contradicting the design doc's §3.4 finding.** `updates.jsonl`'s
  `turn_completed` update carries a full `usage` object:
  `inputTokens`, `outputTokens`, `totalTokens`, `cachedReadTokens`,
  `cacheCreationTokens`, `reasoningTokens`, `modelCalls`, `apiDurationMs`,
  and **`costUsdTicks`** (plus a `modelUsage` breakdown keyed by model id).
  See the token-split verdict below — this materially changes the Phase 4
  `GrokStatsReader` scope from "tokens/cost: None (dashes)" to "tokens/cost:
  fully available, parse `updates.jsonl`."
- **`--permission-mode dontAsk` is not an alias for `auto`.** It shows the
  full approval dialog, indistinguishable from `default`. Only `auto`
  actually skips the dialog (footer shows a bare `· auto` tag with no
  dialog). This confirms the design doc's speculation
  ("`dontAsk` ... grok treats `auto` as nearest") in the sense that `auto`
  is the real bypass-equivalent, but `dontAsk` itself behaves like
  `default`, not like `auto` — do not treat them as interchangeable in
  `GrokBackend`'s mode mapping.
