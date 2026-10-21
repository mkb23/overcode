# Overcode 0.6.1 Release Notes

**In short:** overcode is faster, calmer and more honest. Status lights mean what they say, spend is counted correctly and can be billed to projects, agents can name themselves, and you can finally see the work your agents hand off to others. It is a big upgrade, and it converts your stored data on first run: **back up `~/.overcode` before you upgrade** ([why](#upgrading-read-this-first)).

0.6.1 is the first published 0.6 release. It includes everything built for 0.6.0, which was never released on its own (its notes are [here](release-notes-0.6.0.md)), and a second round of work on top.

## One source of truth for what your agents are doing

**The problem.** In 0.5, two programs kept watch over your agents: the dashboard you look at (the TUI) and a monitor running in the background. Each worked out for itself whether every agent was busy, waiting for you or finished. Because they looked separately, they sometimes disagreed: a light could show an agent working while the history recorded it as idle, and two open dashboards could show different things. Doing the same work twice also made the dashboard slow and kept your CPU busy, even when nobody was looking at it.

**The fix.** Now only the background monitor works things out. It keeps every agent's status, spend, git state and history, and sends each change to the dashboard, which just displays it. So what you see is what was recorded, every dashboard shows the same thing, changes appear within half a second, and when nobody is watching, the monitor drops to almost no CPU while still keeping the record.

**Also simpler.** `overcode` now always opens one screen: your agent list on top, the selected agent's own terminal below. The old standalone dashboard, the web dashboard and the Cloudflare relay are gone, taking about 7,000 lines of code with them. The API server stays, for sharing agents between machines.

## Status you can trust, in your colours

Each agent's light now means one thing. ❔ shows while an agent starts, ⚪ marks a child that has finished, 🟡 a parent waiting on its children, and a Result column (📭 ✅ ❌ ⌛) says how each child ended. Short blips are merged so the bell rings once per real stall, and questions an agent asks you count as approvals. An idle agent goes from 📥 to a dim 😴 after an hour, and can be put to sleep automatically if you choose. If the default colours do not suit you, pick a theme (`classic`, `calm`, `colorblind` or `shapes`) or set your own glyphs from the palette. `?` shows the legend.

## Spend you can bill

Claude spend was over-counted by 1.3 to 2 times in 0.5 and early 0.6, because messages were counted more than once. Each message now counts exactly once, priced from a dated price table (`overcode models refresh` updates it; `--prices FILE` rebuilds it offline from an AWS price list). Historical figures are restated on upgrade, so expect your totals to go down (see below).

Every agent now has a billing code, with spend recorded in a ledger that is kept forever. Codes are ids with names you can change: `m` opens a dialog listing every code with its spend, where you can rename codes, merge several into one (`M`) or fold stray codes into a default. New agents take their code from their name, their folder or `billing.default_code`, and children inherit their parent's. `overcode billing` reports spend by code from the command line. A trip counter (`↺`) zeroes an agent's row spend for a fresh measurement while the totals keep counting. If you are on a Max or Pro plan, the Q% column shows each agent's estimated share of your weekly and 5-hour quota, with a burn rate in percent per hour.

## Agents that name themselves

Agents can now be renamed without restarting them: the tmux window, the record and Claude's own session title all follow, the old name keeps working as an alias, and the agent and its parent are told about the change with their next prompt. A trailing `*` on a name means Claude's title has not caught up yet. That usually lasts a moment, while the agent is busy or you have half-typed text in its input.

On top of that, overcode can name agents for you. Turn on **Auto-naming** from the palette (`/` → "Auto-naming"), and any agent whose name you left at the default is given a name five minutes after its first prompt, based on its task and your naming advice. Names overcode chose are marked with `~`. Names you or a parent agent chose are never changed. The advice is free text in `config.yaml` or a repo's `.overcode/naming.md`, and it decides the style, case included. The **Naming lab** (`/` → "Naming lab…") lets you edit that advice and see live what it would call every agent in the fleet, then apply the picks one at a time or all at once. Auto-naming calls the model configured under `summarizer:` (OpenAI by default) and is capped at $0.50 a day unless you change `naming.cost_cap_per_day`.

## Delegated work you can see

When an agent hands work to subagents, workflows or background sessions, that work is now visible: a SubAgents column counts it, 🌙 marks Claude background sessions, and `←`, `→` and `X` fold and unfold every kind of row as one tree. An agent whose conversation Claude moves into its background worker (through Remote Control or `/background`) is still recognised as the same agent. `g` launches a new agent in a fresh git worktree of its own, so parallel work does not collide. When agents launch children, presets (`launch --preset supervised|balanced|autonomous`) set how much the child may do unattended, and the names parents give their children are checked against your naming policy.

## Backends

codex, grok, opencode and hermes stats now read only what changed, which keeps large fleets fast. opencode is fully supported, and rename and context notices reach opencode models. Claude receives its launch prompt on the command line rather than as pasted text it would ignore. Energy is now estimated in watts from token counts, configurable under `energy:`.

## Upgrading: read this first

The first time you run `overcode` after upgrading, it lists what will change, suggests a backup and asks before converting anything; answering no leaves your data untouched (`overcode tmux --yes` converts without asking). Until you agree, the daemon will not start. Once you do, it converts the state 0.5 left in `~/.overcode` (or `$OVERCODE_DIR` / `$OVERCODE_STATE_DIR`). It works in batches and picks up where it left off if interrupted. An upgrade test runs it over a model of a default 0.5.8 installation, but **it makes no backup of its own**, and several steps are one-way:

- **The old archive is replaced.** Archived agents move from `archive.json` / `archive.jsonl` into compact monthly files, and the old files are then deleted. Only the fields 0.6 uses are kept: archived agents lose their stored launch command, extra CLI arguments, standing-instruction text and conversation ids. A damaged line in the old archive is skipped and lost with it.
- **Old per-agent files are packed.** Hook state, hook logs and the other files 0.5 kept per agent name are moved into compressed day bundles and the originals removed. Only the last 64 KiB of each file is kept (256 KiB for logs). 0.5 rotated its logs well below that, so little should be lost, but very large files are cut. An agent that is still running from before the upgrade keeps its files until it is restarted.
- **Historical spend is rewritten.** Archived agents' spend is recalculated with the corrected counting and written back in place, and live agents are restated on their next sync. The old figures survive only as correction entries in the ledger. Totals will drop, which is intended.
- **Finished jobs older than 24 hours are removed** from the job list on first start. Set `jobs.retention_hours` beforehand if you want to keep more.
- **Going back to 0.5.8 is possible but lossy.** Your live agents still load in 0.5.8, but it cannot read the new archive, ledger or bundles, so it will show no history. Upgrading again later picks up anything 0.5.8 archived in the meantime.

**Before upgrading:** stop the 0.5 TUI and daemons, then copy your state directory, for example `cp -a ~/.overcode ~/.overcode.bak-0.5.8`. That copy is the only way to recover the old archive, the original spend figures or job history if you need them later.
