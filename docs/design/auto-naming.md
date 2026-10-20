# Auto-naming agents (#542)

Status: proposal, 2026-10-20; owner decisions taken the same day (end of file). Builds on `overcode rename` (#478) and the
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

- **Rename restarts the agent.** `RenameMixin.rename` (`rename.py:55`)
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
  (off), `avoid_recent` (off; names archived in the last 24 h). The CLI
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

The daemon decides, because it owns per-agent values (AGENTS.md). It works
from events and adds no polling.

- **The first name** comes at the first Stop after the agent has done
  enough work: by default at least 2 completed turns and either 3 minutes
  of green time or 15k output tokens. The thresholds are configurable.
  A Stop is when the task is clearest, and the agent is idle at that
  moment, so the notice arrives with the next prompt.
- **One refinement** comes once the agent has done 10× that much work.
  It renames only when the namer is confident the task has changed (see the
  `keep` reply below). So `auto` names change at most twice in an agent's
  life (`max_renames`, default 2), and at least 10 minutes apart.
- Never while the name is `human`, while the agent is waiting on a
  permission dialog, or when the agent's tmux session has had a human
  keypress in the TUI in the last 30 s. The last rule is so a name doesn't
  change under someone who is about to type it.
- Calls run on the daemon's worker pool, with a per-day cost cap
  (`naming.cost_cap_per_day`, default $0.50). Spend goes in the ledger
  under the billing code `overcode` (closing the gap the summariser has).
  At about 2 calls per agent with a mini model, 100 agents cost about $0.05.

### 4. What the namer reads

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

The model returns JSON: `{"candidates": ["jwt-refresh", "auth-refresh",
"jwt-fix"], "keep": false, "why": "..."}`, best first. `keep: true` means
the current name still fits, which is how refinements stay rare. Overcode
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
    avoid_recent: false      # names archived in the last 24 h
    llm_check: false         # check the advice with the model too
    warn_humans: false
  first_after: {turns: 2, green_minutes: 3, output_tokens: 15000}
  max_renames: 2
  cost_cap_per_day: 0.50
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
  launcher's ready-line check). Otherwise it waits for the next Stop. A
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
| P3 | Naming lab, `overcode name suggest` / `name lab` | needs P2 |
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
