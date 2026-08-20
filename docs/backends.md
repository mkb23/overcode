# Agent Backends

Overcode was built around Claude Code, but the pieces that know *which* CLI
is running now live behind one seam — an `AgentBackend` adapter that owns
that CLI's argv grammar, key gestures, pane chrome, and telemetry. Two
backends ship today:

| Backend name | CLI | Binary override |
|---|---|---|
| `claude-code` (default) | [Claude Code](https://claude.ai/claude-code) | `CLAUDE_COMMAND` |
| `opencode` | [opencode](https://opencode.ai) | `OPENCODE_COMMAND` |

Everything below opencode's row is honest about maturity: opencode support
is an **observability-lite tier**. You get the dashboard, live status,
previews, AI summaries, send-instruction, restart, kill, resume and fork.
You do not yet get token/cost columns or hook-grade status detail.

---

## Launching an opencode agent

```bash
overcode launch -n my-agent --backend opencode -d ~/code/myproject
overcode launch -n my-agent -B opencode --model openai/gpt-4o-mini
```

`-B` is the short form (`-b` was already `--budget`).

In the TUI, press `n` for the new-agent modal — there is a **Backend**
toggle alongside Provider. From the CLI, `overcode show <name>` prints the
backend, and a **BKD** column appears in the dashboard **only** when the
fleet actually spans more than one backend, so a Claude-only user sees no
change.

To make opencode the default for new agents:

```yaml
# ~/.overcode/config.yaml
new_agent_defaults:
  backend: opencode
```

Children inherit their parent's backend unless you pass `--backend` or
`--no-inherit`.

### Models

opencode expects the fully-qualified `provider/model` form and overcode
passes `--model` through verbatim:

```bash
overcode launch -n oc -B opencode --model openai/gpt-4o-mini
overcode launch -n oc -B opencode --model anthropic/claude-sonnet-4-5
```

A bare model name (`sonnet`) will not resolve — that is Claude Code's
grammar, not opencode's.

---

## Support matrix

Capabilities are the `BackendCapability` flags each adapter declares;
overcode gates UI actions and telemetry off them.

| Capability | claude-code | opencode | Notes |
|---|---|---|---|
| `RESUME` | ✅ | ✅ | opencode: `--session <id>` |
| `FORK` | ✅ | ✅ | opencode: `--session <id> --fork` — **verified**, creates a `(fork #1)` session |
| `SESSION_ID_PRESCRIPTION` | ✅ | ❌ | opencode mints its own `ses_…` ids; overcode must discover, not prescribe |
| `HOOK_EVENTS` | ✅ | ❌ | Phase 5 (bundled opencode plugin). Until then: pane polling |
| `TRANSCRIPT_STATS` | ✅ | ❌ | Phase 5 (SQLite `session` table). Until then: dashes, never zeros |
| `PERMISSION_INJECTION` | ✅ | ❌ | opencode v1.18.19 has no per-launch tool allowlist flag |
| `SKILLS` | ✅ | ❌ | opencode *does* have a `/skills` command, but no overcode integration |
| `SANDBOX_PROBE` | ✅ | ❌ | Claude-only loopback heuristic |
| `SUBSCRIPTION_USAGE` | ✅ | ❌ | Anthropic-only usage API |
| `AGENT_TEAMS` | ✅ | ❌ | Claude Code experimental feature |

## Flag mapping

| overcode concept | claude-code | opencode (v1.18.19) |
|---|---|---|
| Binary | `claude` | `opencode` |
| Bypass permissions | `--dangerously-skip-permissions` | `--auto` |
| Permissive | `--permission-mode dontAsk` | `--auto` (approximate — see below) |
| Allowed tools | `--allowedTools a,b` | ✗ no flag exists |
| Model | `--model sonnet` | `--model provider/model` |
| Persona | `--agent name` | `--agent name` |
| Prescribe session id | `--session-id <uuid>` | ✗ |
| Resume | `--resume <id>` | `--session <id>` |
| Fork | `--resume <id> --fork-session` | `--session <id> --fork` |
| Telemetry injection | `--settings '<json>'` hooks | ✗ (Phase 5 plugin) |
| Stats source | `~/.claude/projects/**.jsonl` | SQLite `~/.local/share/opencode/opencode.db` (unread today) |
| Graceful exit | `C-c`, then `/exit` | `Escape` ×2, then `/exit` |
| Clear conversation | `/clear` | `/new` |
| Approve | `Enter` | `Enter` (confirms the preselected *Allow once*) |
| Reject | `Escape` | `Escape` |
| Trust-folder dialog | "I trust this folder" | none |

### Permission modes are approximate

overcode has three modes; opencode has one flag.

- **normal** → no flag. opencode asks according to its own `permission`
  config in `opencode.json` / `~/.config/opencode/opencode.jsonc`.
- **permissive** → `--auto`.
- **bypass** → `--auto`.

`--auto` auto-approves anything not explicitly *denied*, so opencode's
`"deny"` rules still win — it is closer to Claude's `dontAsk` than to
`--dangerously-skip-permissions`. Both overcode modes collapse onto it,
which means **permissive and bypass behave identically on opencode**. If
you need finer control, put it in the project's `opencode.json`:

```json
{ "permission": { "bash": "ask", "edit": "allow" } }
```

`--allowed-tools` is silently ignored for opencode: the `--permissions`
flag the design research expected does not exist in v1.18.19, and emitting
it would fail the launch outright.

---

## Status detection

opencode has no hook channel yet, so status comes from **pane polling** —
overcode reads the rendered TUI. The pattern set lives in
`src/overcode/backends/opencode.py` (`OPENCODE_PATTERNS`) and is grounded in
a committed corpus of real captures at
`tests/fixtures_opencode_panes/`, replayed by
`tests/unit/test_status_detector_opencode.py`.

The signals that matter:

| overcode status | opencode chrome |
|---|---|
| `running` | `esc interrupt` (or `esc again to interrupt`) in the bottom bar, plus an animating `⬝ / ■` spinner |
| `waiting_user` (permission) | `△ Permission required` box with `Allow once / Allow always / Reject` |
| `waiting_user` (idle) | input box `┃` gutter with no interrupt hint; hint bar reads `tab agents  ctrl+p commands` |
| `terminated` | shell prompt, none of the above |

Known rough edges, honestly:

- **Errors read as `waiting_user`, not `error`.** opencode renders provider
  failures as plain prose in a red-bordered box using the same `┃` gutter as
  everything else, and overcode strips ANSI before matching — the colour is
  the only structural signal and it is gone by then. A short list of known
  message texts (`Incorrect API key provided`, `ECONNREFUSED`, …) is matched,
  but the general case degrades to "stopped, needs you".
- **The pristine banner screen reports a vague activity string.** On a fresh
  launch opencode centres its input box with blank filler beneath, so the
  bottom-of-pane slice the detector reads contains only the tip line and the
  info bar. The *status* is right (`waiting_user`); the one-line activity
  text is just less useful until the first turn.
- **Finished tool calls are not treated as work.** `→ Read README.md` and
  `✱ Glob "*"` stay on screen after a turn ends, so matching them would
  report a settled agent as running. Tool-execution detection is therefore
  disabled for opencode; `esc interrupt` covers the in-flight case.
- **UNVERIFIED: thinking/reasoning chrome.** No reasoning-capable model was
  driven during corpus capture, so the thinking markers are empty rather
  than guessed.
- **UNVERIFIED: post-interrupt pane.** The prompt opencode shows after an
  actual interrupt was not captured; that field feeds the hook detector,
  which opencode does not use yet.

---

## Doctor checks

`overcode doctor` adds two opencode-specific checks, and only when the
fleet actually contains an opencode agent:

1. **Version range.** opencode ships every 2-3 days and overcode reads its
   on-screen chrome, so a version outside the tested range earns a warning.
   The range lives in `TESTED_OPENCODE_RANGE` in
   `src/overcode/backends/opencode.py` (currently `>=1.18.0, <2.0.0`).
2. **Autoupdate.** If `~/.config/opencode/opencode.json[c]` has
   `"autoupdate": true`, overcode warns — an unattended upgrade can move the
   TUI out from under the pattern set. Silent when there is no config or it
   doesn't mention the setting.

Per-agent, the health verdict for an opencode session is simply "is there a
live `opencode` process under the pane?". There is no `--settings` analogue
to inspect; once the Phase 5 plugin lands, "plugin loaded" becomes the real
check.

---

## What is coming (Phase 5)

- A bundled opencode plugin translating the bus events (`session.idle`,
  `permission.asked`, `tool.execute.*`, `session.error`) into overcode's
  existing hook-state files — which buys hook-grade status, obligation
  badges, and the status-detail column for free.
- An `OpencodeStatsReader` over the SQLite `session` table for token, cost,
  and context columns, which in turn unlocks budgets.
- Supervisor recipes for opencode's permission dialog.

Useful detail already observed for that work: opencode prints
`Continue  opencode -s ses_…` in its farewell block on `/exit`, which is the
cheapest place to learn a session ID without touching SQLite.

---

## Adding a third backend

Implement the `AgentBackend` protocol in `src/overcode/backends/base.py`,
register it in `src/overcode/backends/__init__.py`, and supply a
`StatusPatterns` instance built from a captured pane corpus. Declare only
the capabilities you actually support — every gated feature degrades to a
dash or a clean "backend X does not support fork" rather than a crash.
