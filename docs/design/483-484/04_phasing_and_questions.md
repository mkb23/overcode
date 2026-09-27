# Phasing and open questions

- Date: 2026-09-27
- Issues: #483, #484

Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)

## 4. Phasing

| phase | ships | size | depends on |
|---|---|---|---|
| **P0** | #493 applied · help overlay generated from the catalog | S | — |
| **P1** | usage log capture (§1.1–1.4), `overcode activity summary/keys`, test fixture, budget test | M | — |
| — | *collect real data for several weeks, then calibrate thresholds and decay from it rather than guessing* | | |
| **P2** | journey model, `overcode journey`, journey panel `u` (pull only, no nudges) | M | P1 |
| **P3** | director, hint strip, help glow, `journey.mentor` config (default `off`) | S–M | P2 |
| **P4** | view-control API, `overcode view`, `tui_view_state.json` | M | — (parallel with P1–P3) |
| **P5** | overagent backend, `e` key, persona, configurator skill, bundled docs, permissions | M | P0 (#493), P4 |
| **P6** | integration: journey `a` → overagent, the overagent as a way into the journey, `activity stream` digests | S | P3, P5 |
| **P7** | overagent as feature-request and self-modification helper (§3.7) | M | P5 |
| ongoing | session mining for competencies (§1.5) · prove out each signal (§1.5) | — | P1 |

P1 and P4 are independent and could run as two parallel agents. P1 should go
first on the critical path, because every journey threshold is a guess until
there is data.

## 5. Decisions (from review, 2026-09-27)

1. **Typed text.** Never record what people type to their agents: the
   command bar, standing orders and new-agent prompts are always redacted, with
   no opt-in. Text that is overcode's own metadata *is* recorded, because it
   helps suggestions: annotations, tag names, agent names, palette queries.
   See §1.3.
2. **Mentor default.** A config setting, `journey.mentor: off | occasional |
   coach`, defaulting to `off` for an extended period. Early adopters trial it
   switched on. The usage log still records locally (`activity.record: on`), so
   anyone who turns the mentor on later starts with their history.
3. **Keys.** `u` opens the journey and `e` opens the overagent.
4. **Overagent in the list.** It is a normal agent alongside the rest, launched
   with its own backend. It is not pinned, not hidden, and not excluded from
   stats. See §3.2.
5. **Sister hosts.** The log is per machine for now. The schema carries `host`,
   and events are loaded through one loader that can later take several
   sources, so sister data can be merged into one journey view without a
   migration.
6. **Phantom-key export.** Dropped. Instead the overagent helps turn "I can't
   find X" into a feature request, or a patch (§3.7).

## 6. Still open

1. **Advanced capabilities and special tracks.** The four tracks in §2.3 are a
   first cut that stops well short of how overcode is really used at the top
   end. To discuss: which advanced usages deserve their own tracks (e.g.
   multi-agent orchestration patterns, remote and sister fleets, custom
   backends and wrappers, budget-governed autonomous runs, prompt
   engineering for summarizers), and whether a special track can be opted
   into rather than inferred.
2. **Decay calibration.** §2.2 now starts at 30 days grace and one rung per
   45 days. Both are settings, to be set from P1 data.
3. **Which signals are real.** Every signal in §1.5 is a hypothesis until it
   has produced nudges people act on. See "proving the signals" in §1.5.
4. **Jobs in the main switcher.** Jobs have their own tmux session and a
   dedicated view (`J`). Worth a separate design pass on folding them into the
   main agent list, especially now that the overagent and shell windows (#496)
   show that "an agent row" can hold more than one kind of thing.

---
Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)
