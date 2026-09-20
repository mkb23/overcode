# Overcode 0.5.3 Release Notes

0.5.3 brings two new agent backends. `overcode launch -B hermes` runs
NousResearch's Hermes Agent (#475) and `-B opencode2` runs the OpenCode 2.0
preview, each with launch, resume, restart, kill, bypass mode, approve/reject
gestures, hook-grade live status including `waiting_approval`, and the
token/cost/context columns fed by the CLI's own session store. Both were
verified end to end against the real CLIs (Hermes v0.21.3, opencode2
`v0.0.0-dev-19272` and `dev-19742`) driven through overcode itself, and
each ships a captured pane corpus so its status patterns are replayed in the
unit suite. That makes six supported CLIs: Claude Code, opencode, opencode2,
Codex, Grok and Hermes. The backend seam grew four optional hooks along the
way (`process_argv_markers`, `prompt_ready_line`, `uninstall_telemetry`,
`doctor_findings`), so `overcode hooks uninstall-backend` and `overcode
doctor` now dispatch to the adapter instead of carrying per-backend branches.

Live testing against current CLI builds turned up three startup problems that
are fixed here. Claude Code 2.1.27x redrew its workspace-trust dialog with
"No, exit" preselected, so the launcher's bare Enter left the dialog up
forever; it now moves to the trust line first. Codex 0.153.4 draws an
"Update available!" prompt ahead of its trust dialog whose preselected option
runs `npm install -g`, which a bare Enter or a supervisor's `approve` would
have started under the agent; the launcher now picks "Skip" by digit. And the
opencode2 curl installer ships `opencode2` as a shell wrapper that execs
`opencode`, so a healthy preview agent reported "no opencode2 process under
pane" and `doctor` printed a doubled binary name; the backend now matches
both process names and strips either version prefix.

Testing itself gained two tiers. `make test-matrix` drives every backend
through launch, status, permission gestures, restart and kill against the
mock CLIs in a real tmux with no credentials, and `make test-live` runs the
same flow against whichever real CLIs you name in `OVERCODE_LIVE_BACKENDS`
(it spends real tokens and is opt-in). The e2e harness now isolates
`GROK_HOME` alongside `HERMES_HOME`, so no test writes into your real
hooks files, and the four tiers are written up under "How each backend is
tested" in docs/backends.md. A TUI unit test that raced the sessions
container's mount is also fixed: the #286 guard now catches the `NoMatches`
form of that race as well as `MountError`.

The opt-in model-metadata auto-refresh no longer runs inside the daemon's
monitor loop (#473). On a network that silently drops packets the old
synchronous fetch stalled status detection for a minute every hour; it now
runs on a background thread with a 15 s timeout and backs off for six hours
after a failure.

## Follow-on fixes from the 0.5.2 cycle

These landed on main after the 0.5.2 release was cut and ship for the first
time in 0.5.3.

**TUI selection stays on the agent when the list re-orders (#471).** In the
status and value sort modes the highlight used to stay on the *row* while a
different agent slid into it, so the TUI and the synced tmux pane drifted
apart until you tapped `j`/`k`. Selection is now anchored to the agent id
across every re-sort, including the periodic refresh.

**Folds survive restarts (#464).** Parents collapsed with `X` in tree mode
are persisted per session and restored when the TUI comes back.

**Model metadata for the long tail (#473).** A transcoded models.dev
catalog (~1,000 text models) now backs the context-window and pricing
lookups behind the curated tables, so GLM, Kimi, DeepSeek, Qwen, MiniMax and
friends get a real `CTX%` and `$` instead of a dash. Lookups use the
freshest copy on the machine — `overcode models refresh` writes one under
`~/.overcode/cache`, opencode's own cache is read when present — with a
bundled snapshot as the offline fallback. No unattended network by
default; `model_metadata.auto_refresh: true` opts the daemon in. New
`overcode models info|lookup|refresh` commands, and `doctor` nudges when
the catalog is over 90 days old.

**opencode `CTX%` matches opencode's console (#469).** opencode agents now
divide by the `limit.context` in opencode's own cached models.dev catalog —
the same figure its "N% used" uses — with the bundled snapshot as fallback.
The per-backend rules are written up in docs/backends.md.

**Default backend surfaced in `config init` / `config show` (#470).** The
`new_agent_defaults.backend` key (already honoured by `overcode launch`, the
`n` modal and the `G` defaults modal) is now in the generated template and
the `config show` output.

**History rotation streams (#468, #465).** `agent_status_history.csv`
rotation no longer loads the whole file to rotate it — a legacy 2 GB file
is archived in one streaming pass instead of being materialised in the
daemon's memory. The opt-in `status_changes.csv` diagnostic gets the same
hard cap as `event_loop_timing.csv`.
