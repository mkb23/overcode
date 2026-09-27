# Learning journey (#483) and overagent (#484): overview

- Date: 2026-09-27
- Issues: #483, #484

Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)

Status: draft for review, 2026-09-27. Nothing here is built yet.

The two issues share a foundation, so this doc covers both:

- **A keystroke-level usage log.** The journey needs it to know what you have
  learned. The overagent needs it to give advice grounded in how you actually
  work.
- **A single catalog of what overcode can do.** It feeds the help overlay, the
  palette, the journey and the overagent's skill.

```
             keystrokes / actions / clicks / CLI calls
                              │
                   ┌──────────▼──────────┐
                   │  usage log (§1)      │  ~/.overcode/activity/*.jsonl
                   └──────────┬──────────┘
          ┌───────────────────┼─────────────────────┐
          ▼                   ▼                     ▼
  usage analytics (§1.5)  journey model (§2)   attention context (§3.4)
          │                   │                     │
          ▼                   ▼                     ▼
  overcode activity     journey panel `u`,    overagent window `e`
  (CLI, --json, stream) nudges, `overcode      (Claude + configurator skill,
                        journey`               drives the TUI via §3.3)
```

## 0. Principles

1. **Teach by doing, just in time, then fade.** The best game tutorials never
   lecture; they notice what you haven't found yet and show you at the moment
   it would help. Success means the user stops needing it: mastery is the goal,
   not engagement.
2. **Earned nudges only.** Never teach something the log shows you already do.
   Never explain `b` to someone who presses `b` twenty times a day.
3. **Pull first, push rarely.** The journey panel and the overagent are always
   one key away. Unsolicited nudges are capped and back off when dismissed.
4. **No streaks, XP or points.** They read as childish in a serious tool. The
   motivation comes from making *what's still to learn* visible.
5. **Honest state.** If a capability can't be observed, show it as "available
   to discover". Never show it as a fake zero or claim it has been discovered.
6. **Progress is derived, not stored.** Journey state is a pure function of the
   usage log plus a tiny mentor-state file, so it can't drift. The same function
   feeds the TUI, the CLI (`--json`) and the tests.
7. **Freshness and CPU budgets hold.** The #486 work (TUI at about 4% of a core)
   must not regress. Logging a keystroke costs a list append, not a syscall.


---
Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)
