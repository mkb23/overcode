# Agent observability for 0.6.1: every kind of delegated work

Status: proposal, from a spike on Claude Code **2.1.300** (2026-10-15).
Raw captures: `docs/design/spike-agent-types/captures/` (index at the end).

Today overcode is good at observing the agents it launches itself. Claude
Code now hands work to several other kinds of agent: subagents (foreground,
background, forks), workflows, teammates, background sessions and cloud
sessions. This doc covers what each kind exposes, as verified by running it,
and proposes how overcode should show it. The goal is that a swarm of 40
agents reads as one line, not 40.

---

## 1. What the spike found

Every claim below comes from a real run. Each run used haiku, a temp git
repo, a private tmux socket (`tmux -L spike-*`) and a logging hook injected
with `--settings`, and an env with overcode's variables stripped. A finding
marked **(new)** is not in the hook docs, or contradicts what overcode
assumes today.

### 1.1 Facts that change the design

| # | Finding | Evidence |
|---|---|---|
| F1 | **(new) The Agent tool runs in the background by default.** The schema says "Agents run in the background by default". `run_in_background: false` is the opt-in to block. A plain `Agent(...)` call returns `status: async_launched` at once. | `01-default-agent-call`, `00-tool-schemas.md.txt` |
| F2 | **(new) `Stop` and `SubagentStop` carry `background_tasks`**: `[{id, type: subagent\|shell\|workflow\|teammate, status, description, agent_type\|name\|command}]`, plus `session_crons`. This is an authoritative list of the parent's in-flight work, taken at every turn end. | all runs, `hooks.jsonl` |
| F3 | `SubagentStart` has only `session_id` (the parent's), `transcript_path` (the parent's), `agent_id` and `agent_type`. It has no description, name, tool_use_id or workflow id. Those live in `<sid>/subagents/agent-<id>.meta.json` and in the spawning tool's `PostToolUse.tool_response`. | `02`, `05` |
| F4 | A subagent's own tool hooks (`PreToolUse`, `PostToolUse`, `PermissionRequest`, …) arrive in the **parent's** `session_id` with `agent_id` and `agent_type` set. `SubagentStop` adds `agent_transcript_path` and `last_assistant_message`. | `02`, `03` |
| F5 | **(new) The parent transcript contains none of the subagent's messages.** The subagent's result reaches the parent as a `<task-notification>` user turn carrying `<status>`, `<result>` and `<usage><subagent_tokens>…`. The `cost-state` record, written when the session ends, does include subagents. | `01` |
| F6 | **(new, bug) Today's spend sums over-count.** The transcript writes one line per content block, and every line repeats the whole message's `usage`. `history_reader` adds every line, so it over-counts 1.3–2.0×. Deduplicating by `message.id` (keeping the last line) over the parent plus `subagents/**/agent-*.jsonl` matched Claude's own `cost-state` **exactly** in all 6 print-mode runs. Interactive runs also matched exactly on cache tokens. The few uncached input/output tokens still missing there are side calls that are never written to the transcript, such as session naming. | `spend-reconciliation.txt` |
| F7 | **(bug) Workflow agents are not counted at all.** Their transcripts live in `subagents/workflows/<runId>/agent-<id>.jsonl`, and overcode's glob is `subagents/agent-*.jsonl`, which is not recursive. | `05`, `06` |
| F8 | **(bug) Forks double-count.** A fork transcript opens with a `fork-context-ref {parentSessionId, parentLastUuid, contextLength}` line, then a copy of the parent's last assistant message, with the same `message.id` and the same usage. Deduplicating across files (F6) fixes this. | `04` |
| F9 | Forks are gated: without `CLAUDE_CODE_FORK_SUBAGENT=1`, `subagent_type: "fork"` fails with "Agent type 'fork' not found". With it, `agent_type: "fork"`, meta `isFork: true` and `model: inherit`, and the fork is always a background call. | `04` |
| F10 | **(new) Workflows only report live progress in the journal.** `subagents/workflows/<runId>/journal.jsonl` gets `started {agentId,label,phase}` and `result {agentId,result}` as they happen. `workflows/<runId>.json`, which has per-agent `state`, `tokens`, `durationMs` and `phaseTitle`, is written **only when the run completes**. Hooks report each agent only as `agent_type: "workflow-subagent"`, with no run id. The run id comes from the Workflow `PostToolUse` (`taskId`, `runId`, `workflowName`, `transcriptDir`, `scriptPath`) or from the transcript's directory path. | `06/live-file-watch.jsonl` |
| F11 | **(contradicts docs) Agent teams are not what the docs say.** With `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` there is no `TeamCreate`, `TeamDelete` or team task-list tool, and the Agent tool's `team_name` is "deprecated and ignored". Each session instead gets an implicit team `session-<8 hex>` at `~/.claude/teams/session-<8>/config.json` plus `inboxes/*.json`, deleted when the lead exits. `Agent(name: "alice")` spawns a teammate. `TaskCreated` and `TaskCompleted` never fired. | `07`, `08`, `09` |
| F12 | In-process teammates have `agent_id` `a<name>-<hex>` and **`agent_type` = the teammate's name**. `TeammateIdle {teammate_name, team_name}` fires when one goes idle. In `background_tasks` a teammate has `type: teammate` and stays `running` while idle. `SubagentStart` fires **again** when a message wakes it. | `08` |
| F13 | **(new) tmux teammates are separate Claude processes.** Each runs in a split pane (`tmux_pane_id: %1`, `agent_id: name@session-<8>`) with its own top-level transcript tagged `teamName` and `agentName`. **It does not inherit hooks passed with `--settings`**: none of its events reached the logger. | `09` |
| F14 | Background sessions (`claude --bg`) refuse an untrusted directory. They are registered in **`~/.claude/jobs/<short>/state.json`**, with `state` (`working`, `blocked` or `done`), a one-line `detail`, `tempo`, `inFlight`, `tokens`, `output.result`, `needs` (the pending question, when blocked), `children` (e.g. `{kind: pr, href}`), `respawnFlags` and `linkScanPath` (the transcript). `timeline.jsonl` beside it records each state change. Hooks passed with `--settings` survive, because they are kept in `respawnFlags`. | `10` |
| F15 | `claude agents --json` takes about **0.12 s** per call. It lists interactive sessions (`pid`, `status: busy\|idle`) and background ones (`id`, `state`, `status`). After `claude stop` a session leaves the default list and appears only under `--all`, as `done`. `claude rm` deletes `jobs/<id>`. `claude logs` returns the raw terminal byte stream (ANSI), which has no structure to parse. | `10` |
| F16 | **(new) `~/.claude/sessions/<pid>.json` is a live registry of every interactive Claude** on the machine. Each entry has `status` (`idle`, `busy`, `waiting` or `shell`; `waiting` was seen during an AskUserQuestion), `tmux: "<session>:@<window>.%<pane>"`, `messagingSocketPath`, `name` and `sessionId`. Status changes land in under a second, and reading it costs one file read. | `11` |
| F17 | `SendMessage` to a background agent is queued and delivered "at its next tool round" as a `queued_command` attachment with `origin.kind: coordinator`. If the agent has already finished, the message resumes it, so one task id can notify more than once. | `03` |
| F18 | **(gap in overcode) The `bg_task` obligation never arms for default-async agents or workflows.** The rule requires `tool_input.run_in_background` to be truthy, and the Workflow tool has no such parameter. 👥 still works, because it comes from `SubagentStart`. | `hook_handler.py:63-82` |

The cloud kinds (`Agent isolation: "remote"`, `claude --cloud`) were not
exercised. They leave nothing on local disk to observe beyond the spawning
tool call.

### 1.2 What overcode already has

- Hooks: `SubagentStart` and `SubagentStop` keep a `subagents` map in
  `hook_state.json`. A subagent's events never become the parent's turn,
  but its permission prompt is lifted onto the parent (🟠 with
  `permission_agent_id`). A one-hour staleness timer covers a lost Stop.
- Status: a parent whose turn is over while subagents are live goes 🟡 with
  the 👥 badge ("Waiting on N background agents"). Background shells and
  tasks give 🔌.
- Spend: `history_reader` globs `<sid>/subagents/agent-*.jsonl`, skips the
  isMeta compaction copies, adds each subagent's tokens into the parent, and
  prices each at its own model. This carries the F6, F7 and F8 bugs.
- Tree: `parent_name`, `children_count`, `subtree_cost_usd` and
  `children_rollup` (the 👶 column, `👶 5 2▶ 1📭 1✅ 1❌`). Tree sort and
  `X` collapse apply to overcode's own children only.
- Not used anywhere: `claude agents`, `~/.claude/jobs`,
  `~/.claude/sessions`, `TeammateIdle`, `background_tasks`, workflow
  journals.

---

## 2. Taxonomy

| Kind | Started by | Who can steer it | Survives parent? | Backends | How overcode observes it (source, latency) | What overcode can do |
|---|---|---|---|---|---|---|
| **Subagent, foreground** | `Agent(run_in_background:false)` | parent model only; the parent is blocked meanwhile | no | Claude (opencode and codex have their own task tools, out of scope) | hooks with `agent_id` (≤0.5 s, existing wake path); `subagents/agent-<id>.jsonl` + `.meta.json` (5 s stats cadence) | read only; interrupting the parent stops it |
| **Subagent, background** (the default) | `Agent(...)` | parent model: `SendMessage(to=agentId)`, `TaskStop`; user: the ↓ manage view in the parent pane | no: dies with the session (verified for shells; assumed for agents) | Claude | as above, plus `background_tasks` on each Stop (reconciliation) and the `<task-notification>` status/result in the parent transcript | read only; can type a request into the parent ("stop agent X") |
| **Fork** | `Agent(subagent_type:"fork")`, needs `CLAUDE_CODE_FORK_SUBAGENT=1` | as background | no | Claude | as background; meta `isFork`; transcript opens with `fork-context-ref` | read only |
| **Named agent / in-process teammate** | `Agent(name:"x")` (a teammate when AGENT_TEAMS=1) | parent and other teammates via `SendMessage(to:"x")`, shutdown_request; `TaskStop` by name | no | Claude | hooks (`agent_type` = name), `TeammateIdle`; `~/.claude/teams/session-<8>/` while the lead lives | read only |
| **tmux teammate** | AGENT_TEAMS=1 + `--teammate-mode tmux` | lead via SendMessage; user in its pane | unknown (not tested) | Claude | **not** via `--settings` hooks; its own transcript (`teamName`/`agentName`), `sessions/<pid>.json` (pane, status ≤1 s), team config | its pane is a real tmux pane, so overcode could focus or send-keys to it (not proposed for 0.6.1) |
| **Workflow** | `Workflow(script\|name)`, always background | parent: `TaskStop(taskId)`, resume with `resumeFromRunId` | no | Claude | Workflow PostToolUse (`runId`, `transcriptDir`); `journal.jsonl` (live, per agent with label and phase); hooks per agent; `workflows/<runId>.json` at the end | read only |
| **Background session** | `claude --bg`, or dispatched from `claude agents` | user: `claude attach/stop/rm`; any local session via `SendMessage` | **yes** (Claude's own daemon supervises it) | Claude | `~/.claude/jobs/<id>/state.json` + `timeline.jsonl` (file stat, ≤2 s); `claude agents --json` (0.12 s, for reconciliation only); its transcript (`linkScanPath`); hooks only if they were in its launch flags | **attach** (`claude attach <id>` in a new tmux window), **stop**, **rm**; send with `SendMessage` from an agent (no CLI send) |
| **Cloud session** | `claude --cloud`, `Agent(isolation:"remote")` | claude.ai; `--teleport` | yes | Claude (remote) | nothing local except the spawning tool call | open its URL; out of scope |
| **Foreign interactive session** | a user's `claude` outside overcode | the user | n/a | Claude | `~/.claude/sessions/<pid>.json` (status ≤1 s, tmux pane), its transcript | read only; jump to its pane if it is in our tmux |
| **overcode agent / child** | `overcode launch`, `n`, children | overcode, parent agent, user | configurable | Claude, opencode, codex, grok, hermes… | the full engine | everything (send, kill, restart, …) |

---

## 3. Data model

### 3.1 Two new row classes

1. **Virtual rows**: read-only rows under an overcode agent. They cover
   subagents, forks, teammates, and workflow groups with their agents. They
   are never sent keys, never killed by `x`, never ring the bell, and are
   never counted as fleet agents in totals.
2. **Adopted rows**: Claude background sessions, plus optionally foreign
   interactive sessions, shown as fleet rows with a 🛰 marker. They support
   only actions Claude itself provides (attach, stop, rm).

### 3.2 Identity keys

| Row | Key | Stable across |
|---|---|---|
| subagent / fork | `<overcode_id>/sa/<agent_id>` | the parent's life; resumes keep `agent_id` |
| teammate | `<overcode_id>/tm/<name>` (agent_id `a<name>-<hex>` as alias) | wakes (SubagentStart repeats) |
| workflow group | `<overcode_id>/wf/<runId>` | resume (`resumeFromRunId` creates a new runId; link it with `resumed_from`) |
| workflow agent | `<overcode_id>/wf/<runId>/<agent_id>` | the run |
| background session | `claude-bg/<sessionId>` (display: `daemonShort`) | stop/attach/resume |
| foreign session | `claude-pid/<sessionId>` | the process |

The parent link comes from the hook's `session_id`, which overcode already
maps to an agent (`OVERCODE_SESSION_ID` and `hook_state`). A workflow agent
is linked to its group through the path of its `.meta.json`
(`subagents/workflows/<runId>/…`). That file appears by the time of the
first `PreToolUse`, so the first `SubagentStart` can sit in an "unassigned"
bucket for under a second.

### 3.3 Lifecycle

```
SubagentStart ─▶ live ─(tool hooks refresh last_seen)─▶ SubagentStop ─▶ done
                   │                                          │
                   └── PermissionRequest(agent_id) ─▶ 🟠 ────┘
Agent PostToolUse(async_launched), or an unknown agent's own tool event ─▶ live
done ─(SendMessage resumes it: SubagentStart again)─▶ live
Stop.background_tasks lacks an id that we hold as live ─▶ done (reconciled; replaces the 1 h timer)
SubagentStop or Stop while a shell/Monitor the agent started is still listed ─▶ live 🟡 (it resumes when that ends)
parent SessionEnd ─▶ every live virtual row → done(killed)
```

Retention: a finished virtual row stays visible (dimmed) until the parent's
next `UserPromptSubmit` that comes from the user, not a task-notification,
or for 10 minutes, whichever is later. After that it folds into its group's
✅/❌ counts. History stays in `agents/<id>/subagents.jsonl`, which the
engine appends on each transition.

### 3.4 Lights and the Result column

The six lights keep their meanings. Virtual rows take theirs from the same
rule table, fed by hooks scoped to the agent.

| Kind | 🟢 | 🟡 | 🟠 | 🔴 | ⚪ + Result | ❔ |
|---|---|---|---|---|---|---|
| subagent / fork | live, tool hook within the green window | live, its own bg shell/agent pending (`parent_task_id`) | its PermissionRequest is pending | never (it cannot wait for the user) | SubagentStop: ✅ completed; ❌ failed/killed (task-notification `<status>`); 📭 stopped by parent SessionEnd | started, no tool hook yet |
| teammate | working | — | permission | — | `TeammateIdle` → ⚪ "idle, listening" (no Result until shut down) | — |
| workflow group | any agent 🟢 | all live agents 🟡 | any 🟠 | — | run done: ✅ / ❌ (status from wf json) | — |
| background session | `state: working` | `tempo` waiting on inFlight work | — | `state: blocked`, activity = `needs` | `done` → ✅ with `output.result` as the result text | unreadable state file |
| foreign session | `busy` | — | `waiting` (permission or question; one value covers both) | `idle` (attended at a prompt) | — | `shell` (meaning unverified) |

Bell: **never** for virtual rows. A subagent's permission prompt already
shows on the parent (🟠, existing behaviour), and the parent's episode
rules decide the bell. Adopted background sessions ring at most once per
`blocked` episode, opt-in per row (`B`-style toggle, see §8 Q4).

### 3.5 Spend attribution

Fix the reader first (phase 0):

1. Deduplicate assistant usage by `message.id`, keeping the last line, across
   the parent file and **`subagents/**/agent-*.jsonl`** (recursive). That
   fixes F6, F7 and F8 in one change, and matches `cost-state` exactly.
2. Attribute each `message.id` to the file it first appears in, so a fork's
   copied prefix stays with the parent.

Then:

- **Parent row cost = own + all virtual descendants**, as today, but
  correct. The cost cell can split it on demand: `$4.20 (👥 $3.10)`.
- **Group and virtual rows show their own share.** It is never added to
  anything again, because the parent total already includes it. Adding up
  rows on screen would double count, so the fleet total is computed from
  fleet rows only, never from virtual rows.
- `subtree_cost_usd` (overcode children) is unchanged. Subagents are inside
  each node's own cost.
- Adopted background sessions are separate conversations with separate
  transcripts. They count in the fleet total once, under their own row.
  tmux teammates count the same way (their own transcript), with a
  team-to-lead link for display only.
- During a workflow run, live per-agent tokens come from the deduplicated
  transcript sums (5 s / 60 s cadence). The wf json's `tokens` only cross-check
  them once the run has ended.

---

## 4. TUI: scale by aggregation

### 4.0 Telling the kinds apart (owner question, 2026-10-15)

Once virtual rows exist, a row's kind must read at a glance. A marker
leads NAME: none for an overcode agent or child (unchanged); 👥 for a
subagent (read-only, inside its parent's session); 🧩 for a workflow group;
🌙 for a Claude background session. A KIND column (hidden by default) spells
it out. Until Phase 2, only overcode agents have rows; subagents and
workflows are counts in the parent's 👥 DLG cell, and background sessions
are not shown. Since Phase 3 they are (🌙, KIND `bg`; the mockups below
still draw the earlier 🛰).

### 4.1 Rules

- **One line per parent by default.** A parent with virtual work gets a
  rollup cell, a generalised 👶, in the status-detail column. Nothing else
  changes in its row.
- **Groups before agents.** When expanded, virtual work shows as groups,
  never as a flat list:
  1. one group row per workflow run
  2. one group row per team
  3. one group row for loose subagents ("agents")
  An agent row appears only inside an expanded group.
- **Auto-collapse above N.** A group with more than `N=8` members opens
  collapsed even when its parent is expanded, and shows its top 3 "needs
  you" members (🟠 first, then 🟢 by age) and then "+k more".
- **Phase is a first-class grouping** for workflows. An expanded workflow
  shows phase rows ("Verify 18/30"), and each phase opens on its own.
- Done members fold into counts. Expanding a group shows live members,
  then a single `✅ 25 done · ❌ 1` line, which opens the finished ones on
  demand.

### 4.2 Mockups

Collapsed (the default). This is today's row plus the rollup cell:

```
🟡 review-060     opus  Waiting on 2 background agents   👥 43 ▶15 ✅27 ❌1  🧩1   $4.20
🟢 fix-flaky      opus  Running tests                    👥  2 ▶2                  $0.85
🛰 ⚪ bg:a3f9c1e2  haiku done · "ran migration; PR #512"   ✅                         $0.31
```

Expanded with `→` on review-060:

```
🟡 review-060     opus  Waiting on 2 background agents   👥 43 ▶15 ✅27 ❌1         $4.20
 ├ 🧩 review-060 ▸ 40 agents  phase Verify 18/30   12🟢 3🟡 25✅      ($3.95)
 │   ├ ✔ Find      10/10                                    ✅10
 │   ├ ▶ Verify    18/30   🟠 verify-api-auth  needs approval      ← top "needs you"
 │   │                      🟢 verify-cache-ttl  Read cache.py 12s
 │   │                      🟢 verify-retry      Bash pytest 40s   +13 more
 │   └ · Report     0/1
 └ 👥 agents ▸ 3   🟢 Explore "map engine"  ✅ 2 done          ($0.15)
```

Expanded agent (`→` on a member). Read-only detail comes from the
transcript; there is no pane preview, because a virtual agent has no pane:

```
   🟢 verify-cache-ttl   haiku · workflow-subagent · phase Verify · 1m12s · 14.2k tok
      last: Read src/overcode/cache.py   ·   result: (running)
```

### 4.3 The rollup cell (generalised 👶)

The cell is `👥 <total> ▶<working> <🟠n> ✅<ok> ❌<fail>`, with zero counts
left out, as in 👶. A trailing `🧩k` gives the number of workflow runs. 👶
keeps meaning overcode children, and 👥 now means "all virtual work". Both
come from the same `rollup()` helper in `child_lifecycle.py`, generalised to
take `(light, result)` pairs. The engine computes and publishes it, and the
TUI only renders it. For the fleet header, aggregate the lights of fleet
rows only.

### 4.4 Keys

`X` already toggles a parent's children in tree mode. The proposal is to
extend it to virtual groups, so it works in every sort mode. The new actions
below each need a palette entry (AGENTS.md); the keys are free in the
`default` preset, but the presets still need checking:

| Key | Action | Notes |
|---|---|---|
| `→` / `←` | expand / collapse the focused row (parent → groups → phases → agents) | tree convention; free today |
| `X` | collapse or expand everything under the focused parent | existing; now includes virtual groups. *As built: fleet-wide expand/collapse all ("Phase 2 as built", 7)* |
| `v` | cycle swarm detail (*as built on `f`*): `off` (rollup only) → `groups` → `needs-you` (expand only groups with 🟠, or with 🔴 for adopted rows) | per-session preference |
| `b` | jump to attention | existing; now also lands on a 🟠 virtual member and focuses its parent's pane |
| `enter` on a virtual row | focus the parent's pane | a virtual row has no pane of its own |
| `enter` on 🛰 bg row | open `claude attach <id>` in a new tmux window of the overcode session | through `app.split`, not ad hoc |
| `x` on 🛰 bg row | confirm, then `claude stop <id>` | `rm` only from the palette |

---

## 5. Timeline: aggregated rows for swarms

Today the timeline draws one row per agent from `agent_status_history.csv`.
For swarms it draws one **density row per group**:

```
review-060      ▁▂▅▇█▇▆▆▇█▇▅▃▂▁·····   (height = live members, colour = worst light)
 └ 🧩 Find      ▂▅█▇▃▁
   🧩 Verify          ▃▆█▇▇█▇▅▃▂
fix-flaky       ████████▆▆▆▆█████
```

- **Encoding:** the glyph height (`▁▂▃▄▅▆▇█`) shows how many members are live
  in that bucket, scaled to the group's peak. The colour shows the worst
  light in the bucket, in the order 🟠 > 🟢 > 🟡. A bucket with any ❌
  finish gets a red underline. The parent's own row is unchanged.
- **Drill-down:** expanding a group in the list expands it in the timeline
  too, with the same fold state. A member row is the existing per-agent
  bar, capped at 20 members plus a "+k" line.
- **Data:** the engine already writes history on change. It also records
  group bucket counts per 30 s timeline cadence:
  `agents/<id>/swarm_history.csv` with `ts, group_key, n_green, n_yellow,
  n_orange, n_done, n_failed`. It writes one line per change, not per tick,
  so a quiet swarm writes nothing. Member intervals come from
  `subagents.jsonl`.
- **Episodes:** virtual rows get no episodes and no bell (§3.4). The
  parent's episode is unchanged, so a subagent's permission prompt still
  opens the parent's 🟠 episode, as today.

---

## 6. Engine implications

### 6.1 Sources and cost

| Source | What | Cadence | Cost at scale |
|---|---|---|---|
| hook events (existing wake path) | start/stop, tool activity, permission, teammate idle, `background_tasks` | ≤0.5 s attended | none extra: same `hook_state.json` stat. The `subagents` map grows; cap the serialised map at the live set plus the last 200 done, with the rest in `subagents.jsonl`. |
| `<sid>/subagents/**/*.meta.json` | description, label, phase, isFork, name, toolUseId | on SubagentStart (one read) | 1 read per agent |
| `subagents/workflows/<runId>/journal.jsonl` | live workflow membership and per-agent result | stat each live run on the wake loop; tail on change | 1 stat per live workflow |
| `workflows/<runId>.json` | final per-agent tokens and state, run status | once, when it appears | — |
| transcripts (deduped) | tokens and cost per virtual row | existing stats cadence (5 s / 60 s), **live rows only**; done rows frozen | the incremental tail reader already exists |
| `~/.claude/jobs/*/state.json` | background sessions | stat the dir every 2 s; read changed files | ~1 stat per job |
| `~/.claude/sessions/*.json` | foreign interactive sessions, panes | stat every 2 s (opt-in) | ~1 stat per session |
| `claude agents --json` | reconciliation only (startup, then every 60 s at most) | — | 0.12 s per call; never on the hot path |

Polling `claude agents --json` would cost a process spawn per poll. The
jobs and sessions files hold the same data, and checking them costs a few
`stat` calls. Hooks give ≤0.5 s for in-session work. So the CLI is only a
cross-check, never the source.

### 6.2 Publishing without no-op deltas

- `AgentView` gains a `swarm` field: `{rollup: {...}, groups: [{key, kind,
  title, phase, counts, cost_usd, started_at}]}`. It holds absolute times
  and counts only (no countdowns), so it changes only on transitions.
- Member rows are **not** in the default snapshot. A view subscribes to
  `swarm:<agent_id>` when the user expands a parent. The engine then sends
  that parent's members as `delta.virtual {key: partial}`, using the same
  set/unset/removed shape and the same property test. A collapsed swarm of
  400 agents costs one rollup delta per transition.
- Rate-limit: a workflow whose agents finish in a burst still sends one
  delta per engine tick, because counts are coalesced per tick.
- Adopted 🛰 rows are ordinary `AgentView`s with `kind: claude_bg`, so the
  existing delta path covers them.

### 6.3 Freshness contract additions (for `test_engine_contract.py`)

| Output | Attended | Unattended |
|---|---|---|
| Virtual row light (hook-driven) | ≤ 0.5 s | ≤ 2 s |
| Workflow phase and progress | ≤ 1 s (journal stat) | ≤ 2 s |
| Background session state | ≤ 2 s | ≤ 10 s |
| Virtual row tokens and cost | 5 s | 60 s |

---

## 7. Phased plan for 0.6.1

| Phase | Slice | Why first |
|---|---|---|
| **0. Correct spend** (smallest, ships alone) | dedup by `message.id` across parent + recursive `subagents/**`; tests built from the capture skeletons; reconcile against `cost-state` in a test fixture | Today's numbers are wrong: over-counted by up to 2×, workflows missing, forks double-counted. Every later phase builds on spend. |
| **1. Rollup cell** | hook handler records `background_tasks`, meta lookups and workflow run ids; engine publishes `swarm.rollup`; 👥 cell `👥 43 ▶15 🟠1 ✅27 ❌1 🧩1`; reconcile live subagents from `background_tasks` (drop the 1 h timer); arm `bg_task` for default-async Agent and Workflow (F18) | The smallest useful view: one line per parent stays honest about what it is waiting on. No new rows. |
| **2. Expandable groups** | `→`/`←`, workflow group and phase rows, top-3 "needs you", read-only member detail, `swarm:<id>` subscription | Gives drill-down without flooding the list. |
| **3. Background sessions as 🛰 rows** | jobs-dir watcher, state mapping, attach/stop/rm through `app.split` | Different source, independent of 1–2. |
| **4. Swarm timeline** | `swarm_history.csv`, density rows, linked fold state | Needs phase 1's counts history. |
| later | foreign sessions from `sessions/*.json`; tmux teammates (no hooks: transcript plus registry); cloud | Smaller audience, and less certain data. |

---

## 8. Decisions (owner, 2026-10-15)

1. **Background sessions:** shown only when their `cwd` is under a repo
   the fleet uses.
2. **Interactive Claude sessions overcode did not launch:** not shown;
   opt-in only.
3. **Finished virtual rows:** kept for a configurable time, default 30 s
   (`observability.finished_row_seconds`), then folded into the parent's
   rollup.
4. **A blocked background session rings the bell.**
5. **Teams** are just another group kind.
6. **Cost:** the parent's own spend and its subagents' are shown
   separately in the hover text.
7. **Phase 0 recomputes history** for live agents and for archived agents
   whose transcripts still exist; the rest keeps its figures.
8. **Hooks through a settings file** (so re-spawned `claude` processes stay
   visible): later, not in the first slices.

Phase 0 stays on release/0.6.1; it is not cherry-picked to main.

---

## Phase 1 as built

- `src/overcode/swarm.py`: the hook side folds SubagentStart/Stop,
  TeammateIdle (now registered), the Agent and Workflow `PostToolUse`
  responses, `<task-notification>` prompts and `background_tasks` into the
  parent's hook state (`subagents` live map, `swarm` record: done map
  capped at 200, workflow runs, teammate names, background-launch ids).
  The daemon side is `rollup` (published as `swarm_rollup`) and `in_flight`
  (the armed rule's count).
- Reconciling: the parent's Stop ends every subagent its list leaves out
  (no foreground call can be running then); a SubagentStop's list ends only
  always-background work (workflows, teammates, `async_launched` agents),
  so parallel foreground agents are safe. Once a list has been seen there
  is no timer; the 1 h timer stays for hook state written before it.
- A workflow counts once in the light ("Waiting on 1 workflow"), whatever
  its agents do; its agents count in the 👥 total. An idle teammate counts
  in the total but not in flight.
- ✅ unless a notification's `<status>` or a foreground call's response
  says otherwise (❌). SessionEnd (exit or /clear) clears the record rather
  than marking live work killed: the conversation it belonged to is over.
- Background shells in `background_tasks` are left to the pane's status bar
  count, as before: nothing fires when a shell exits, so a list entry could
  hold the parent yellow after it ended.
- `observability.finished_row_seconds` (default 30) is read by
  `config.get_observability_config()`; nothing uses it until phase 2's rows.
- Bell: virtual work has no episodes; a test replays a busy swarm through
  the episode recorder and finds no bell before the parent's own last Stop.

---

## Phase 2 as built

What the person sees: `→` on a parent with a 👥 rollup expands its
delegated work into read-only rows right under it (the DLG cell's leading
space becomes `▸` / `▾`); `j`/`k` walk them, `→`/`←` go a level in or out,
`←` on the parent collapses. See `docs/tui-guide.md`, "Delegated work".

- **Hook side** (`swarm.py`): a member's last tool call (`activity`, one
  short line from its `PreToolUse`), a workflow agent's run (from
  `SubagentStop.agent_transcript_path`), each run's `started_at` /
  `ended_at`, a done entry's `type` and `started_at`, and the parent's
  `transcript_path` (only once it has delegated). No new hook events, no
  extra writes.
- **Rows** (`swarm.virtual_rows`, pure): group rows (`<p>/wf/<run>`,
  `<p>/tm`, `<p>/sa`), phase rows (`<p>/wf/<run>/ph/<n>`) and member rows
  (`<p>/wf/<run>/<agent>`, `<p>/tm/<name>`, `<p>/sa/<agent>`), as §3.2.
  Each holds absolute times, counts, names and its own share of the spend;
  none holds `last_seen` or a countdown. A finished member (and a finished
  run's group) keeps its row for `observability.finished_row_seconds`
  (30 s) after it ended; the function returns the earliest such deadline.
  Group counts always include every finished member.
- **Details** (`swarm_files.py`): description, type, phase and model come
  from `<transcript>/subagents/[workflows/<run>/]agent-<id>.meta.json`, read
  once per agent (a missing one is retried at most every 2 s); a workflow's
  membership, labels, phases and per-agent results from its
  `journal.jsonl`, re-read when its (mtime, size) moves. Only for expanded
  parents.
- **Engine** (`swarm_publisher.py`, `engine_protocol.py`,
  `engine_socket.py`): snapshots gained a `virtual` section keyed by row
  identity, diffed and applied like `agents` (`virtual_set` /
  `virtual_unset` / `virtual_removed`; the protocol property test covers
  it; an older view ignores it). A view sends `{"t": "swarm", "agents":
  [...]}` with the parents it expanded (restated on reconnect); the server
  reports the union over visible views. Rows are built on every publish and,
  between ticks, at each wake step when a subscription changed, a deadline
  passed or a watched journal moved. They go to views only, never to the
  state file. `SessionDaemonState.subagent_cost_usd` (default snapshot) is
  the delegated share of the agent's spend, for the $ hover.
- **TUI** (`tui_actions/swarm.py`, `swarm_view.py`,
  `tui_widgets/swarm_rows.py`, `columns/render_virtual.py`): one
  `SwarmRows` widget per expanded parent, mounted after its row, its lines
  drawn on the agent rows' column grid (9 below), redrawn only when they
  change. The highlight
  on a virtual row is `app.swarm_cursor`; the parent's row keeps Textual
  focus and the split's pane. With it set, agent actions (send, kill,
  restart, rename, fork, sleep, …) are refused with a notice, through one
  `run_action` guard (keys and palette alike), and Enter focuses the
  parent's pane. `→`/`←` have palette entries (help lists them).
- **Kind markers** (§4.0): fleet rows unchanged (no marker); member rows
  👥, workflow groups 🧩 (`Wf` emoji-free). The KIND column (hidden below
  full detail, and while every row is the same kind) says `agent` or
  `child`.
- **Bell**: virtual rows have no episodes; a pilot test publishes a 🟠
  member and sees no ring and no 🔔.

Choices where the design left room, or the data disagreed:

1. **Groups travel with members, under the subscription.** §6.2 put group
   summaries in every snapshot; a collapsed parent draws only the rollup,
   so nothing about groups is published until it is expanded. One fewer
   field in the default snapshot, no cost for a collapsed swarm.
2. **The done line opens, capped** (owner, 2026-10-17; first built
   closed). Finished members show for 30 s, then only in the group's
   `✅ n done · ❌ k ▸` line. `→` on it subscribes to `<group key>/done`
   beside the parent's id (same `swarm` message, so the union, the wake
   check and the reconnect restatement needed no change; an older engine
   matches no agent with it). The publisher then lists that group's most
   recently finished members, `observability.done_rows` (50) of them
   counting those still within their 30 s; the TUI draws them under the
   line, newest first (and nowhere else while it is open), then
   `+k older` from the group's counts. No time window: a window would
   need a deadline per row and publish a change as each aged out; a count
   moves only when a member finishes. Members already folded out of the
   hook state (beyond `DONE_KEEP` = 200 per parent) and a finished
   workflow run's group (gone 30 s after the run) cannot be reopened.
3. **No running durations.** A live member's detail shows its start time
   (`since 12:03:10`), a finished one its run time: lines change only when
   rows do, so the TUI needs no clock for them.
4. **Lights without a green window.** A live member is 🟢 unless it waits
   for approval (🟠), on its own background work (🟡) or is an idle
   teammate (⚪); there is no ❔ "no tool hook yet" state (nothing in the
   hook state tells the first tool call apart, and it would last under a
   second).
5. **Phases are journal-driven.** "Verify 18/30" is finished/started in
   that phase as the journal tells it; a phase appears once its first agent
   starts (the script's `phases` list is not parsed). A workflow agent is
   put in its run by the journal, else by the run its `SubagentStop`
   named, else (live) the newest running run.
6. **Expansion is saved** (owner, 2026-10-17; first built per TUI).
   `TUIPreferences` gained `swarm_detail` (the `f` rule), `swarm_expanded`
   (per-agent overrides of it, `{id: bool}`) and `swarm_opened` (groups,
   phases, done lines, `{key: bool}`). An old file loads with the
   defaults; a value of the wrong type is dropped on load, and entries of
   agents that are gone are pruned on save (as `collapsed_parents`).
   Details lines and the cursor are not saved.
7. **Keys** (owner, 2026-10-17). `→`/`←` act on one row: delegated work
   level by level, and in tree sort a parent's children (`←` folds them
   once its delegated work is collapsed, `←` on a child moves to its
   parent, `→` unfolds). `X` is now *expand/collapse all*
   (`toggle_expand_all`): with anything open (an expanded agent, or in
   tree sort an unfolded parent) it collapses everything, else it expands
   every agent with a rollup and unfolds every parent; it sets per-agent
   overrides and leaves the `f` rule alone. Its old per-parent action,
   `toggle_collapse_children`, stays as a palette command without a key
   (so a `keys:` entry naming it still works; nothing goes in
   `REMOVED_ACTIONS`). The swarm-detail cycle (§4.4's `v`) is on `f`
   (`cycle_swarm_detail`): `v` is the VSCode preset's ctrl+o, and `f` was
   free in both presets. `off` (default: only what `→` opens), `groups`
   (every agent whose rollup has live work: running, waiting, idle or a
   running workflow; it closes again when the work ends) and `needs_you`
   (agents whose rollup has a 🟠, with only the groups and phases holding
   one open by default). The rule is applied in the TUI from the
   published rollups (a view decision, not agent state); `→`/`←` override
   it per agent, and `f` drops every override and group choice so the new
   rule shows as it is. `b` still does not land on a 🟠 member (its parent
   already turns 🟠 and `b` finds the parent).
8. **Teammates group by "team"**, one group per parent (Claude makes one
   implicit team per session, F11).
9. **Rows on the grid** (owner, 2026-10-17; first built as free-text
   lines). Virtual rows render through the summary columns and align cell
   for cell with the agent rows. `swarm_view` still decides which lines
   exist, their order and keys, but a `SwarmLine` now carries data (the
   published row, kind marker, label, fold, SubAgents counts, summary,
   tree connector), not text. `ColumnContext.for_virtual(line, parent)`
   (`columns/render_virtual.py`) copies the parent's view settings (emoji,
   $ mode, name width, fleet gates) and sets the row's own values,
   clearing the parent's agent data; `cell_segments` sends a virtual
   context to the column's `virtual_render` only (never the agent
   renderer, which reads a real session), so every registered column
   either fills a cell or yields its placeholder. Filled: status (the
   light), ST and UPT (since `started_at`, ticking on the row clock; the
   run time once finished, still), RES (`render_task_result`, result text
   in the hover), SUBA (group, phase, done and `+k` counts, glyph then
   count, no total, laid out as the agent's cell), KIND
   (sub/fork/team/wf), NAME (`├─`/`└─` indented below the parent as tree
   sort indents a child, marker, title), MDL, TOK, $. Placeholder: a dim
   `-` at the right edge of the widest text in that column (`column_edges`,
   worked out with the widths), blank where every cell is blank, so it
   sits where a value would. The fleet gate (`visible`) is not asked for
   a virtual row: a subagent's result or model shows even when no agent's
   does (the column takes width only while one is on screen);
   hide-when-uniform still applies. The summary column maps the `l` mode
   (`virtual_summary`): ai_short what it does / its result, ai_long type ·
   kind · phase then the same, orders its task (full title), last_command
   its last tool, annotation and heartbeat `-`; structure lines show
   their counts and phase progress in every mode. Widths and edges are
   computed over agent and visible virtual rows
   (`_recompute_cell_column_widths`), the NAME width grows for the
   virtual titles up to the detail level's cap, and the agent rows repaint
   only when that moved them. `SwarmRows` builds cells when the lines,
   cursor, columns or the parent's settings change, pads when widths,
   mode or width move, and on the 250 ms row clock redraws only live
   members' durations; Textual repaints only when the text changed. A
   member's detail line stays free text, starting at the NAME column.

For Phase 3 (🛰 background sessions as ordinary agent views): the row
class goes into the default snapshot's `agents`, not `virtual`, so extend
`monitor_daemon.py` (a jobs-dir source beside the detector),
`SessionDaemonState` (a `kind` field), `ColumnContext.agent_kind` and the
KIND column (`columns/registry.py`, `columns/render_identity.py`), and the
read-only guard's action set in `tui_actions/swarm.py` (`READ_ONLY_BLOCKED`)
for the actions a 🛰 row allows. `swarm_view` and `SwarmRows` stay for
virtual rows only.

---

## Phase 3 as built

What the person sees: a Claude background session whose directory is
inside one of the fleet's agents' gets a 🌙 row (`Bs` emoji-free) right
under the agent it came from, with Claude's own record of it as its light
and status text. `Enter` attaches, `x` stops (second press), the palette's
"Remove background session" removes (confirmed). A blocked one rings. See
`docs/tui-guide.md`, "Claude background sessions".

- **Source** (`claude_bg.py`): `JobsScanner` lists `$CLAUDE_CONFIG_DIR` (else
  `~/.claude`)`/jobs` and stats each `<short>/state.json`, re-reading only
  a file whose `(mtime_ns, size)` moved; a half-written file keeps the last
  good read, a removed job (`claude rm`) is dropped. `claude agents --json`
  is not used at all (no reconciliation pass): the files carry everything
  the rows need.
- **Lights** (`job_light`, §3.4): `working` + tempo `active` (or
  `starting`/`resuming`/`adopted`) 🟢; `working` + tempo `idle` 🟡 ("Waiting
  on N background tasks" from `inFlight`, badge 🔌); `blocked`, or tempo
  `blocked`, 🔴 with `needs` as the status text (badge 📥); `done` ✅,
  `failed`/`crashed` ❌, `stopped` 📭, all ⚪ with `output.result` as the
  Result reason; anything else ❔. The state and tempo values were read from
  Claude Code 2.1.302's own code, not just the spike.
- **Attribution** (`attribute`), strongest first: (1) the agent's hooks saw
  it: the hook handler now notes, in the agent's hook state
  (`foreign_sessions`, newest 20), every other conversation whose hooks ran
  under the agent's name, and whether it is a registered background session
  (`find_job`: `jobs/<sessionId[:8]>`, then a scan, at most every 30 s per
  id); (2) its `sessionId` or `resumeSessionId` is one of the agent's own
  conversations; (3) its `cwd` is inside the agent's `start_directory` (the
  deepest wins); (4) its `cwd` is a git worktree of the repository an agent
  works in (follow-up 3 below). None of these: not shown (decision 1). Only
  this tmux session's live agents (not terminated or done) count.
- **The /bg confusion, fixed at the source**: an event from a conversation
  that is a registered background session no longer touches the agent's
  hook snapshot (status, obligations, swarm): only `foreign_sessions` is
  written. Nested CLIs get the same treatment since follow-up 1 below.
- **Engine** (`monitor_daemon.py`, `engine_wake.py`): `ClaudeBgSource`
  refreshes at every full tick and, between ticks, at every wake step that
  is due (1 s watched, 5 s not; a finished row's time running out). The
  rows are rebuilt only when a state file moved, the agents changed, a
  deadline passed or a spend read is due. Each row is a
  `SessionDaemonState` with `kind: "claude_bg"` and `claude_bg: {id,
  session_id, parent_id, via, cwd}`, keyed `claude-bg:<sessionId>`, merged
  into the snapshot's `agents` for views only: the state file (CLI,
  supervisor, sister API) never holds one, so nothing that acts on agents
  can reach it. They are republished only when one moved.
- **Spend** (`BgSpend`): its own transcript (`linkScanPath`) plus its
  subagents, deduplicated by `read_tree_stats`, priced per file's model, at
  the stats cadence while live, once more when it finishes, then frozen
  (until it runs again). It counts once, under its own row; never added to
  the agent it nests under. Billed to the ledger since follow-up 2 below.
- **Bell**: each row's colour goes to its own recorder at every refresh
  (`RecorderStore.record_adopted`): bells and visits as for an agent (rings
  once its red has stood G, at most once per stretch; a session already
  blocked when the engine started does not ring), no episode log.
- **TUI**: `BackgroundSession` (a `Session` subclass, never in
  sessions.json) is built from each view; the list is re-applied only when
  a row comes, goes or is renamed. `parent_session_id` nests it under its
  agent in every sort; 👶 counts leave it out; the timeline has no row for
  it (since Phase 4 it has one); the engine is never told it has focus.
  KIND says `bg`.
- **Actions** (`tui_actions/claude_bg.py`, `tui_split.py`): through
  `app.split`. `attach_background` opens `claude attach <id>` (overcode's
  env unset first) in a `bg:<id>` window of the agents session and shows it
  below; focusing the row shows that window, else an `oc-bg` placeholder
  explaining the keys. `run_claude` runs `claude stop|rm <id>` (30 s bound)
  in a worker. `x` on a finished row points at the palette instead (there
  is nothing to stop). Other agent actions (`BACKGROUND_BLOCKED`) refuse
  with a notice. The `bg:` and `oc-bg` windows count as overcode's own.
- **Config**: `observability.finished_row_seconds` (30) and
  `observability.background_sessions` (true).
- **Freshness** (`test_engine_contract.py`): state ≤ 2 s watched / ≤ 10 s
  not; views hold still between changes; spend at the stats cadence. A
  quiet jobs directory sends no delta and re-reads nothing (test); a quiet
  scan of 20 jobs under 10 agents costs about 0.1 ms.

Choices where the design left room:

1. **Marker 🌙**, per §4.0's latest text (the mockups' 🛰 predates it).
2. **Identity `claude-bg:<sessionId>`**, not `claude-bg/<sessionId>`: a `/`
   would read as a virtual row under a parent in the swarm cursor's keys.
3. **Rows go to views only.** §6.2 said ordinary `AgentView`s; they are,
   on engine.sock, but not in the state file, so `overcode list`, the
   supervisor (which would try to steer a blocked one) and sisters do not
   see them.
4. **The bell is not opt-in** (decision 4 over §3.4's per-row toggle).
5. **No `claude agents --json` cross-check**: nothing the files lack was
   found to need it.
6. **No live run**: a `claude --bg` needs the real login and Claude's
   per-user background service, which an isolated HOME cannot provide
   without touching the person's real state. The state values were checked
   against Claude Code 2.1.302's code and the spike capture instead.

### Follow-ups as built (owner decisions after phase 3)

**1. Only the agent's own Claude steers it** (`hook_handler._place`,
`conversation_warning.py`). A nested Claude (an agent runs `claude -p` in
its Bash tool; it inherits OVERCODE_*) no longer moves the agent's status,
obligations or subagent list.

- *Discriminator: the CLI's process id.* Claude Code builds every hook's
  environment as `{...process.env, ...k9e(...), CLAUDE_PROJECT_DIR}` where
  `k9e` sets `CLAUDECODE=1`, `CLAUDE_CODE_SESSION_ID`,
  `CLAUDE_CODE_CHILD_SESSION=1` and **`CLAUDE_PID: String(process.pid)`**
  (read from the 2.1.302 binary: the hook executor and the Bash tool share
  `k9e`). `/resume` and `/clear` switch conversations inside the same
  process (the `~/.claude/sessions/<pid>.json` registry, F16, is keyed by
  pid with a changing `sessionId`). Observed live from inside an overcode
  agent: its Bash tool's env carries `CLAUDE_PID` = the agent's `claude`
  process (parent of the shell), `CLAUDE_CODE_SESSION_ID` = its
  conversation. `CLAUDE_CODE_CHILD_SESSION` / `CLAUDECODE` do *not*
  discriminate in a hook: `k9e` sets them for the agent's own hooks too.
  The spike captures carry payloads only (no env), so they cannot show it;
  no live nested run was made (it would need the real login).
- The hook handler records the agent's own CLI pid (`cli_pid` in hook
  state) from every event of its own conversation, and places each foreign
  conversation once (rechecked every 30 s until settled), in
  `foreign_sessions[sid].kind`: `bg` (a registered job, as before);
  `nested` (its `CLAUDE_PID` descends from the agent's: one `ps -eo` walk);
  `own` (same pid as the agent's, or the agent's CLI is dead: a `claude`
  started by hand in its pane); `unplaced` (neither, or the agent's own pid
  not seen yet). `bg`, `nested` and `unplaced` write only
  `foreign_sessions` (and skip the session record entirely once placed).
  `own` steers: the record moves to the conversation (`adopt`, aged now so
  an old resumed transcript is not called superseded) unless another live
  agent owns it (`shared`: the row follows it, the record stays). Backends
  whose hooks have no `CLAUDE_PID` (codex, opencode, grok, hermes) are not
  placed and behave as before (#500).
- *The warning* (owner: "if resuming into an unknown agent can a warning
  appear?"): `conversation_warning` in hook state, `{kind: adopted | shared
  | unplaced, session_id, previous?, owner?, at}`. The detector appends a
  🔀 badge (`Cv`; `conv_adopted` / `conv_shared` / `conv_unplaced`) to the
  status detail for 30 min after `at`, beside the light; the hover says
  "resumed a conversation overcode did not start; now tracked as this
  agent's", "in a conversation another agent owns; not adopted, restart
  returns to the old one", or "a conversation overcode can't place ran in
  this agent's name; its events are ignored". `overcode doctor` adds an
  `unknown_conversation` warning for a day, e.g. "resumed conversation
  1a2b3c4d, which overcode did not start: now tracked as this agent's (was
  9f8e7d6c) (at 14:02)".
- Known gaps: a nested CLI resuming the agent's *own* conversation id
  (`claude -p --continue` in the agent's directory) still reads as the
  agent (it is not foreign); right after an upgrade, before the agent's
  first own event records its pid, a nested CLI reads as `unplaced` (a
  false warning for 30 min).

**2. Background-session spend in the billing ledger** (`claude_bg_ledger.py`).
Each adopted session is a ledger "agent" of its own: `sid`
`claude-bg:<sessionId>`, `parent` the agent it nests under, that agent's
billing code (children inherit; a child agent's session bills the child's
code), rows marked `claude_bg: true`. Cumulative rows as for agents: a
`checkpoint` as soon as it has spent anything, then at most every
`ledger.checkpoint_minutes` when it moved; `final` when it finishes, or when
its row goes unfinished (agent ended, `claude rm`), closing at what it
really spent (its transcript read again, not the last checkpoint). A
finished session that spends again (resumed) gets an `open` row in a new
segment based on where the last closed; a rebill of its agent writes
`close` + `open`, moving only later spend. A session whose `sessionId` is
one of its agent's own conversations is skipped (the agent's own spend
already reads that transcript). The segment state (seg, closed, base, last
values, code, which engine wrote it) lives in `ledger/claude_bg.json`, so a
restart neither re-bills nor reopens; only the engine that opened a segment
closes it when its row is gone. Synced after every rebuild of the rows and
at every housekeeping pass; planned against the last-read state (one stat),
locked only to write. `overcode billing` counts them among the code's
agents and in a `background` column (`"background"` in `--json`; totals and
period reports alike). The 🌙 row's own $ moves at the stats cadence (5 s
watched / 60 s not) and holds still between reads (TestLive).

*Forked sessions count only what they add* (2026-10-17, Claude Code
2.1.302, live). `claude --bg --resume X` appends to X's transcript under
X's id (the agent's own conversation: skipped, as above), but
`--fork-session`, and `/bg` from an interactive session, write a new
conversation whose transcript starts with a copy of X's whole history:
the same `message.id`s, timestamps and usage. Read alone, that copy was
billed again under the 🌙 row. Now `BgSpend` reads a session against its
*fork origins* (`claude_bg.fork_origins`: the agent's own conversations,
the job's `resumeSessionId`, and the agent's 🌙 sessions created before
it, each the transcript of that id beside its own) and the copied prefix
contributes nothing (`TranscriptRegistry.fork_prefix`, below in the cost
model note). A segment billed before the fix (the copy included) reads
lower on its next pass: the ledger gets a `correction` row for it
(`ledger.correction_rows`, open or closed segment), never a lower
cumulative row. The feature had not shipped, so no historic bg rows exist
outside development machines.

**3. Worktree placement** (`claude_bg.repo_of`, `_by_repo`; owner: pick a
behaviour, revise with experience). A session whose `cwd` is a git worktree
outside every agent's directory nests under the agent working in the same
repository: `git rev-parse --git-common-dir` (the main checkout's `.git` for
every worktree alike), asked once per directory and remembered (a directory
in no repository is asked again at most every 60 s), so no git call per
tick. Of several agents in that repository: the one in the main checkout,
else the shortest directory path, then the name. A directory that holds the
session still wins (rule 3 before rule 4). The row is `via: "repo"` and
takes the agent's repo name, with no branch.

---

## Phase 4 as built

What the person sees: with the timeline on (`t`), an agent that delegated
work gets a density strip under its row (`└ 👥 swarm`): height is how many
members were live, colour the worst light, red underline where one failed,
`↑n` the peak. Expanding the agent in the list (`→`) unfolds the strip into
one per group (`└ 🧩 review`, `└ 👥 team`, `└ 👥 agents`); `←` folds it back.
🌙 background sessions get ordinary timeline rows. See `docs/tui-guide.md`,
"Delegated work in the timeline".

- **History** (`swarm_history.py`, `SwarmHistoryWriter`): the daemon calls
  `record` for each agent it detects (full and quick ticks, beside the
  status history), with the hook state and rollup the detector just read.
  It does nothing unless the hook state's timestamp or the rollup moved,
  or a keepalive is due; then it counts the groups (`group_counts`:
  `swarm.virtual_rows` on the hook state alone, no journals, no finished
  rows kept) and appends a row for each group whose counts changed, plus
  the whole swarm (group `*`). Columns: `timestamp, agent, session_id,
  group, kind, title, running, yellow, waiting, idle, ok, failed,
  workflows`. Groups are the virtual-row keys without the parent
  (`wf/<run>`, `tm`, `sa`). A live group gets a keepalive row every 5 min
  (`KEEPALIVE_SECONDS`); a group that goes away while live (SessionEnd
  cleared the record, the agent left the fleet: `forget`) gets one closing
  row with nothing live. A swarm with nothing live writes nothing.
- **Rotation**: the hourly housekeeping pass runs `rotate_and_retain` on
  `swarm_history.csv` with the status history's settings
  (`history_retention.status_history_rotate_mb` / `_max_days`).
- **Reader** (`SwarmHistoryFile`, `read_swarm_history`): mtime/size cache,
  tail reads, a full read on a shrink (rotation), keyed by (agent session
  id, group) so a renamed agent keeps its strips. A live row before the
  window within `STALE_AFTER_SECONDS` (two keepalives) is carried to the
  window's left edge. The timeline's existing 30 s worker reads it beside
  the status history: one more incremental read of one file.
- **Slots** (`density`, `glyphs`): a point stands until the next one, or
  `STALE_AFTER_SECONDS` while live (a stopped daemon is not live for
  ever). Height is the **time-weighted mean** of live members over the
  slot's covered time, scaled to the strip's peak; colour the worst light
  (🟠 > 🟢 > 🟡 > idle); a slot where `failed` rose is red and underlined.
- **TUI** (`status_timeline.py`): `swarm_strips` picks the strips in the
  list's fold state; `set_swarm_fold` follows `app.swarm_expanded`, called
  at the top of `_sync_swarm_rows` (the one line in `tui_actions/swarm.py`),
  and repaints only when a parent whose strips are on screen changed. No
  file is read on a fold. Strip labels share the name column (it widens to
  fit them, up to its 30-cell cap).
- **🌙 rows**: the daemon logs each background session's status to
  `agent_status_history.csv` on change and keepalive (none once finished),
  keyed `claude-bg:<sessionId>`; the TUI no longer filters them out of the
  timeline.
- **Cost** (scratch benchmark, M-series laptop): a quiet tick over 50
  parents each with a 40-agent swarm, 0.08 ms; a hook event on a 400-agent
  swarm, 0.8 ms (the group count); first read of 600 rows 7.6 ms, a cached
  read 0.3 ms; a timeline render with 11 strips 1.9 ms, 34 group strips
  3.2 ms (renders happen on the 30 s refresh and on a fold change only).
  Tests: a quiet swarm over 300 ticks counts once and writes once
  (`test_engine_contract.py`), a fold reads no file and a fold on a parent
  without strips does not repaint (`test_swarm_timeline_tui.py`).

Choices where the design left room:

1. **One file per tmux session, not `agents/<id>/swarm_history.csv`.** The
   timeline reads one file incrementally, as for the status history, and
   rotation is shared. Rows carry the agent's session id.
2. **Columns are the rollup's counts**, not §5's `n_green, n_yellow,
   n_orange, n_done, n_failed`: `running, yellow, waiting, idle, ok,
   failed`, plus `workflows` (running runs) on the whole-swarm row.
3. **A keepalive while live.** "One line per change" alone cannot tell a
   reader the state at its window's edge or that a daemon stopped; a
   quiet (nothing live) swarm still writes nothing.
4. **Group counts without journals.** The history must exist before anyone
   expands the parent, and journals are read only for expanded ones; a
   workflow agent goes to the run its SubagentStop named, else the newest
   running run. Phases are not in the history (no strip per phase).
5. **The strip sits under the parent**, which keeps its own status bar
   unchanged (§5's mockup drew the density in the parent's row).
6. **Height is a mean over the slot**, per the owner's mean-not-median
   rule; a 10 s burst in a 3 min slot reads low. The peak is the `↑n`
   label, not a central line.
7. **Member rows are not drawn** (§5's "a member row is the existing
   per-agent bar, capped at 20"): members have no status history of their
   own, and per-member rows would need intervals from `subagents.jsonl`,
   which the hook side does not write yet. Groups are as deep as it goes.

---

## Appendix: capture index

`docs/design/spike-agent-types/captures/`, scrubbed (paths become `~`,
`$SPIKE` and `<proj>`; no emails, org ids or message content; transcripts
reduced to record skeletons):

| Dir | What |
|---|---|
| `00-tool-schemas.md.txt` | model-reported input schemas: Agent, SendMessage, TaskStop, ListAgents, Workflow |
| `01-default-agent-call` | an `Agent` call without flags runs async (F1) |
| `02-foreground-subagent` | `run_in_background:false`: SubagentStop comes before the parent's PostToolUse; tool_response carries usage and toolStats |
| `03-background-subagent-with-SendMessage` | bg agent + SendMessage delivery, nested bg shell with `parent_task_id`, `background_tasks` |
| `04-fork` | fork with `CLAUDE_CODE_FORK_SUBAGENT=1`; `fork-context-ref` |
| `05-workflow-small`, `06-workflow-slow-live-watch` | workflow files, journal, wf json; a 0.5 s file watch showing what is live and what is end-only |
| `07`–`09` | teams: print mode (no tools), in-process teammates (TeammateIdle), tmux teammate (own process, no hooks) |
| `10-background-session` | `--bg` hooks, `agents --json` running, done, stopped and `--all`; jobs `state.json`; `logs` excerpt; stop/rm |
| `11-interactive-session-registry` | `~/.claude/sessions/<pid>.json` status transitions idle→busy→waiting→busy→idle |
| `12-background-subagent-own-shell` | 2.1.301: subagents background their own shell and end their turn; interim "waiting on its own background work" notifications |
| `13-busy-parent-subagent-own-shell` | 2.1.302 via `overcode launch`: the same while the parent runs a 40 s command, so the interim notifications wait in its queue; the subagent's `backgroundTaskId` is still in its `SubagentStop`'s `background_tasks` |
| `14-background-subagent-progress-summaries` | 2.1.302: a long-running background agent's progress summary (every ~30 s) fires `PreToolUse` under its own agent id with no `agent_type`, refused with "No tools needed for summary" |
| `spend-reconciliation.txt` | naive vs dedup vs `cost-state` for 9 runs |
| `_harness/` | the logger, runners and inspectors used (the hook logger is `hooklog.py`) |
