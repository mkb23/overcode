# Overcode 0.6.1 Release Notes

The first published 0.6 release: it includes everything in the [0.6.0 notes](release-notes-0.6.0.md) (never released on its own), plus:

- **Spend fixed and billable.** Claude spend was over-counted 1.3–2× (0.5.x too); now each message counts once, from a dated price table (`models refresh --prices`). Billing codes are ids with renamable names: `m` lists every code's spend, renames, merges (`M`) and collapses codes into a default; new agents take their code from their name, folder or `billing.default_code`. A trip counter (`↺`) zeroes an agent's row spend while totals keep counting.
- **Agents name themselves (#542, opt-in).** With `naming.enabled`, the daemon names an agent once its task is clear, following your advice and the rest of the fleet; a Naming lab tries advice on the whole fleet. Rename no longer restarts an agent, and Claude's own session name follows overcode's.
- **Delegated work you can see.** A 👥 SubAgents column, 🌙 background sessions, and `←` / `→` / `X` that fold every kind of row as one tree. An agent whose conversation moves into Claude's background worker stays that agent.
- **Status colours your way.** Themes (`classic`, `calm`, `colorblind`, `shapes`) or your own glyphs; questions count as approval; idle agents go 📥 → 😴 and, if you choose, to sleep.
- **Subscription quota.** A Q% column and a %/h burn for Max/Pro plans, from usage the engine polls.
- **Children presets and safety.** `launch --preset supervised|balanced|autonomous`; a parent's child names are checked against the naming policy (`--force-name`); rename and context notices reach opencode models (#533); scripts inside an agent are refused the real state dir.
