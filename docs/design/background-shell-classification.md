# Background shells: services, batch work and watchers

- **Status:** Proposal / Not implemented
- **Date:** October 2026
- **Issue:** [#515](https://github.com/mkb23/overcode/issues/515)
- **Builds on:** #507 (the status model where yellow means "idle at the prompt, but work in flight elsewhere will wake it")

## Problem

Since #507, an agent sitting at the prompt while it has background shells running shows **yellow** with a `bg_task` badge ("background shell or task running"). The evidence is Claude Code's status bar (`· N shells ·`), because no hook fires when a background shell exits. Live-verified on Claude Code 2.1.286: a background Bash's PreToolUse and PostToolUse land about 70 ms apart, and its completion arrives later only as a fresh UserPromptSubmit.

That badge treats every background shell the same, but they mean very different things for the question yellow answers: *will this agent pick itself back up without you?*

| Kind | Examples | Finishes? | Wakes the agent? | What overcode should show |
|---|---|---|---|---|
| **Service** | dev servers, `npm start`, `vite`, `uvicorn`, `serve --forever`, `tail -f` | Never, by design | No | A neutral "server up" badge. Not work in flight: an agent whose only background shell is a service is effectively idle. |
| **Batch** | builds, test suites, benchmarks, data jobs | Yes | Yes, when it ends | Yellow, as today |
| **Watcher** | `until … \| grep -q …; do sleep N; done`, `overcode follow X`, `gh run watch N`, `sleep N && check` | When its condition comes true | Yes, when it fires | Yellow while its target is alive. **Stuck** when the target has ended and the watcher is still polling. |

### The case that prompted this

An agent stayed yellow for about 6h40m. Its only background shell was a hand-rolled wait loop:

```sh
until overcode jobs tail <job> -n 40 | grep -qE 'expected marker|Error|Traceback'; do sleep 3; done
```

The job it watched had never started (see *Related bug*). The job's pane showed only a shell parse error, which matches none of those words, so the loop polled every 3 s indefinitely. The agent looked busy the whole time, and nothing pointed at the dead watcher.

## Design

### 1. Know which shells are alive, and what they run

The status-bar count says *how many* shells are running, never *which*. The process table says both. On macOS and Linux, each Claude Code background shell is a child of the agent CLI's process:

```
claude --session-id …            ← the agent (pid known to overcode)
└─ /bin/zsh -c source …/shell-snapshots/… && eval '<command>' …
   └─ sleep 3                    ← a poll loop's current iteration
```

overcode already tracks the agent's pid for CPU and memory sampling (`process_resources.py`). Listing its children, and parsing the `eval '<command>'` back out, gives the live shells with their commands and ages. It can also be joined to the PreToolUse that launched each one (`tool_input.command`, `description`, `run_in_background`) by command text. This replaces the status-bar count as the source of truth, with the count kept as a fallback where the process walk isn't available (e.g. agents inside containers).

### 2. Classify each shell

Cheapest signal first. Each later step runs only when the earlier ones are inconclusive.

1. **Rules on the command and description.** Claude's `description` field is often explicit ("Start dev server", "Wait for CI").
   - **Service:** `serve`, `start`, `dev`, `--watch`, `--forever`, `tail -f`, `docker compose up`, a `--port` flag with no terminating step.
   - **Watcher:** `until`/`while` loops around `sleep`, `overcode follow`, `overcode jobs tail` inside a loop, `gh run watch`, `gh pr checks --watch`, `kubectl wait`.
   - **Batch:** everything else.
2. **Runtime behaviour**, from the same process walk:
   - A **listening socket** under the shell (`lsof -a -p <pids> -iTCP -sTCP:LISTEN`) means service.
   - A **recurring `sleep` child**, a new pid every few seconds, means a poll loop.
   - **CPU time increasing** means it's doing work. Near-zero CPU for a long time, outside a poll loop, means it's waiting on something.
   - **Output growing**: Claude writes each background shell's output to a task file. Growth means alive and producing.
3. **Target liveness, for watchers.** Extract the target from the command and check it:
   - `overcode follow X` / `overcode jobs tail X` → the agent or job's state
   - `gh run watch N` → the run's state
   - `kubectl wait …` → leave as unknown

   A watcher whose target has ended (or never started) but is still polling is **stuck**. That would have flagged the case above within seconds of the job failing.
4. **An LLM classifier for whatever is still ambiguous.** Use the summarizer path that already exists, prompted with the command, description, age and runtime signals, returning `service | batch | watcher` plus a target if it's a watcher. Cache the result per command hash, so each distinct command is classified once.

### 3. What the person sees

- **DTL badges** split the current `bg_task`:
  - ⚙️ batch (yellow, wakes the agent)
  - 👀 watcher (yellow)
  - 🖥 service (neutral: doesn't make the agent yellow on its own)
  - ⚠️ stuck watcher
- **Colour:**
  - services alone don't hold an agent yellow;
  - batch and live watchers do;
  - a stuck watcher shows a warning, and possibly escalates to red with "watcher stuck: target ended" (open question).
- **Hover** on DTL lists each shell: command, kind, age, CPU, and for watchers what they're watching and whether it's still alive.

### 4. Prevention

Detection catches stuck watchers; better tools stop agents writing them.

- **Add `overcode jobs wait <job> [--until REGEX] [--timeout D]`.** It exits 0 when the pattern appears, and non-zero when the job ends or fails first, or the timeout passes. It never outlives its target. A watcher written this way can't get stuck, and its target is explicit.
- **The overcode skill** already tells agents to run servers as `overcode bash` jobs (visible in the jobs list and monitor bar) rather than Claude background shells. It should also steer agents to `jobs wait` / `follow` instead of hand-rolled loops. Then Claude's own background shells are mostly batch and watcher work, which is the easy part to classify.

## Related bug

`job_launcher.py` builds the job's tmux command as one line:

```python
f"echo '│ Command: {command}' && "   # raw command inside single quotes
…
f"eval '{escaped_cmd}'; "             # this one is escaped
```

A job command containing a single quote (e.g. `…; echo 'done'`) breaks the quoting of the whole line. The job never runs, `overcode jobs _complete` never fires, and with unlucky contents, fragments of the command run unquoted in the header. The job name and directory are interpolated the same way. **Fix:** build the header lines with `shlex.quote`. This is independent of the rest of this design and worth fixing first.

## Open questions

1. Should a service alone keep an agent yellow? Proposed: **no**. Show it, but the agent is idle.
2. Should a stuck watcher turn the agent red, or only add a warning badge?
3. Should overcode ever kill a stuck watcher itself? Proposed: **no**. Flag it, and let the person or the agent decide.
4. Where should the process walk run: in the daemon's per-tick resource sampling (where the pid is already known) or in a slower housekeeping pass? Shells change rarely, so every 10–30 s is probably enough.
5. Containers and remote sisters: the process walk only sees local processes. Fall back to the status-bar count there.
