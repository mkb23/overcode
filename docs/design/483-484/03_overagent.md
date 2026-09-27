# #484: the overagent window

- Date: 2026-09-27
- Issues: #483, #484

Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)

## 3. #484: the overagent window

### 3.1 What it is

A persistent Claude Code agent that knows overcode. You open it with one key and
talk to it for:

- **control:** "restart every dead agent", "sleep everything in repo X"
  (see also #481)
- **configuration:** "show me cost and hide branch", "why is RPO n/a?"
- **help:** "how do I get approvals without attaching?"
- **advice:** "what am I doing slowly?", grounded in §1.5

It is the *pull* half of the journey, and **a way into it**. The journey panel
shows what you haven't found yet, and the overagent answers questions about it.
The reverse also works: asking it "what should I learn next?" or "show me my
journey" gets an answer grounded in `overcode journey --json`. It can open the
journey panel with `overcode view open journey`, or walk you through a frontier
item there and then.

### 3.2 Shape

- **It is a normal agent alongside the rest.** It is launched through
  `AgentLauncher` like any other agent, with the default name `overcode`. It is
  listed, sorted, counted and costed like everything else, with no pinned row,
  special glyph or exclusions. Restart, resume, cost and status all come for
  free.
- **It has its own backend: `overagent`.** `overcode launch -B overagent`, or
  pick it in the new-agent dialog. The backend wraps `claude_code` and adds the
  persona, the configurator skill, the bundled docs and the permissions below.
  This is the same shape as the `shell` backend (#496): a small class whose
  `build_command` / `prepare_launch` extend another backend's. Nothing about
  the overagent lives in the list code. More than one overagent is allowed (one
  per project, say), though `e` goes to the most recent.
- **Key `e`** (currently free). It focuses the most recent overagent-backend
  row and opens it in the terminal split, the same way you attach to any agent,
  so it's a real Claude session with all its normal UI. If there isn't one, the
  first press launches it (about 2 s). Pressing `e` again returns focus to the
  list.
- **Backend:** Claude Code first, because it has skills, `--agent` and
  `--settings`. Other backends come later.
- **Persona and skill:**
  - Launched with `--agent overcode-overagent`, an agent definition installed
    by `overcode skills install` next to the existing bundled skills.
  - It loads a new bundled skill, **`overcode-configurator`**, which covers:
    - the column catalog, with every column's `description` (generated from
      `SUMMARY_COLUMNS`, so it stays current)
    - the key and palette catalog (§2.1)
    - config.yaml keys, and how to read status glyphs
    - the view-control API (§3.3)
    - the usage analytics commands, and how to give advice from them: lead with
      the hard-way and phantom-key signals, and never claim a user doesn't know
      something without evidence
    - the journey: how to read `overcode journey --json` and walk someone
      through a frontier item
- **Overcode's skills and docs in context.** The `overcode` and
  `delegating-to-agents` skills are loaded as well. The user docs
  (`docs/*.md`: TUI guide, configuration, CLI reference, backends, wrappers)
  are shipped as package data and found with `overcode docs path`, so the
  overagent answers from the docs for the installed version rather than from
  memory. The configurator skill lists which doc answers which kind of
  question.
- **Working directory:** `~/.overcode`, so it can read configs and state
  directly.
- **Lifecycle:** it resumes its own conversation across restarts. **This
  depends on #493**: the overagent is exactly the long-lived, often-`/clear`ed
  agent that loses hours of context without that fix, so #493 is a hard
  prerequisite.

### 3.3 The view-control API (the missing piece)

Today the TUI's view state (columns, sort, detail level, filters, focus) is held
in memory and in `tui_preferences.json`. That file is loaded once at startup,
never reloaded, and overwritten on the next toggle. An agent editing it would
lose. So the TUI gets a small control inbox, and the TUI remains the only writer
of its own prefs:

- **Command:** `overcode view <verb> ...` appends one JSON command to
  `sessions/<s>/tui_control.jsonl`.
- **Pickup:** the TUI's existing fast timer checks the file size (one `stat`,
  no new polling). New lines are applied through **the same handlers the
  dialogs use**; for columns, that is the code in
  `on_summary_config_modal_config_changed`.
- **Acknowledgement:** the TUI writes an ack to `tui_control_ack.jsonl` with
  `{id, ok, error?, state_after}`. The CLI waits up to 2 s for it and prints the
  result, so the agent *verifies* rather than assumes. When the TUI isn't
  running, the CLI says so and edits prefs directly.
- **Verbs:** `columns list|show|hide|reset [--level]`, `sort <mode>`,
  `detail <level>`, `filter tag <t>|clear`, `focus <agent>`, `toggle <action>`
  (any palette action, by id), `notify "<text>"`, and `point <action>`. `point`
  is a coachmark: the help overlay opens scrolled to that key, with the key
  highlighted, and the hint strip shows it.
- **Errors say what *is* possible.** For example: `unknown column 'rpo' — did
  you mean 'repo' (Repo, hidden: same value on every row)`.
- Commands are logged as `via=agent` (§1.1), so the overagent's actions never
  count toward the user's journey.

### 3.4 Attention context

The TUI keeps `sessions/<s>/tui_view_state.json` up to date. It is written on
change and debounced to at most 1 per second. It contains:

- the focused agent and the visible columns, with their values for the focused
  row
- the sort and filters
- which dialog is open
- the last 20 actions

This lets the overagent resolve deictic questions without asking. For example,
"why is *this* column n/a?" resolves to: the focused row is X, the column is
RPO, and its value is `n/a`. That exact case is #494.

### 3.5 How the overagent and the journey connect

- `a` in the journey panel sends the selected item to the overagent as a
  prompt, e.g. "Explain standing orders and set one up with me". It opens the
  overagent window and records `engaged`.
- The overagent can run `overcode activity stream --detail rollup` under
  `Monitor` while you work. **Off by default**; the user asks for it.
- `overcode journey --json` and `overcode activity summary --json` are its
  grounding for any "what should I learn / what am I doing slowly" answer. The
  skill tells it to cite the numbers.

### 3.6 Permissions

It is launched with `--settings` (the same injection path as today's hooks).

**Allowed without asking:**
- `Bash(overcode list*)`, `show`, `view *`, `activity *`, `journey*`,
  `config show`, `usage`, `history`, `export`
- `Read(~/.overcode/**)`
- `Edit(~/.overcode/config.yaml)`, `Edit(~/.overcode/summarizer_prompts/**)`

**Asks each time** (normal Claude permission prompts): `kill`, `restart`,
`cleanup`, `launch`, `send`, `instruct`, budget changes, and anything else.

**Never** launched with a permissions bypass. It controls the fleet, so a bad
guess must cost a prompt, not an agent.

### 3.7 Feature requests and self-modification (later, P7)

When the user can't find something ("is there a way to see only agents in repo
X?"), the overagent first checks the catalog and the docs. If the feature
really isn't there, it offers to help:

1. **Write it up.** It drafts a feature request from what it saw: the user's
   words, the relevant phantom keys, palette misses and hard-way patterns, and
   the closest existing feature. After you approve, it files it with `gh issue
   create`.
2. **Build it.** If overcode is installed from a checkout (editable install),
   it can do more: "I can add this. Want me to?" It launches a child agent in
   that checkout on a branch, using the delegating skill. The child implements
   the feature with tests, the overagent reports back, and you try it live,
   since an editable install picks the change up on the next TUI restart.
3. **Upstream it.** Once you're happy, the overagent opens a PR against
   overcode with the change and the issue linked.

Every step asks first: filing an issue, starting a child agent and opening a PR
are all outward-facing or costly. For a user on a PyPI install, step 2 becomes
"here is the issue, and here is a patch sketch" instead.

### 3.8 Tests

- Unit: the control-inbox round trip (append → apply → ack), each verb against
  pilot, and ack timeouts when the TUI isn't running.
- A fake `claude` shim (`OVERCODE_CLAUDE_BIN`) for lazy launch, focus and
  restart-resume. It never spawns a real agent in unit tests (#485).
- Live tier (`make test-live`): a real Claude overagent asked to "hide the cost
  column". The test asserts that the ack arrives and the TUI's column set
  changes.


---
Parts: [00 overview](00_overview.md) · [01 usage log](01_usage_log.md) · [02 learning journey](02_learning_journey.md) · [03 overagent](03_overagent.md) · [04 phasing and questions](04_phasing_and_questions.md)
