# Agent Backend: Hermes Agent (NousResearch)

**Document Type:** Design Assessment + Verified Appendix
**Date:** September 2026
**Status:** Implemented (issue #475, Sep 17 2026)
**Scope:** Adding the Hermes agent (`hermes`, https://github.com/NousResearch/hermes-agent) as overcode's fifth backend on the `AgentBackend` seam, and the seam generalisations it needed
**Predecessors:** `docs/design/agent-agnostic-backends-opencode.md` (the seam), `docs/design/agent-backends-codex-grok.md` (the two backends added on it) — read their §2 first; this document does not re-explain the seam.

> **Ground truth discipline.** Every claim tagged **[VERIFIED]** was checked
> against a live install on this machine: Hermes Agent v0.21.3 (2026.9.14,
> upstream `98f758ae`), installed via Nous's curl installer, macOS/arm64,
> `openai-api` provider with `gpt-5-mini`, driven both by hand in an isolated
> tmux server and end-to-end through `overcode launch`/`send`/`restart`/
> `kill`/`doctor` in an isolated `OVERCODE_STATE_DIR`. The pane corpus is
> `tests/fixtures_hermes_panes/`; hook payloads were captured through
> Hermes's shell-hook and plugin surfaces. Claims about the Hermes source
> cite the tree at that upstream commit.

---

## Executive summary

**Verdict: adapter-sized, with four small generalisations of the seam that
were worth making rather than working around.** Hermes is the first
backend that (a) runs as an interpreter (its venv `python`) rather than a
binary with a distinctive basename, (b) never draws its prompt glyph bare,
(c) needs a one-time enable step through the CLI's own config tool, and
(d) has a Python plugin system rich enough to translate its hook vocabulary
into Claude's on the adapter side — the second backend (after opencode) to
prove that shape, and the reason `hook_handler` still knows exactly two
dialects.

What shipped: `src/overcode/backends/hermes.py` (adapter + `HERMES_PATTERNS`
+ plugin install/enable + doctor findings), `src/overcode/backends/
hermes_stats.py` (`HermesStatsReader` over `state.db`),
`src/overcode/hermes_plugin/` (the bundled plugin), `tests/mock_hermes.py`,
a 13-file pane corpus, five new test files, and the seam changes below.
Capabilities declared: `RESUME | HOOK_EVENTS | TRANSCRIPT_STATS`.

## 1. What Hermes is, as a backend target [VERIFIED]

- **Install:** `curl -fsSL https://hermes-agent.nousresearch.com/install.sh |
  bash` (`--skip-setup --skip-browser --skip-computer-use --non-interactive`
  ran cleanly under the tool harness). Provisions its own Python 3.11 venv
  and Node under `$HERMES_HOME` (default `~/.hermes`); `~/.local/bin/hermes`
  is a 3-line bash shim: `exec $HERMES_HOME/hermes-agent/venv/bin/python
  $HERMES_HOME/hermes-agent/hermes "$@"`. Consequence: the process the pane
  runs has basename `python`.
- **Version:** `hermes --version` → multi-line, first line `Hermes Agent
  v0.21.3 (2026.9.14) · upstream 98f758ae`.
- **Two front-ends:** the classic prompt_toolkit REPL (`--cli`, the shipped
  default) and a Node TUI (`--tui`, `HERMES_TUI=1`, or `display.interface:
  tui` in config). overcode forces `--cli` on every launch; the TUI's
  alternate-screen chrome is unverified.
- **Provider setup is a prerequisite.** A fresh install has no provider;
  `hermes` then opens an interactive wizard. Non-interactive path: `hermes
  config set OPENAI_API_KEY …` (writes `$HERMES_HOME/.env`), `hermes config
  set model.provider openai-api`, `model.default gpt-5-mini`, and — the
  trap found live — `model.base_url https://api.openai.com/v1`, because the
  shipped default `base_url` is OpenRouter's and the first attempt sent the
  OpenAI key there (`HTTP 401: Missing Authentication header`). The provider
  id for a direct OpenAI key is `openai-api`, not `openai`.
- **Flags** (`hermes chat --help`): `--resume/-r <id-or-title>`,
  `--continue/-c`, `-m/--model`, `--provider`, `--yolo`, `--cli`/`--tui`,
  `--in DIR`, `--worktree`, `--toolsets`, `--skills`, `--accept-hooks`,
  `--ignore-user-config`, `--safe-mode`, `--source`, `-z` (one-shot),
  `--usage-file`. No `--session-id`, no fork flag, no tool allowlist flag.
- **Session ids:** `YYYYMMDD_HHMMSS_<hex6>` (`hermes_state_ids.
  new_session_id`), minted by Hermes. `/quit` prints `Resume this session
  with: hermes --resume <id>`.
- **Approvals:** `approvals.mode: smart` (default; an auxiliary model
  pre-screens each dangerous command), `manual`, `off` (= `--yolo`).
  Dangerous-command detection is regex-based (`tools/approval_detection.py`:
  `rm -r…`, `sudo`, `mkfs`, …). The classic-CLI dialog is a numbered box
  (`1. Allow once / 2. Allow for this session / 3. Add to permanent
  allowlist / 4. Deny`), digit + Enter.
- **Key gestures:** single `C-c` interrupts safely (idle: nothing; mid-API:
  `⚡ Interrupted during API call.`; mid-tool: tool exits 130) — a second
  `C-c` within 2s force-exits (Hermes docs). `/quit` exits cleanly. `/new`
  opens a confirmation box (`tools.slash_confirm.destructive_slash_confirm`
  defaults true); `1` approves once.
- **Hook surfaces:** Python plugins (`$HERMES_HOME/plugins/<name>/
  plugin.yaml` + `__init__.py` with `register(ctx)`, opt-in via
  `plugins.enabled`), shell hooks (`hooks:` block in config.yaml, JSON on
  stdin, consent per `(event, command)` or `HERMES_ACCEPT_HOOKS=1`), gateway
  hooks (messaging only). Observer events: `on_session_start`,
  `on_session_reset`, `on_session_finalize`, `on_session_end` (turn-scoped,
  with `completed`/`failed`/`interrupted`), `pre_llm_call`/`post_llm_call`,
  `pre_tool_call`/`post_tool_call` (with `status`), `pre_approval_request`/
  `post_approval_response` (with `surface` and `choice`), plus API-request
  and subagent observers.
- **Stats store:** `$HERMES_HOME/state.db` (SQLite, WAL). `sessions` row:
  cumulative tokens (input/output/cache_read/cache_write/reasoning),
  `model`, `model_config` (JSON with `_usage_anchor.prompt_tokens` = last
  call's prompt size), `cwd` (written at finalize), `started_at`/`ended_at`
  (epoch seconds), `estimated_cost_usd` + `cost_status` (`unknown` for
  `openai-api`), `parent_session_id` (compression splits). `messages` rows
  carry role/timestamp but no per-message usage.

## 2. Design decisions

### 2.1 Telemetry: plugin, not shell hooks

Both routes were exercised live. Shell hooks worked (with
`HERMES_ACCEPT_HOOKS=1`) but need twelve list-valued entries under `hooks:`
in the user's ~115KB commented `config.yaml`, which Hermes normalises and
rewrites on every `hermes config set` — overcode would have had to either
round-trip that file or shell out twelve times. The plugin route needs two
files under `$HERMES_HOME/plugins/overcode/` and one allow-list entry that
`hermes plugins enable overcode --no-allow-tool-override` writes itself
(the flag is what makes it non-interactive; bare `enable` prompts on
`/dev/tty` about a tool-override capability). The plugin also runs
in-process, so it can translate Hermes's vocabulary into Claude's before
piping to `overcode hook-handler` — no new dialect in `hook_handler`, no
new rows in `_HOOK_STATUS_MAP` (it emits only `SessionStart`,
`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
`PermissionRequest`, `Stop`, `StopFailure`, `Interrupt`, `SessionEnd`).
`tests/unit/test_hermes_plugin.py` pins that set against the handler's.

Filtering rules the plugin applies, all from captured payloads: the
`smart` approval surface is dropped (aux-model pre-screen, no human);
denied approvals are dropped (`post_tool_call` with `blocked` follows);
`on_session_finalize` only maps to `SessionEnd` for `shutdown`/
`keyboard_interrupt` (a `/new` finalizes with `session_boundary` then
`on_session_reset` carries the new id → `SessionStart`); `on_session_end`
with `reason: shutdown` is left to finalize. `pre_llm_call` is the one
hook whose return matters: the handler's `UserPromptSubmit` stdout comes
back as `{"context": …}` (Hermes's documented equivalent of Claude's
context injection). A budget block (exit 2) cannot stop a Hermes turn.

### 2.2 Stats: `state.db`, sampled burn-rate window

Cumulative per-session totals from `sessions` (children folded in), the
live context size from `_usage_anchor.prompt_tokens` (matches Hermes's own
status bar), CTX denominator from `model.context_length` in Hermes's config
when set else overcode's tables (#469 — they agreed at 400K for
`gpt-5-mini`). Hermes's cost figure is used only when `cost_status` isn't
`unknown` and non-zero; otherwise the daemon prices tokens itself (verified
live: `$0.0106` after the 60s stats sync). The burn-rate window is the one
compromise: no per-message usage exists, so `_WindowSampler` records totals
per daemon tick and answers "since t" as a delta — exact within a daemon's
lifetime, under-reports (never over-reports) after a daemon restart.

### 2.3 Launch grammar

`hermes --cli [--resume <id>] [-m <model>] [--yolo] [extra]`. Permissive
== normal (no flag exists below `--yolo`). No `--accept-hooks` (plugin
route). `OVERCODE_HOOK_COMMAND` (absolute overcode binary, or `python -m
overcode.cli`) is exported so the in-process plugin can find the handler
from inside Hermes's venv.

## 3. Seam generalisations (the "does anything need generalising" answer)

Four, all small, all optional-hook shaped so the other four adapters were
untouched except for gaining the same hooks:

| Seam change | Why Hermes needed it | Where |
|---|---|---|
| `process_argv_markers` on `AgentBackend`; `find_agent_process` also matches argv substrings | the pane runs `.../venv/bin/python .../hermes-agent/hermes --cli` — basename `python` is too broad to match | `backends/base.py`, `doctor.py` (`session_process_argv_markers`), `monitor_daemon.py` callers |
| `prompt_ready_line(line)` consulted by `launcher._wait_for_prompt` before the exact `prompt_ready_chars` match | the idle prompt is `❯ <rotating placeholder>`, never a bare glyph | `backends/base.py`, `launcher.py` |
| `uninstall_telemetry(project_dir) -> (ok, message)`; `overcode hooks uninstall-backend` dispatches generically | the fourth `if backend == …` branch in `cli/hooks.py`; each adapter now owns its footprint removal (messages preserved verbatim, tests unchanged) | `cli/hooks.py`, all five adapters |
| `doctor_findings() -> list[str]`; `overcode doctor` loops over backends present in the fleet | the fourth copy-pasted version-findings block in `cli/doctor.py`; adapters resolve their module-level `version_findings` at call time so existing test patches still apply | `cli/doctor.py`, opencode/codex/grok/hermes adapters |

Plus the devcontainer wrapper: the hooks-install guard became `[[
"$AGENT_BACKEND" == "claude-code" ]]` (an equality on the one backend with
a settings.json protocol, instead of an exclusion list), and the curl
installers share one pipe reading a per-backend `AGENT_INSTALL_URL`.

Not generalised, deliberately: `BACKEND_BADGES` (a two-letter badge is a
human choice), the CLI help strings (prose), and `hook_handler`'s dialect
sniffing — the finding here is the opposite: put the dialect on the adapter
side whenever the CLI gives you a place to run code, as opencode and now
hermes do.

## 4. Live verification log (2026-09-17) [VERIFIED]

Through `overcode` in an isolated state dir + tmux socket, real Hermes,
`approvals.mode: manual` to force the dialog:

| Step | Result |
|---|---|
| `overcode launch -n hm1 -B hermes -d … -p "Reply with the single word: ready"` | launched; plugin installed to `~/.hermes/plugins/overcode/` (marker present), already enabled; `hook_state` = `SessionStart` → … → `Stop` with the Hermes id recorded; daemon `waiting_user` |
| dangerous-command prompt | daemon `waiting_approval` (hook `PermissionRequest`, tool `Bash`) within 15s |
| `overcode send hm1 approve` | `1` + Enter taken, directory removed, back to `waiting_user` in 4s |
| second dangerous prompt + `overcode send hm1 reject` | `4` + Enter taken, `[You denied this command — it did not run.]`, directory intact, `waiting_user` |
| `overcode list` / `show` | TOK Σ 12.0K → 16.3K, CTX 3% (of 400K), Backend `hermes`, work median 7s; cost `$0.00` until the daemon's 60s stats sync, then `$0.0106` with model `gpt-5-mini` |
| `overcode doctor` | `✓ ok — hermes process running, telemetry plugin installed and enabled`, PID found via the argv marker; one data-quality note (`cost_zero`, the pre-sync window) |
| `overcode restart hm1` | graceful `C-c` + `/quit`, relaunched with `--resume <id>`; `state.db` shows one session row whose message count grew across the restart (12 messages) |
| `overcode kill hm1` | clean; `No running agents` |

By hand (isolated tmux server, `tests/fixtures_hermes_panes/`): idle, busy
(API and tool), approval approve/deny, `C-c` idle/mid-API/mid-tool, `/`
menu, `/new` confirm, `/quit`, `--resume`, `--yolo` (no dialog).

## 5. Known limits / follow-ups

- **Fork.** Hermes has `/branch` in-session only. A future fork could be
  `--resume <id>` then `/branch` once the prompt is up, with the plugin's
  `on_session_reset` reporting the new id — not built; `FORK` undeclared.
- **Node TUI unverified.** `--cli` is forced.
- **Container story unverified** (no live docker run); plugin and provider
  config live under the host's `$HERMES_HOME`.
- **Burn-rate window resets on daemon restart** (§2.2).
- **`hermes plugins enable` is a ~2s Hermes boot** — once per install, only
  while `plugins.enabled` lacks the entry; unit-tested with `subprocess.run`
  patched, e2e-mocked by `tests/mock_hermes.py`'s `plugins enable` handler.
- **Version range** `>=0.21.0, <0.22.0`; `overcode doctor` warns outside it.
