# Overcode 0.6.0 Release Notes

0.6.0 adds two new agent backends — OpenAI's Codex CLI and xAI's Grok Build — bringing overcode's supported CLIs to four: Claude Code, opencode, Codex, and Grok. It also closes out a piece of unfinished business from opencode support: bypass mode now genuinely bypasses opencode's permission rules instead of quietly behaving like permissive mode.

## Codex CLI support

Launch a codex agent with `overcode launch -n my-agent --backend codex` (or `-B codex`). Everything you'd expect from a full backend works: launch, restart, revive, resume (`codex resume <id>`), fork (`codex fork <id>`), kill, send-instruction, and hooks-grade live status — including the `waiting_approval` distinction pane polling alone can't make. Every launch injects `overcode hook-handler` via per-launch `-c 'hooks.<Event>=[...]'` config overrides plus `--dangerously-bypass-hook-trust`, so nothing is written to your project or to `~/.codex/config.toml`.

Token and context columns read codex's own rollout JSONL (`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`) accurately. Cost is a **list-price estimate**, not a billed figure — codex is subscription/API billed with no local per-turn charge to compare against, but `pricing.py` now carries an entry for `gpt-5.6-sol` (codex's account-default model), sourced from OpenAI's own pricing docs, so the estimate uses codex's real published rate instead of falling back to your configured default model's rate.

Two gestures worth knowing if you supervise a codex agent by hand: bare `C-c` **kills codex instantly, with no confirmation** — never send it; the safe interrupt is a single `Escape`. Clear-conversation is `/new`, not `/clear`; graceful exit is `/quit` or `/exit`.

## Grok Build support

Launch a grok agent with `overcode launch -n my-agent --backend grok` (or `-B grok`). Same launch/restart/resume/fork/kill/hooks-grade-status story as codex, plus two capabilities unique to grok among overcode's non-Claude backends: **session-id prescription** (overcode mints the uuid and grok lands the session at a predictable path) and a **permission allowlist** (`--allowed-tools` becomes repeated `--allow <rule>` flags that actually suppress the approval dialog). Grok is also the only backend where forking mints a brand-new prescribed session id rather than reusing one the CLI generates itself.

Grok's token/cost/context columns are the most complete of any non-Claude backend: `GrokStatsReader` reads a full, genuinely billing-accurate split from `updates.jsonl`'s `turn_completed.usage` — not an estimate. Two things had to be corrected empirically before that reader could be trusted: usage objects are per-turn, not cumulative, so overcode sums them rather than taking the latest one; and `costUsdTicks` is nano-dollars (1e9 per USD), not the coarser scale an early sample hadn't ruled out.

Grok requires a SuperGrok or X Premium+ subscription and a one-time `grok login` outside overcode. If `~/.grok/auth.json` is missing or empty, `overcode doctor` now names `grok login` explicitly instead of letting the launch fail with no explanation.

Bare `C-c` is **safe** on grok (interrupts only, session stays alive) — the opposite of codex and opencode. Clear-conversation is `/new`; approve/reject are bare digit keys (`2`/`3`, no Enter) — the default-selected dialog option is *always-approve*, not a one-time approval, so `overcode send approve` deliberately sends `2`, never a bare Enter.

## opencode: bypass mode is a real bypass now

Previously, overcode's `permissive` and `bypass` modes both mapped onto opencode's single `--auto` flag, and `--auto` leaves opencode's own `"deny"` rules in force — so bypass mode quietly behaved like permissive, not like Claude's `--dangerously-skip-permissions`. As of 0.6.0, **bypass** mode additionally sets `OPENCODE_PERMISSION` to an allow-everything JSON blob, which is merged into opencode's resolved config *after* project config and genuinely overrides `"deny"` rules — live-verified via `opencode debug config`. **permissive** mode is unchanged (`--auto` alone, deny rules still win). No file is written; the variable is process-scoped to the launched opencode process only.

## Devcontainer support for codex and grok

`--wrapper devcontainer` now recognizes `codex` and `grok` in `OVERCODE_BACKEND`. codex installs the same way Claude Code does (`npm i -g @openai/codex`); grok has no npm package and uses x.ai's own curl installer instead. `XAI_API_KEY` is now forwarded alongside the existing provider-credential list.

## Honest gaps

- **Codex's cost column is an estimate, not a bill.** codex has no local per-turn charge recorded anywhere, so even with the new `gpt-5.6-sol` pricing entry, the cost column is always a computed estimate — accurate only insofar as the entry's list price matches what you're actually billed (promotional pricing OpenAI has said runs through ~Nov 21, 2026; tiers other than standard/short-context aren't modelled).
- **Grok requires a subscription overcode cannot provision for you.** SuperGrok or X Premium+, plus a one-time `grok login` outside overcode. CI and most other machines will not have this — all of overcode's own tests run against a mock grok CLI.
- **Grok's devcontainer auth story is unverified, not confirmed unsupported.** No live docker build was run as part of this release (out of scope by design); `XAI_API_KEY` is forwarded, but whether grok's interactive subscription-login flow works from inside a container's tmux pane has not been tested.
- **Tested version ranges**: codex `>=0.148.0, <1.0.0` (ships multiple releases a week; `overcode doctor` warns outside this range and always flags codex's enabled-by-default in-app updates). grok `>=1.0.5, <2.0.0` (no fast-churning release cadence found, but the guardrail still applies as the corpus ages). opencode's tested range is unchanged at `>=1.18.0, <2.0.0`; the bypass-mode `OPENCODE_PERMISSION` verification specifically was re-run live against v1.18.23.

## Smaller changes

- `docs/backends.md`'s permission-modes section now describes opencode's bypass/permissive split honestly instead of noting they "collapse."
- A cross-backend sweep test (`tests/unit/test_cross_backend_sweep.py`) asserts the backend registry, BKD badges, the new-agent modal, and `overcode doctor` all handle a fleet mixing all four backends without a crash.
- `docs/architecture.md`'s Agent Backends section, `docs/README.md`, and the top-level `README.md` now name all four backends instead of two.
