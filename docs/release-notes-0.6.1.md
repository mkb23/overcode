# Overcode 0.6.1 Release Notes

The first published 0.6 release: it includes everything in the [0.6.0 notes](release-notes-0.6.0.md) (never released on its own), plus:

- **Spend fixed.** Claude spend was over-counted 1.3–2× (0.5.x too): each message now counts once, forks don't recount copied history, and archived agents are restated once with a ledger correction. Prices come from a dated, sourced table (long-context tiers, Bedrock regional premiums, 1-hour cache writes); `overcode models refresh --prices` updates it on air-gapped hosts, and `doctor` checks spend against Claude Code's own.
- **Delegated work you can see.** A 👥 SubAgents column, → / ← to expand a parent's subagents into rows, 🌙 rows for background sessions, and `d` for the agent visibility dialog. A parent stays yellow while its subagents run.
- **Subscription quota.** A Q% column and quota mode for Max/Pro plans, from usage the engine polls, with an opt-in calibration statusline.
- **Children presets.** `launch --preset supervised|balanced|autonomous`, `--archive-after` and `--normal-permissions`; presets live in `config.yaml`.
- **TUI.** MOD shows auto and plan mode, the timeline gains swarm rows, and the highlighted agent stays put when the view changes.
- **Safer.** Scripts run inside an agent are refused the real state dir; hermes no longer leaves its launch prompt unsent.
