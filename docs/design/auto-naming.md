# Auto-naming agents (#542)

Status: proposal, 2026-10-20; owner decisions taken the same day (end of file).
Section 1 (P0), sections 2 and 2a (P1), sections 3-8 (P2, with P4's
parent notices and Claude name alignment) and `overcode name suggest` are
built. Builds on `overcode rename` (#478) and the
per-agent directories keyed by session id (quality review item 35).

## Goal

Once an agent has done enough work for its task to be clear, overcode gives
it a good name, without restarting it:

1. following naming advice the user can configure,
2. aware of the other agents' names, so the new name is distinct and short,
3. never overriding a name a human chose, decided agent by agent,
4. with a naming lab for trying out advice, like the summary prompt lab (#491).

Non-goals: sister agents (semi-deprecated: no naming, no uniqueness
across machines); the agent naming itself; renaming jobs. Billing codes
become ids with renamable names (`billing-code-ids.md`), which auto-naming
uses but does not depend on.

## What exists today

- **Rename restarts the agent** (until section 1 was built). `RenameMixin.rename` (`rename.py:55`)
  refuses a busy agent, sends a graceful exit, renames the window
  (`<name>-<id4>`), commits the record (`rename_session`: `previous_names`
  alias, locked duplicate check), writes `agents/<id>/rename_notice.txt`,
  and resumes the conversation. The restart is needed only because the
  running process carries `OVERCODE_SESSION_NAME`, and three hook-handler
  paths look the agent up by it with no alias fallback:
  `_sync_agent_session_id` (`hook_handler.py:903`), the budget check
  (`:1383`) and enhanced context (`time_context.py:221`).
- **Files are already keyed by id.** hook state, hook events, report,
  episodes, heartbeat and the rename notice live in `agents/<id>/`, so a
  rename moves no files. sessions.json, parent links, the archive, day
  bundles and the ledger are keyed by id too.
- **Old names keep working.** `find_agent` / `resolve_session_name` follow
  `previous_names` and print `note: agent 'X' was renamed to 'Y'`, so
  `overcode send old-name` from a parent still reaches the agent.
- **There is no record of where a name came from.** The new-agent modal has
  a form-local `auto` flag that is never saved.
- **Summariser and lab.** The AI summariser runs in the TUI, reads the pane,
  calls OpenAI or Anthropic through `summarizer_client.py`, and is off by
  default with a per-launch cost cap. Its "context" mode (task, noun-first,
  60 chars) is close to a name already. The prompt lab (`prompt_lab.py`,
  `tui_widgets/summary_prompt_lab.py`) edits one draft prompt, runs it on up
  to 6 live agents and saves to `~/.overcode/prompts/summary-<mode>.md`.

## Design

### 1. Rename without a restart (prerequisite)

**Built** (2026-10-20). As designed, plus: the lookups share
`agent_identity.py` (`in_agent`, `current_agent`, `find_in_daemon_state`);
an id with no record is no agent (never a name fallback, since the launch
name may belong to another agent by then); a child launched from inside an
agent gets its parent by id too; the daemon sets `@overcode_id` on every
live agent window once at start (idempotent, rather than checking first);
the TUI rename dialog shows its Force row only for a pre-0.6 agent; and
`rename()` takes a `source` keyword it does not store yet (section 2).
An agent counts as 0.6+ when it has its own directory (`agents/<id>/`),
which every launch since then creates along with `OVERCODE_SESSION_ID`.

An agent's identity becomes its session id everywhere. The name becomes
something displayed, not a key.

- The hook handler, `overcode report`, the jobs link, the activity log and
  `view_control` resolve the agent by `OVERCODE_SESSION_ID` first. They fall
  back to `OVERCODE_SESSION_NAME` only for an agent with no id (pre-0.6).
  Enhanced context prints the record's current name, not the env's.
- Exact-name lookups switch to the alias-aware resolver:
  `follow_mode.py:73`, `monitor_daemon_state.get_session_by_name`,
  `cli/doctor.py:273` and `command_bar.py:307`. The supervisor's
  intervention counts, and the daemon's subtree rollup, key by id.
- `_detect_from_tmux_pane` stops reverse-parsing the name out of the window
  name. It reads a `@overcode_id` window option, set at launch and kept by
  `rename-window`.
- `rename()` then just renames the window, commits the record and writes the
  notice. It no longer stops a busy agent, needs no `--force`, and has no
  relaunch to roll back. A pre-0.6 agent still uses the restart path.
- The env name is left as the launch name. Nothing reads it for identity
  any more. The devcontainer wrapper keeps the container named at launch.

This is worth doing even without auto-naming: it removes the "agent busy"
refusal and the 3 s gap from every manual rename.

### 2. Where a name came from

**Built (P1, branch name-policy).** As designed, except: the palette title
is "Let overcode name it" (the longer one does not fit the palette), and
old records read as `default` only for an exact made-up default
(`name_source.infer_legacy`), else `human`.

A new `Session.name_source`, kept in sessions.json. Loaders default it for
old records.

| value     | set when                                                                   | auto-renamed? |
|-----------|----------------------------------------------------------------------------|---------------|
| `default` | the TUI modal's directory basename, a fork's `-fork`, a pre-feature record | yes           |
| `parent`  | `-n` given by an agent launching a child (`OVERCODE_SESSION_ID` set), checked against the policy (2a) | **never** |
| `auto`    | overcode named it                                                          | yes, within limits |
| `human`   | `-n` from a human shell, a TUI or CLI rename by a human, the modal's name typed in | **never** |

A rename by a human, from the CLI or with Ctrl+N, sets `human`, and that
locks the name. Per agent:

- The TUI marks automatic names in the name column: dim for `default`,
  a leading `~` for `auto`. The column keeps its width, so nothing jumps.
  It must pass the layout matrix.
- A palette action, "Let overcode name this agent", plus
  `overcode rename <agent> --auto`, hands a locked name back to the namer.
  Ctrl+N's dialog has the same toggle. The reverse is "Keep this name"
  (`--lock`), which sets `human` without changing the name.
- `overcode list --json` and the hover show the source and the earlier
  names.

### 2a. Checking the names parents give

**Built (P1, branch name-policy),** with the owner's change of 2026-10-20:
no edit-distance or other similarity check (a numbered series such as
`wiki1`, `wiki2` is deliberate), and every rule opt-in with defaults that
reject nothing. Only the base name pattern and a taken name (live name or
alias) are always checked; `max_length`, `pattern`, `forbid`, `require`,
`no_repo_prefix` and recent archived names (`recent_archived_hours`) are
off by default. `naming.max_length` (18) is the namer's target, separate
from the hard `naming.rules.max_length` (unset). The free-text advice is
the primary policy. Code: `naming_policy.py` (`check`, `choose`),
`cli/naming.py`.

A parent's name is never renamed, so it is checked once, when the parent
chooses it. `overcode launch` and `fork`, run from inside an agent
(`OVERCODE_SESSION_ID` set), check `-n` against the naming policy and
refuse a name that breaks it.

- **The advice is the policy.** The free-text advice (`naming.advice`, or
  the prompt file) is where naming conventions live. Deterministic rules
  are an optional supplement for what is worth enforcing exactly.
- **Only two checks are built in:** overcode's name pattern, and a name that
  is already taken (a live name or a live alias). Neither can be configured
  away.
- **Every other rule is opt-in** in a `naming.rules` block, and the defaults
  reject nothing a person would reasonably choose: `max_length` (no limit),
  `pattern` (none), `forbid` (empty), `require` (none), `no_repo_prefix`
  (off), `recent_archived_hours` (0, off; refuse names archived that recently). The CLI
  checks these with no API call and no delay, so they apply whether or not
  auto-naming is on.
- **No similarity rule.** Numbered series (`wiki1`, `wiki2`, `wiki3`) are
  deliberate, legitimate names, and an edit-distance rule would reject
  them. Telling confusable names apart is left to the model, which sees the
  fleet.
- **The free-text advice** can be checked by the model with
  `naming.rules.llm_check: true`, off by default. That is one call per
  child launch. A failed or timed-out call lets the name through, so it
  never blocks a launch.
- **The refusal is written for the parent agent:** exit code 3, the rule
  that failed, the advice text, and a suggested name that passes:
  `name 'child-1' breaks the naming policy (forbidden word 'child');
  try 'jwt-refresh-tests', or pass --force-name`. It goes to stderr and,
  with `--json`, as a structured error.
- **`--force-name`** launches with the name anyway. The record notes
  `name_forced: true`, and the TUI shows it in the hover. `--force-name`
  does not get past a taken name or the name pattern; those stay hard
  errors, as today.
- **Humans are not checked.** A `-n` from a human shell only gets a
  warning, with the same message, if `naming.rules.warn_humans` is set.
- **`overcode name check <name>`** runs the same check without launching,
  and `overcode name policy` prints the rules and the advice. The overcode
  skill tells a parent to read the policy before naming children, so most
  names pass the first time.

The checker is the same code as step 1-2 of section 5, so a name the namer
would pick always passes.

### 3. When to name

**Built (P2, branch auto-namer),** in `namer.py` (`Namer`, `due`), run
from the daemon tick after publishing. As designed, with: a turn end is a
status going from green to waiting_user / waiting_heartbeat /
waiting_oversight / done, as the daemon publishes it (so hookless agents
count too); the turn count is the larger of those seen (kept in
`agents/<id>/namer.json`) and the transcript's interaction count; the
refinement needs 10x every threshold (turns and green minutes or tokens; replaced by `rename_every` in #547);
a failed or unreadable call is retried no sooner than 10 minutes later.
There is no wait for the TUI to go quiet (an earlier draft held renames
for 30 s after any TUI keypress; it only delayed names, since a rename
interrupts nothing and the old name stays an alias). The worker is
one thread (renames from one daemon never race each other). The cost cap
is per tmux session (`namer_spend.json` beside the daemon state). Each
call is one ledger row of its own (`sid` `namer:<hex>`, `parent` the
agent, `kind` final, `namer: true`) under the code `overcode`, so the
`overcode` code's agent count is its number of calls.

The daemon decides, because it owns per-agent values (AGENTS.md). It works
from events and adds no polling.

- **The first name** comes `first_after.minutes` (default 5) after the
  agent's first prompt (`prompts.jsonl`'s first time, else the launch for
  an agent launched with a brief), once it has any green time; checked
  each tick (`first_due`), mid-turn or idle. The task is what names an
  agent, and five minutes in it has usually shown what the work is. A
  "keep" answer for a `default` name is asked again 10 minutes later, at
  most 3 times (`first_tries`). "Let overcode name it" sets the source to
  `default` (no `~`: overcode did not choose the name) and clears the
  naming history (`hand_to_namer`), so the rule applies afresh.
- **A check every `rename_every.minutes` of work** (default 30; #547,
  replacing the one refinement after 20 turns). Green time since the last
  answered check, any tick, mid-turn or idle (owner decision 2026-10-22:
  "any time", not only at a Stop). The model sees the recent window (last
  3 prompts from `recent_prompts.jsonl` with their age, files edited since
  the check, and the `why` of the current name) beside the brief, and is
  told to keep an `auto` name unless the focus has clearly moved on.
  No cap on auto renames by default (`max_renames: null`); a failed call
  waits 10 minutes. A green-time count that starts again (a restart)
  restarts the check's count.
- Never while the name is `human` (or `parent`).
- Calls run on the daemon's worker pool, with a per-day cost cap
  (`naming.cost_cap_per_day`, default $10; was $0.50). Spend goes in the ledger
  under the billing code `overcode` (closing the gap the summariser has).
  At about 2 calls per agent with a mini model, 100 agents cost about $0.05.

### 4. What the namer reads

**Built (P2).** `brief.txt` is written by `launch` and `fork` (a fork with
no prompt of its own copies its source's). `prompts.jsonl` comes from the
UserPromptSubmit hook: the payload's `prompt` (Claude Code's; codex, grok
and hermes are captured when their normalised payload carries it, not
live-checked); the opencode plugins' `chat.message` is left to the
opencode-notices branch (TODO). The most-edited files come
from `tool_input.file_path`, which the hook event log now keeps. The PR is
its number (no title is fetched). The fleet is live local agents plus
records archived in the last 24 h. The repo column flag is not passed to
the model; `no_repo_prefix` stays a rule. The prompt gains a `{parent}`
placeholder.

Per-id storage makes this cheap: small files beside the ones that already
exist, written once and never moved.

- `agents/<id>/brief.txt` is new. At launch it holds the first 2 KB of the
  launch prompt.
- `agents/<id>/prompts.jsonl` is new. The hook handler appends the first 3
  user prompts (1 KB each) on UserPromptSubmit. For hermes, codex and grok
  this comes from their UserPromptSubmit hooks; for opencode, from the
  plugin's `chat.message`.
- The repo, branch, cwd and PR title from the engine's git view. A branch
  is often a good name already.
- The most-edited files, from the hook events' tool inputs.
- The current name and its source, and the parent's name.
- **The fleet.** Every live local agent plus the last 24 h of archived ones
  (the archive is indexed by id, so this is a small read), each with its
  name, source, repo and parent. Sister agents are not included.
- **The width budget.** `naming.max_length`, default 18, and whether the TUI
  is showing the repo column, so the name need not repeat the repo.

A hookless agent falls back to the last 60 pane lines, as the summariser
does.

### 5. Choosing the name

**Built (P2)** with `naming_policy.choose`. The rename stores
`name_source` in the same locked write (`rename_session(name_source=)`). A
lost race chooses again once against the fleet as it is then. A pick equal
to the current name, or `keep: true`, renames nothing. The first auto name
of a root agent calls `codes.follow_agent_name` on its code (owner decision
3); a child shares its root's code, so never.

The model returns JSON: `{"candidates": ["jwt-refresh", "auth-refresh",
"jwt-fix"], "keep": false, "why": "..."}`, best first. `keep: true` means
the current name still fits, which is how most checks rename nothing. Overcode
then decides on its own, without the model:

1. Normalise each candidate to overcode's name pattern. Drop any that
   breaks a configured rule (2a), using the same checker as the CLI.
2. Drop any equal to a live name or a live alias. Nothing more: numbered
   series stay legal, and `naming.max_length` is a target given to the
   model, not a rejection, unless `rules.max_length` is set.
3. Take the first survivor. If none survive, add the shortest disambiguator
   to the first candidate (`-2`, then `-3`), or the parent's issue number if
   advice asks for one.
4. Commit through the restartless `rename()` with `name_source=auto`. Its
   locked duplicate check settles a race between two agents naming at once.

Children of one parent are named relative to each other. The fleet list
groups siblings, so advice such as "siblings share a short prefix:
`auth-db`, `auth-ui`" can be followed, and the engine's sibling view keeps
the set readable.

### 6. Naming advice

**Built** (P1 and P2); `naming.provider` (`api_type`, `api_url`, `model`,
`api_key_var` over the `summarizer:` block) and `naming.cli_title` (default
on, section 7) are the additions.

```yaml
naming:
  enabled: false            # off by default (see the open questions)
  advice: |                 # inline, or put it in ~/.overcode/prompts/naming.md
    kebab-case, 1-3 words, what the work is about, not how.
    Put an issue number last when there is one: fix-jwt-542.
  max_length: 18             # a target for the namer, not a rule
  rules:                     # all optional; the defaults reject nothing (2a)
    max_length: null
    pattern: null            # e.g. '^[a-z0-9]+(-[a-z0-9]+)*$'
    forbid: []               # e.g. [test, tmp]
    require: null            # e.g. '-[0-9]+$' for an issue-number suffix
    no_repo_prefix: false
    recent_archived_hours: 0 # refuse names archived this recently (0: off)
    llm_check: false         # check the advice with the model too
    warn_humans: false
  first_after: {minutes: 5}
  rename_every: {minutes: 30}
  max_renames: null
  cost_cap_per_day: 10.00
  # provider: falls back to the summarizer: block (api_type, url, model, key var)
```

- **Project advice.** `<repo>/.overcode/naming.md`, if present, is added
  after the user's advice. It is a team convention kept in the repo.
- **Presets.** Children presets can set `naming.enabled` or `advice`, for
  example `autonomous` naming its children.
- **Prompt template.** `~/.overcode/prompts/naming.md` holds the full
  template. Its placeholders are `{advice}`, `{brief}`, `{prompts}`,
  `{repo}`, `{branch}`, `{files}`, `{current_name}`, `{name_source}`,
  `{fleet}`, `{siblings}` and `{max_length}`. It is rendered with the same
  safe one-pass substitution as the summary prompts.

### 7. Keeping the CLI's own name in step

**Built for Claude Code (P4).** `display_name_args` (`--name <name>`) on
every launch, restart and revive. Alignment: at a turn end, a Claude agent
whose believed title (`namer.json` `cli_title`) differs from its name gets
`/rename <name>` typed in when its live input line is the bare prompt glyph
(not gated on TUI keypresses: keys typed into the agent's pane reach tmux,
not the TUI, so the empty line is the guard); it runs whatever started the rename
(auto, CLI, TUI). Adoption: a new `custom-title` (Claude 2.1.304 writes
`{"type": "custom-title", "customTitle", "sessionId"}`, plus an
`agent-name` twin, at a `/rename`) that is not the agent's name, an old
name of it, or the title overcode last typed is normalised and committed as
a `human` rename; a taken or invalid one is logged and left. Runs with
`naming.enabled` off; `naming.cli_title: false` turns it off. An agent
renamed while the daemon was down is assumed in step (its title is not
recorded at launch). Hermes and opencode titles: not done.

Claude Code has a session name of its own: `claude -n/--name` sets it,
`/rename` changes it, and it shows in the prompt box, the `/resume` picker,
the terminal title and Remote Control. The transcript records it as
`{"type": "custom-title", "customTitle": ...}`. Claude also writes an
`agent-name` record (`agentName`) for the background sessions it names
itself. Overcode passes none of this today, so a `claude --resume` picker
lists overcode agents by their first prompt.

- **At launch**, the Claude backend passes `--name <overcode name>`. A
  backend gets this through a new `display_name_args(name)` hook, empty by
  default.
- **On a rename**, overcode types `/rename <new>` into the agent. It does
  this only when the agent is idle and its input line is empty (the
  launcher's ready-line check). The rename itself (Ctrl+N, the CLI, an
  auto rename) types it in at once when it can (`RenameMixin._type_cli_title`);
  otherwise the daemon checks an idle agent on the next tick and every 10 s while the input line is
  not empty (asleep counts as idle); a busy one waits for its next Stop. A title never recorded is
  taken to be the launch's `--name`, so a rename records the old name as
  the title first. A
  missed alignment changes only the cosmetic title, never overcode's
  identity, so it is never forced.
- **The other way round:** a `/rename` the user types inside Claude shows
  up as a new `custom-title` in the transcript, which the stats reader
  already tails. The engine adopts it as a human rename of the agent
  (`human`, locked). One name, whichever side changed it.
- The other backends follow where their CLI has a title: hermes `/title`,
  opencode's session title. opencode titles its sessions itself; that title
  could also be an input to the namer.

The model learns its name from overcode, not from the title: through the
rename notice and the enhanced-context line `Agent: <name>`, which now
prints the record's current name (P0).

### 8. Telling people

**Built.** The parent notice is written by every rename of a child (human
or auto) to `agents/<parent>/child_notice.txt`, appended, and printed and
removed by the parent's next UserPromptSubmit. The toast is the TUI's,
when a row's record changes name to an `auto` source. The activity-log
line is the daemon log (the `d` panel): `namer: auto-named 'a' → 'b'
(why)`.

- **The agent** gets the existing one-shot `rename_notice.txt` on its next
  prompt: "You are now `jwt-refresh` (was `overcode-main2`); old names still
  reach you." This works today for Claude and hermes. opencode needs #533.
- **The parent** gets a notice on its next prompt: "your child
  `overcode-main2` is now `jwt-refresh`". A parent that kept the old name
  in its context still reaches the child through the alias, so this is
  courtesy, not correctness. That answers #478's worry about cross-agent
  messages.
- **The human** gets a TUI toast (`overcode-main2 → jwt-refresh (auto)`), an
  activity-log line, and the `~` marker. Ctrl+N on the agent shows the old
  name, so undoing is one rename, which then locks the name.

### 9. The naming lab

`overcode name suggest` is built (P2); the lab and `name lab` are P3.

**Built (P3, branch naming-lab),** in `naming_lab.py` (UI-free: agents,
runner, spend, stats), `tui_widgets/naming_lab.py` and `overcode name lab`.
Differences from the text below:

- The editor holds the **advice** (the policy, and what people tune);
  `Ctrl+O` switches it to the whole `naming.md` template. The advice saves
  to `~/.overcode/prompts/naming-advice.md`, a new file that stands in for
  `naming.advice` (`naming_policy.user_advice`), so the lab never rewrites
  config.yaml; a repo's `.overcode/naming.md` is still appended per agent.
- Keys: `Ctrl+N` moves between the editor and the rows (Tab is tmux's pane
  toggle in the split), `Ctrl+O` advice/prompt (`Ctrl+P` is forbidden by
  the vscode preset). `Ctrl+D` is a priority binding, so the editor loses
  delete-right on it (Delete still works).
- A call is keyed by the prompt it renders, so a settled draft, a pinned A
  and a draft you return to cost nothing. Inputs are read when the lab
  opens or switches view, not polled.
- Archived agents (the last 30; `--archived N` in the CLI) are checked
  against each other, as if they were the fleet; one with no bundle has no
  inputs and is shown, not run. `gather_inputs(files=)` reads a bundle member
  (`bundle_files`).
- Collisions are two agents getting the same pick (each pick is chosen alone
  against current names, so a pick never equals another's current name).
  Changed and mean length count the names the draft would leave: a locked
  agent keeps its name.
- Enter (twice) on a live row renames through the TUI's rename path with
  `source="human"`; archived rows can't be applied.

Opened from the palette as "Naming lab…". It is a TUI modal with the same
skeleton as the summary prompt lab: an editor above, results below, and
Ctrl+S save, Ctrl+R revert, Ctrl+L load the default, Esc to close (twice
with unsaved edits). It differs where names differ from summaries:

- **It shows the whole fleet.** A name is only good next to the others, so
  the lab shows the name column as the draft would leave it. For each agent
  it shows the current name with its source, the draft's pick, the rejected
  candidates and why (`too long`, `taken by #3`, `repeats repo`), and the
  width against `max_length`. Collisions are highlighted.
- **It runs on recorded agents, not just live ones.** Its inputs are files
  in `agents/<id>/` and the archive, so the lab can rerun advice on the last
  N finished agents (Ctrl+T switches between live and archived).
  The summary lab can't do this, because it reads panes, and a pane is
  gone once the agent ends.
- **Shows two drafts side by side.** Ctrl+D pins the current draft as A,
  and the next edits are B. Rows show A | B, and the header counts names
  that changed, collisions, and the mean length. This is the comparison the
  summary lab lacks.
- **Apply.** Enter on a row renames that agent to the draft's pick, as a
  human choice (`human`, locked).
- **Cost** shows in the header and counts against the naming cap.

For batch evaluation without the TUI:

```
overcode name suggest <agent> [--advice-file f] [--json]   # one agent, prints candidates and the pick
overcode name lab --archived 50 --advice-file a.md --vs b.md --csv out.csv
```

### 10. Phases

| phase | content | stands alone |
|-------|---------|--------------|
| P0 | Restartless rename: id-first identity, alias-aware lookups, `@overcode_id` window option | yes, rename gets better for everyone |
| P1 | `name_source`, TUI marker, `--auto` / `--lock`, migration for old records; the policy check on parent-given names (2a), `name check` / `name policy` | yes |
| P2 | Brief and prompt capture, daemon namer, triggers, choosing the name, notices, ledger spend | needs P0 and P1 |
| P3 | Naming lab, `overcode name suggest` / `name lab` (built) | needs P2 |
| P4 | opencode notices (#533), parent notices, CLI name alignment (Claude `--name` / `/rename` / `custom-title`) | parallel |

Tests:

- P0: unit tests that a hook arriving after a rename finds the agent by id;
  a live-backends step that renames mid-run and checks the next Stop lands.
- P1: layout-matrix rows for the marker.
- P2: replay tests that feed recorded fleets through the namer with a stub
  model, checking the trigger cadence and the name choice (collisions,
  length, the human lock).
- P3: pilot tests for the lab, using the `make_tui` harness.

## Owner decisions (2026-10-20)

1. **Off by default.** It is switched on with `naming.enabled` or a preset.
2. **Names a parent chose are never auto-renamed.** `parent` is treated
   like `human`. Instead the CLI checks them against the policy when the
   parent launches the child, and refuses a name that fails, with
   `--force-name` to override (2a).
3. **Billing codes become ids with renamable names, and soon:** see
   `billing-code-ids.md`. A code that was created just for this agent
   (defaulted, not chosen, and used only by its tree) takes the agent's
   first auto name, if no other code has that name.
4. **The agent does not name itself.** It is told its name: by the rename
   notice, the enhanced-context line, and the CLI's own session name kept
   in step (section 7).
5. **No sister awareness.** Sister support is semi-deprecated.
6. **Permissive rules, advice first** (later the same day). No
   edit-distance or similarity check: `wiki1`, `wiki2`, `wiki3` are
   deliberate names. Every deterministic rule is opt-in; only overcode's
   name pattern and a taken name are always refused. The free-text advice
   is the primary policy, the rules a supplement.
