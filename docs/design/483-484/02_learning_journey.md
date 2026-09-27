# #483: learning journey

- Date: 2026-09-27
- Issues: #483, #484

Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)

## 2. #483: learning journey

### 2.1 The catalog (single source of truth)

`command_palette.COMMANDS` already has about 72 entries, each with an action,
a title, a category and keywords, and its keys are derived from `BINDINGS`.
That becomes **the** capability catalog:

- Every palette command is automatically a tracked capability, so there's
  nothing to maintain.
- Extra entries cover things that aren't TUI actions:
  - CLI capabilities (`launch --parent`, `fork`, `jobs`, `sister`, `wrappers`,
    `budget`, `tag`, `follow`, `report`).
  - "Residue" capabilities, detected from state files rather than keystrokes.
    Examples: an agent with a parent in `sessions.json`, a standing order set,
    a budget set, a wrapper in use, a sister host configured, the
    summarizer's prompts customised (#491).
- **Prerequisite (P0):** `help_overlay.py` is hand-written and already drifts
  from `BINDINGS`. Generate it from the same catalog. This is also the only way
  to put journey glyphs in help (§2.5).

A capability entry looks like this:

```python
@dataclass(frozen=True)
class Capability:
    id: str                    # usually the action name
    title: str                 # the outcome, not the method
    category: str              # palette category
    keys: tuple[str, ...]      # derived from BINDINGS
    actions: tuple[str, ...] = ()        # actions that count
    cli: tuple[str, ...] = ()            # `cli` records that count
    residue: Callable[[Residue], int] | None = None   # count from state files
    efficient: bool = True     # does "via key" mean fluent?
    fluent_at: int = 8
    tracked: bool = True       # False → "available to discover"
```

### 2.2 The mastery ladder (per capability)

There are five rungs:

1. `unaware`: no signal at all.
2. `seen`: shown to the user by a nudge, help or the journey panel, but not
   used yet.
3. `tried`: 1–2 uses.
4. `habitual`: 3 or more uses.
5. `fluent`: habitual, and at least 50% by key or palette. For capabilities
   with no faster path, `fluent_at` uses instead.

- **Decay:** after 30 days unused, drop one rung per 45 days. The floor is
  `tried`; history is never erased. Both numbers are settings
  (`journey.decay_grace_days`, `journey.decay_step_days`) and are placeholders
  until P1 data shows how long real gaps in use are. A capability you use
  monthly must not look forgotten.
- **Hard-way flag:** set when uses ≥ 3, the key share is < 50%, and a key
  exists. The phantom-key and navigation-cost signals from §1.5 raise the same
  flag for *workflows* ("you walk to agents; `b` jumps").

### 2.3 The curriculum: tracks, tiers and competencies

A competency is a **workflow predicate**, not a click count. Where
competencies come from is covered by session mining (§1.5).

> **First cut only.** These four tracks stop well short of how overcode is
> really used at the top end. Advanced capabilities and special tracks are
> still to be discussed (§6, item 1).

The tracks:

| track | for | example core competencies |
|---|---|---|
| **Basics** | everyone | move between agents · open and fullscreen the preview · send an instruction (`i`) · approve or deny without attaching (`1`/`2`) · find anything with `/` · look something up in help |
| **Fleet** | people running more than 3 agents | launch from the TUI (`n`) · fork (`F`) · restart and kill · sleep an agent (`z`) · standing orders (`o`) · budgets (`B`) · rename (`^N`) |
| **Oversight** | people watching many agents | jump to whoever needs you (`b`) · sort (`S`) · configure columns (`C`) · timeline (`t`) · tags and focal repo · baselines (`,` `.`) · summarizer and prompt lab |
| **Orchestration** | people who delegate | child agents via `launch --parent` · the delegating skill · jobs (`J`) · sister machines (`U`) · wrappers · web control (`w`) · the overagent (§3) |

- **Tiers.** `core` counts toward the track level. `concept` is doc-linked,
  e.g. "what the ◐ status means" or "why a row is dimmed". `advanced` means
  agentic loops. Only core counts toward the level, so "Level 4 / 30" never
  looks unreachable.
- **Prerequisites** form a DAG. The frontier is the up-to-3 unearned core
  competencies whose prerequisites are met, in catalog order, which is also the
  syllabus order.
- **Two ways to earn a competency.** Either demonstrate it, or accept a nudge
  and then do it. Power users never have to sit through a tour.
- **Workflow examples:**
  - "Triage without attaching": ≥3 times, focus an agent that is waiting on
    permission and approve or deny with `1`/`2` within 20 s, without an
    `attach`.
  - "Attention-first oversight": navigation cost median < 2 keys over the last
    20 attention events.
  - "Delegator": a child agent exists whose parent is an agent, not a human
    `launch`.
- **Achievements:** a few, all workflow-shaped. Examples: "Hands off the
  wheel": a full day with ≥5 agents and no `attach`. "Polyglot": the same
  action by key, palette and click.
- **No retroactive celebration.** On the first run, achievements the user has
  already earned are recorded silently. From then on, each new one gets a single
  toast.

### 2.4 The journey panel (`u`)

`u` is currently free. The panel is a `ModalBase`, sized like the prompt lab.
Here is a mock-up:

```
 Your overcode journey                           ● recording · 14 days · 9,312 actions
 ─────────────────────────────────────────────────────────────────────────────────────
  Basics        ██████████░░  5/6    up next → Find anything with /
  Fleet         ██████░░░░░░  4/7    up next → Standing orders (o)   Budgets (B)
▶ Oversight     ███░░░░░░░░░  2/8    up next → Jump to who needs you (b)
  Orchestration █░░░░░░░░░░░  1/6    locked: needs Fleet · Launch from the TUI

 The hard way
  You pressed j/k 6+ times to reach a waiting agent 23× this week.  b jumps there.
  You sort by clicking headers; S opens the sort menu.
  You pick "Toggle timeline" in the palette 11×.  It's t.

 Keys you reach for that don't exist
  v (17×, usually then s)   e (4×)

 Capabilities                                     ○ unaware ◔ seen ◑ tried ◐ habit ● fluent
  Navigation  ● next/prev  ● preview  ◐ fullscreen  ○ jump-to-attention  b
  Control     ● send  ◐ approve  ◑ fork  ○ standing orders  o  · web control
  ...
 ─────────────────────────────────────────────────────────────────────────────────────
  enter track syllabus · t try it now · a ask the overagent · m mentor: off · esc
```

- The key is shown only on `unaware` items; you don't need reminding of keys you
  know. A `·` marks an untracked, "available to discover" item.
- A track's syllabus lists items grouped by tier: ✓ achieved, → next,
  ◦ upcoming, 🔒 locked (needs X), and a doc link for `concept` items.
- **`t` (try it now)** closes the panel and runs the frontier action, or opens
  its dialog. If the user then completes it, that counts as `engaged`.
- **`a` (ask)** hands the selected item to the overagent (§3.5).
- The same data prints from `overcode journey [--json]`, for scripts and for
  the overagent.

### 2.5 Nudges: the director

A small, pure function picks at most one candidate. Candidates, in priority
order:

1. **Continuity.** "Last time: standing orders. Next up: budgets (B)."
2. **Hard way.** The most frequent slow path this week. This has the highest
   teaching value because the user is already doing the task.
3. **Phantom key.** "You've pressed `v` 17 times. Did you mean `s` (summary
   detail)?"
4. **Frontier.** The first frontier item of Basics, then of the track the user
   is spending time in, then the rest.

**When a nudge may appear** (all must hold):

- The mentor setting allows it. It is `journey.mentor: off | occasional |
  coach` in `config.yaml`, and it can also be changed in the journey panel.
  **It defaults to `off` for an extended period**; early adopters trial it
  switched on. With it off, the journey panel (`u`), `overcode journey` and the
  overagent all still work: pull-only, no unsolicited nudges.
- The TUI is attended, **5 s after the last key**, with no modal open and the
  command bar empty.
- No agent is waiting on the user. A tip must never compete with a permission
  prompt.
- The time since the last nudge is at least `30 min × 2^(-r/1.5)`, where the
  receptiveness `r` runs from −3 to +3. Each dismissal lowers `r` by 1 and
  snoozes that topic for 7 days. Each engagement raises `r` by 1.
- At most one unsolicited nudge per TUI run on `occasional`.
- The mentor fades with **tenure**, independent of mastery: after month 1 only
  the single top item is offered.

**Surfaces**, from lightest to heaviest:

1. **Hint strip.** One dim line at the bottom of the agent list: `tip  b jumps
   to the agent waiting on you  ·  u journey  ·  esc dismiss`. It dismisses
   itself when the tip is acted on. This is the default.
2. **Help glow.** When help (`h`) opens, rows for `unaware` and `hard way`
   items are highlighted, and help sorts "new to you" first.
3. **Toast.** Only for a new achievement, once each.

Mentor state lives in `~/.overcode/journey_state.json`: dial, receptiveness,
snoozes, the last topic, the achievements already celebrated, and the first-seen
date. It is written atomically.

### 2.6 Tests

- Each competency gets a recipe of synthetic events that must earn it, plus a
  negative control that must not.
- A coverage check: every palette command is `tracked`, `untrackable` (with a
  reason) or `cut`, so new commands can't be silently dropped from the journey.
- Pilot tests: `u` opens the panel, a nudge shows only when idle and
  unattended-by-agents, and dismissing it records `dismissed`.
- The director has property tests: it never nudges an item at `habitual` or
  above.


---
Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)
