# Child Agents

An overcode agent can launch other overcode agents: its children. A child
is a full agent in its own tmux window. You can watch it, step into it and
send it instructions like any other. The parent gets back a result it can
act on, and overcode tracks the child's spend, permissions and oversight as
part of the parent's tree.

This page covers the whole model: the tree, the lights and the Result
column, reporting, oversight policies and presets, budgets and billing, and
what a child inherits. It ends with when to use a child instead of Claude's
own subagents or background sessions.

## The tree

```bash
# From inside an agent: the caller becomes the parent
overcode launch -n fix-jwt-refresh -d ~/project --follow -p "<prompt>"

# From anywhere: name the parent
overcode launch -n fix-jwt-refresh --parent api-work -p "<prompt>"
```

- A launch run inside an agent (`OVERCODE_SESSION_NAME` is set) makes a
  child of that agent. `--parent <name>` names the parent explicitly. Only
  the overagent can use `--no-parent` from inside an agent, for agents you
  will drive yourself.
- A name a parent gives its child is checked against the naming policy
  (`overcode name policy`): a refused name exits 3 with the rule, the
  advice and a suggestion, and `--force-name` launches it anyway unless
  another agent has it. overcode never renames a name a parent chose. See
  [Naming](configuration.md#naming).
- Trees go at most 5 levels deep.
- `overcode list <parent>` shows a parent and its children.
  `overcode kill <parent>` kills its descendants too, deepest first;
  `--no-cascade` leaves them running as orphans.
- In the TUI, the Tree sort (`S`, then Tree) draws children under their
  parent with `├─`/`└─`. `X` folds a parent's children, and the fold is
  remembered. The Agent visibility dialog (`d`) shows or hides done children. Every other sort also keeps
  children grouped under their parent.

### Kinds of row

Your agents aren't the only rows in the dashboard. Each row's kind shows
as a marker, and the KIND column (hidden by default; switch it on with `C`)
spells it out:

| Marker | KIND | What it is | What you can do |
|--------|------|------------|-----------------|
| (none) | `agent` | An overcode agent you launched | Everything |
| (none) | `child` | An overcode agent another agent launched | Everything |
| 🌙 | `bg` | A Claude background session (`claude --bg`, `/bg`) nested under the agent it came from | Claude's own actions: attach (`Enter`), stop (`x`), remove |
| 👥 | | A Claude subagent, fork or teammate working inside an agent's session | Read only |
| 🧩 | | A Claude workflow run inside an agent's session | Read only |

👥 and 🧩 rows aren't shown until you expand them. The SubAgents column
(`SUBA`) rolls them up on the parent's row by state, e.g. `▸👥 ▶15 🟠1
✅26 ❌1 🧩1` (hover it for the total). To look inside:

- `→` on the parent's row opens its delegated work. Groups come first, and
  a workflow shows its phases. Press `→` again to go one level deeper: it
  opens a group or phase, or on a member it opens a detail line (model,
  type, start time, tokens, cost, last tool, result).
- `←` closes whatever the highlight is on, or goes up a level. On the
  agent's own row it collapses everything.
- `j`/`k` move through the rows. The pane below stays on the parent,
  because a virtual row has no terminal of its own. `Enter` goes to the
  parent's pane, where you answer a subagent's permission prompt.

Details: [TUI Guide: Delegated work](tui-guide.md#delegated-work--) and
[Claude background sessions](tui-guide.md#claude-background-sessions-).

## Lights

A child's light means what any agent's light means, with one addition: a
child that has stopped is **at rest** (⚪). It isn't waiting for you; it
has finished its turn and is waiting on its parent.

| Light | On a child | On a parent |
|-------|------------|-------------|
| 🟢 | Working | Working |
| 🟡 | Waiting on its own background work | Its turn ended but its children (or its background subagents, workflows or 🌙 sessions) are still working: "Waiting on 2 children". They will wake it |
| 🟠 | A permission prompt or plan waits for approval: yours, or its parent's (`overcode send <child> approve`) | The same, in its own pane (a 👥 subagent's prompt shows here too) |
| 🔴 | Rarely seen: once a child's turn has ended, overcode settles it to ⚪ | Its turn ended, nothing it delegated is still working, and it waits for you |
| ⚪ | Stopped, reported or not: see its result (`l`) | Only when it is itself a child |

A child waiting on approval still counts as working for its parent, so the
parent stays 🟡 rather than 🔴 until every child is at rest.

## Results

How a child's task went is not in its light: in the TUI, press `l` until
the summary shows **Result**; `overcode list` prints it after the activity.

| Result | Meaning |
|--------|---------|
| 🏁 (no result) | Still working, or has never stopped |
| 📭 | Stopped without a report (`waiting_oversight`) |
| ✅ | Reported success, with its reason |
| ❌ | Reported failure, with its reason |
| ⌛ | Its oversight timeout expired before it reported |

The reason is shown as far as the row has room; `overcode list` prints it
in full. The 👶 column on the parent rolls its children up, e.g.
`👶 5 ▶2 📭1 ✅1 ❌1` (▶ means still working, and a ⌛ counts as a
failure). It reads like the SubAgents cell, glyph then count, but keeps the
total in front: a child can be working again after it reported, and one
waiting on you has no result yet, so the states don't add up to it.

### `overcode report`

A child signals that it has finished by running:

```bash
overcode report --status success --reason "All 214 tests pass"
overcode report --status failure --reason "Build needs a newer protoc"
```

The command reads the child's identity from its environment (overcode sets
`OVERCODE_SESSION_NAME` and `OVERCODE_TMUX_SESSION` in every agent it
launches), so it only works inside an agent. When the child's turn ends:

- **with a report**, it is `done`: ⚪ with ✅ or ❌. Any budget it didn't
  use goes back to its parent.
- **without a report**, it is `waiting_oversight`: ⚪ with 📭. Its
  oversight policy (below) decides what happens next. If you or its parent
  prompt it again, it goes back to work.

**End every child's prompt with the report line.** A child that never
reports is never counted as done:

```
When finished, run: overcode report --status success --reason "<one line>"
(or --status failure, with the reason, if you couldn't do it)
```

### Waiting for a child

- `overcode launch ... --follow` streams the child's output and blocks
  until it reports.
- `overcode follow <name>` does the same for a child that is already
  running.

Both exit with `0` for a success report, `1` for a failure report or a
child that was killed, `2` when the oversight timeout expired, and `130`
when you press Ctrl-C (the child keeps running). A parent that launches
several children without `--follow` can check on them with `overcode list`
and wait for each one with `overcode follow`.

## Oversight

Oversight is what happens to a child that stops without reporting, and to
the child's window after it has finished.

| Flag | Effect |
|------|--------|
| `--on-stuck wait` | Wait for a report for ever (the default) |
| `--on-stuck timeout:DURATION`, or `--oversight-timeout DURATION` | After DURATION at rest without a report, mark it done with ⌛ (a failure, reason "Oversight timeout expired"). The monitor daemon starts the clock when the child stops and enforces it whether or not anyone follows the child. A child prompted again before the timeout gets a fresh deadline the next time it stops. The TUI shows the countdown (⏳) |
| `--on-stuck fail` | A follower (`--follow`, `overcode follow`) marks the child done and exits `1` as soon as it stops without a report. Without a follower the child waits, as with `wait` |
| `--archive-after DURATION` or `never` | How long a done child keeps its window before the daemon closes it (default `1h`). The record then leaves `sessions.json` for the archive after `session_archive.terminated_grace_seconds` (an hour by default; see [Session Archive](configuration.md#session-archive)) |

DURATION is `30s`, `5m`, `1h`, `2d` or a plain number of seconds.
`overcode cleanup --done` archives every done child immediately.

### Presets

A preset sets the oversight policy, the archive delay and a default budget
in one go:

| Preset | Stops without a report | Done child's window closes | Use it for |
|--------|------------------------|----------------------------|------------|
| `supervised` (default) | Waits for you for ever: no timeout | 1 h after done | Work you will review |
| `balanced` | Marked ⌛ after 24 h | 1 h after done | Work you check on once a day |
| `autonomous` | Marked ⌛ after 1 h | 10 min after done | Unattended work that shouldn't leave windows behind |

```bash
overcode launch -n nightly-deps --preset autonomous -p "<prompt>"
overcode launch -n refactor --preset balanced --oversight-timeout 4h -p "<prompt>"  # flags win
overcode config presets    # the presets in effect, and which one is the default
```

- A child launched without `--preset` gets the default preset:
  `supervised`, unless `children.preset` in config.yaml names another.
  `supervised` keeps the behaviour overcode had before presets.
- Any explicit flag (`--on-stuck`, `--oversight-timeout`,
  `--archive-after`, `--budget`) overrides the preset's value.
- A top-level agent gets a preset only if you name one with `--preset`.
  Oversight only ever applies to children.
- If you name a preset that doesn't exist with `--preset`, overcode refuses
  to launch: it exits `1`, lists the valid names and starts nothing. A bad
  name in config.yaml (`children.preset`) only warns, and children get
  `supervised` instead.
- You can change the built-in presets or add your own in config.yaml; see
  [Configuration: Children Presets](configuration.md#children-presets).
  For example:

```yaml
children:
  preset: balanced
  presets:
    overnight:
      on_stuck: timeout:8h
      archive_after: never
      budget: 5.00
```

## Budgets and billing

- `--budget 2.00` caps a child's spend. If the parent has a budget, the
  amount is moved out of it: the launch is refused when the parent doesn't
  have that much left. A parent with no budget (unlimited) gives the child
  its budget without a deduction. A preset can set a default budget, which
  `--budget` overrides.
- When a child reports and turns done, whatever is left of its budget goes
  back to its parent.
- Budgets are soft limits. When an agent goes over its budget, it isn't
  killed, but its heartbeats and supervisor nudges stop. See
  [Cost Budgets](advanced-features.md#cost-budgets).
  `overcode budget transfer <ancestor> <child> <amount>` moves budget down
  the tree later.
- **Billing code:** a child always bills its spend to its parent's billing
  code, so a whole tree adds up under the code its root was launched with.
  This holds even with `--no-inherit`; a `--billing-code` given for a
  child is ignored, with a warning. Codes are sticky: `overcode rebill` on
  a parent moves only the parent, and only from then on. Children already
  running keep their code; children it launches afterwards take the new
  one. See [Billing Codes](configuration.md#billing-codes).

## What a child inherits

Unless you pass a flag for a setting, a child takes these from its parent:

| Setting | Inherited? |
|---------|------------|
| Backend (`-B`) | Yes, except from the overagent (its children are ordinary agents) |
| Model, provider | Only when the child runs on the parent's backend; a codex child of a Claude parent gets codex's default model |
| Wrapper, agent teams | Yes |
| Permission mode | Yes: a child of a bypass parent runs in bypass, and a child of a permissive parent runs permissive. `--normal-permissions` opts out of this one setting |
| Skill profile | Yes, then the folder's pinned profile, then `new_agent_defaults` |
| Billing code | Always, even with `--no-inherit` |

The order is: explicit flag, then parent, then `new_agent_defaults` in
config.yaml, then the built-in default.

**Permission mode deserves care.** A child launched without
`--bypass-permissions` under a bypass parent still runs in **bypass**,
because it inherits the parent's mode. To get a normal-mode child under a
bypass or permissive parent, launch it with `--normal-permissions`: its
permission prompts come back, and it still inherits everything else
(backend, model, provider, wrapper, skill profile, billing code).
`--normal-permissions` can't be combined with `--bypass-permissions` or
`--skip-permissions`; overcode refuses the launch. `overcode fork
--normal-permissions` does the same for a fork, which otherwise takes its
source's mode.

`--no-inherit` also gives a normal-mode child, but it skips every parent
setting except the billing code, so you'd have to add back the ones you
want (`-m`, `--wrapper`, `-B` ...).

## Which tool when

A Claude agent has three ways to hand off work. They show up differently
in the dashboard and give you different controls:

| Use | When | In the dashboard |
|-----|------|------------------|
| **A Claude subagent or workflow** (the Task/Agent tool, a workflow script) | Contained work inside the agent's own session that you don't need to see or steer: searches, reviews, fan-out over files | 👥 / 🧩 rows under the parent, rolled up in SubAgents; read only. Spend counts inside the parent's |
| **A Claude background session** (`/bg`, `claude --bg`) | One-off Claude work that runs on its own, which you'll check on later | A 🌙 row under the agent it came from; attach, stop or remove it with Claude's own commands |
| **An overcode child** (`overcode launch`) | You need another backend (opencode, codex, grok ...), a hard budget, its own billing, an oversight timeout, or a parent that has to know how it ended (`report`, `--follow`, exit codes) | A full agent row (KIND `child`) in its own tmux window: watch it, step in, send it instructions, kill or restart it |

If in doubt: use a subagent when the parent needs the answer to carry on
thinking, and a child when the work is big enough that you'd want to look
in on it.
