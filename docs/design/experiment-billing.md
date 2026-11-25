# Experiment billing: agent spend plus the evals they run

Status: proposal + first increment, 2026-11-25 (branch `billing-experiments`,
not merged). Asked for by Mike: measure the full cost of an experiment, the
tokens its agents spend *and* the tokens spent by eval / simulation runs those
agents kick off, rolled up into overcode's budget totals.

## What exists, and what is missing

overcode already bills every *agent* precisely:

- Every agent has a sticky billing code (`codes.py`, an id with a renamable
  name). Children and forks always inherit the parent's code; `rebill` is
  forward only. So a whole tree, at any depth and on any backend, already
  bills to one code.
- The ledger (`ledger.py`) keeps cumulative spend rows per agent segment for
  ever; `totals.json` folds them incrementally; `overcode billing` reports
  per code, for all time or a period.
- Claude background sessions (🌙) are billed as pseudo-agents of their own
  (`claude_bg_ledger.py`): `sid claude-bg:<id>`, `parent` the agent.
- Budgets: a per-agent `cost_budget_usd`, granted out of the parent's;
  `budget_gate` refuses prompts once the agent's *own* estimated cost reaches
  it; `SUB$` / `budget show` sum a subtree.

What is invisible: any token spent **outside an agent's own conversation**.
An agent that runs `python eval.py` which calls the Anthropic API 2,000 times,
a sim harness, an `overcode bash` batch job: none of that reaches the ledger,
the code's totals or the agent's budget. For an experiment that is often most
of the bill.

## 1. The unit of accounting: an experiment is a billing code

**Recommendation: no new entity.** An experiment *is* a billing code.

- Codes already have exactly the propagation an experiment needs: sticky,
  inherited by every descendant agent (Claude Code subagents and Workflow
  fan-outs run inside the agent's own conversation, so their tokens are in
  its spend already; child agents launched with `overcode launch` inherit the
  code; opencode, codex, grok and hermes agents are launched the same way).
- Starting an experiment is `overcode launch --billing-code exp-foo …` (or
  `rebill` an existing root onto it). Ending it is just the agents finishing;
  spend is kept for ever, a code is never "closed".
- Rename/merge already work for codes ("exp-foo" → "exp-foo-v1").
- A second, parallel tag ("experiment" alongside "code") would mean two
  roll-ups to keep consistent, a second propagation rule, and a question for
  every row ("which one does this count under?"). Mike's decisions so far
  make the code the one thing every row carries; this keeps it so.

Optional sugar (open question Q1): `overcode launch --experiment X` ≡
`--billing-code X` plus a registry flag `kind: experiment`, so `overcode
billing --experiments` can list only those. Not needed for the first
increment; nothing below depends on it.

## 2. Non-agent spend ingestion (the hard part)

### Who is spending? Attribution by environment

Every agent's launch line already exports `OVERCODE_SESSION_ID` (all
backends, `launcher._build_launch_cmd_str`). Every process the agent spawns
(its Bash tool, a script, a script's subprocesses, a Workflow step's shell)
inherits it. So **attribution is free** for anything an agent runs directly.

Gaps and how they are closed:

| Process | Today | Attribution |
|---|---|---|
| script run by an agent's shell | `OVERCODE_SESSION_ID` | that agent |
| `overcode bash` job started by an agent | `OVERCODE_JOB_OWNER_ID` only if `parent_launches` | **new**: the job exports `OVERCODE_BILL_TO=<agent id>` whenever it has an owner |
| child agent | its own `OVERCODE_SESSION_ID` | the child (its code is the parent's anyway) |
| anything, explicitly | — | `OVERCODE_BILL_TO=<agent id or name>` set by the user, or `--agent` / `--code` |
| a script run from a plain shell | nothing | `--code exp-foo` (no agent: counts to the code, no budget) |

Resolution order when recording: `--agent`, `--code`, `OVERCODE_BILL_TO`,
`OVERCODE_JOB_OWNER_ID`, `OVERCODE_SESSION_ID`. `OVERCODE_BILL_TO` is added to
the variables a launch line clears (`HOST_SESSION_VARS`), so an agent started
from inside a job is not billed to the job's owner.

The env carries the **agent**, never the code: the code is looked up when the
spend is recorded, so a `rebill` in the middle of an eval moves its later spend
forward, as for the agent itself.

### How the spend gets recorded: options

| Path | Accuracy | Effort for overcode | Change to eval code |
|---|---|---|---|
| A. `overcode spend record` CLI | exact (what the caller reports) | small | one call per batch / run |
| B. stdlib Python helper over A (`SpendMeter`) | exact | small | 2–3 lines: `meter.add_usage(model, resp.usage)` |
| C. JSONL drop-file the daemon tails | exact | medium (tailing, offsets, crash) | append a line |
| D. metering LLM proxy (`ANTHROPIC_BASE_URL`) | exact, zero-touch | large (proxy, streaming, auth, Bedrock SigV4) | none, if the SDK honours the base URL |
| E. provider usage APIs (Admin usage API, CloudWatch) | exact per key, coarse attribution | medium, per provider | none, but no per-agent attribution |

**Recommended primary path: A + B.** One write path (`overcode spend
record`) owns locking, pricing and the ledger; the helper is a thin,
stdlib-only batching client that shells out to it, so it works from an eval's
own virtualenv with no overcode import, and from inside an agent despite the
real-state guard (the CLI is an entry point; a library writing
`~/.overcode` from an eval process would be refused, rightly).

C is a fallback worth adding later for non-Python harnesses that cannot spawn
a process per flush (the CLI already accepts JSON lines on stdin, which covers
most of that). D is the only zero-touch option and the right long-term answer
for "I don't want to touch my eval code", but it is a product of its own
(streaming, retries, Bedrock signing, failure modes that would break evals);
listed as Q5. E is useful for **reconciliation** (does the sum of recorded
spend match the invoice?), not attribution.

### Pricing

- `--usd X`: a cost the caller knows (a provider that reports cost, an
  invoice line). Taken as is, `priced: "reported"`.
- tokens + `--model`: priced via `settings.get_model_pricing` (the
  `pricing.py` table, config overrides, Bedrock regional premium and
  discounts), `priced: "estimate"`. A model the table does not know is still
  priced (global fallback rates) but the row says `priced: "fallback"`, and
  the CLI warns.
- `--provider`: `anthropic` | `bedrock` | …; **default: the agent's own
  provider** (Q3). A Max subscriber's eval script almost always runs on an API
  key, so its spend is real money even when the agent's own is notional; the
  row records the provider so a report can split "notional" from "billed"
  later.
- Prices are fixed when recorded (the row carries `cost`), exactly as agent
  spend is never repriced.

## 3. The ledger: provenance and roll-up

### Rows

External spend is recorded as **runs**. A run is one eval invocation (one
`SpendMeter`, one CLI call without `--run`, or every call sharing a `--run`
id). A run is a ledger pseudo-agent, like a 🌙 session:

```
{"sid": "ext:<run id>", "name": "<label>", "parent": "<agent id or null>",
 "code": "<code id>", "billing_code": "<name>", "seg": 0, "kind": "final",
 "external": true, "provider": "anthropic", "priced": "estimate",
 "source": {"pid": 4242, "host": "…", "cmd": "eval.py"},
 "model": "…", "cost": <cumulative>, "tokens": …, "tok_in": …, "tok_out": …,
 "tok_cache": …, "tok_cw": …, "tok_cr": …, "px": null,
 "base_cost": 0, "base_tokens": 0, "base_usage": null, "t", "seq"}
```

- Each record call adds its delta to the run's cumulative total (kept in
  `ledger/external.json`, under the ledger lock) and appends the run's new
  cumulative row with `kind: final`. The fold already replaces a closed
  segment's share when a newer closing row for it arrives, so **the last row
  wins and nothing is counted twice**, and a crashed eval is simply counted up
  to its last flush: there is no open "running" state to leak.
- A rebill of the run's agent between two records starts a new segment
  (`seg + 1`, base = the cumulative so far) on the new code: forward only.
- An older overcode reading these rows sees ordinary closed agent segments:
  the money is right, they just count as "agents".

### Roll-ups

- **Per code** (`totals`, `report`): external rows add to `cost`/`tokens` as
  any row does, and also to `ext_cost` / `ext_tokens`; they count as `runs`,
  not `agents`. `overcode billing` shows an `evals` column (the external
  share) whenever any code has one; `--json` carries `ext_cost`, `ext_tokens`,
  `runs`.
- **Per agent**: `SessionStats.external_cost_usd` / `external_tokens`, the
  agent's external spend, updated in the same locked write as the ledger row
  (sessions lock → ledger lock, the existing order). The agent's own
  `estimated_cost_usd` is untouched (it stays "what its conversation cost").
- **Charged cost** (`budget_gate.charged_cost`) = own + external. It is what
  every budget check uses: the prompt gate and the daemon's
  `budget_exceeded`, `SUB$` subtree spend (`_subtree_spend`, so a released
  child carries its evals too), `budget reclaim`, `budget show`.
- **Per experiment, with breakdown**: `overcode billing show <code>` lists the
  code's agents and runs, each with its cost, and the agent / eval split.

### Should external spend count against the agent's budget? Yes.

The budget exists to stop an agent spending past what it was given. An agent
that can launch an unmetered `for i in range(10_000): client.messages.create`
can blow through any budget while its own conversation costs $2: evals are
exactly the spend a budget is for. Counting it also makes the parent→child
budget meaningful for experiments: a parent granting a child $20 for "run the
sweep" is granting it for the sweep.

The gate itself is unchanged in kind: overcode refuses the agent's next
*prompt*. It does not kill a running eval. The recorder reports
`over_budget` back to the caller; the helper can raise
(`SpendMeter(stop_on_budget=True)`) so an eval loop can stop itself. Whether
that should be the default is Q2.

## 4. Crash, restart, double counting, retention

- **Crash mid-eval**: counted up to the last flush (the helper flushes every
  30 s, at `close()` and at interpreter exit). No open segment is left behind.
- **Crash mid-record**: the ledger row is appended before `external.json` is
  saved, both under the ledger lock; sessions.json is written after both
  (outer lock). A crash between the row and `external.json` re-bases the
  next delta on the older cumulative: the next row is *lower* than the last
  one written, and since the last row wins, spend between them is lost, not
  doubled. Bounded by one flush; acceptable, noted.
- **Retries**: a caller that retries a flush that actually succeeded counts
  it twice. Not solved in the increment (Q6: an optional idempotency key per
  flush, kept per run).
- **Double counting with agents**: an eval that drives `claude -p` or an
  overcode child agent must *not* also record that spend: overcode already
  reads it. Documented in the helper; the CLI cannot tell.
- **Retention**: ledger rows kept for ever, as all spend. `external.json`
  forgets a run 62 days (`CLOSED_KEEP_DAYS`) after its last record; a run
  recorded to again after that starts a fresh cumulative (its old rows stay
  in the ledger, and the totals have forgotten them too, so nothing doubles).
- **Agent archived while its eval runs**: the record still lands in the
  ledger under the run's code (looked up from the run, else the archive);
  the agent's stats are not updated (it is gone; its budget no longer
  matters).

## 5. What the TUI shows (follow-up, not in the increment)

The TUI computes nothing: the daemon would publish `external_cost_usd` in
`SessionDaemonState` (an absolute value, still between records, so a quiet
fleet sends no deltas). Proposed:

- The cost column shows charged cost (own + evals) so it agrees with the
  budget gate, with a `+` mark when part of it is external; the hover splits
  "agent $x · evals $y". Same width placeholder as today.
- `SUB$` sums charged cost.
- The billing dialog grows an "evals" column, matching `overcode billing`.

Until that lands, the TUI's cost column under-reports agents with evals, and
a budget can show red ("rejected") while the column reads below the budget.
`overcode budget show` shows the split.

## 6. First increment (this branch)

- `external_spend.py`: `record()` — attribution, pricing, run state, ledger
  row, agent stats, over-budget answer.
- `overcode spend record` (flags or JSON lines on `--stdin`), `overcode spend
  helper-path`.
- `spend_client.py`: stdlib-only `SpendMeter` (batching, `add_usage` for
  Anthropic- and OpenAI-shaped usage objects, flush at exit).
- Ledger fold: `ext_cost`, `ext_tokens`, `runs`.
- `budget_gate.charged_cost` used by the gate, the daemon, `_subtree_spend`,
  reclaim and `budget show`.
- Jobs export `OVERCODE_BILL_TO`; launch lines clear it.
- `overcode billing` evals column; `overcode billing show CODE`.

End to end:

```
overcode launch --name exp1-lead --billing-code exp1 --budget 25 -p "…"
# inside the agent (or a job it started):
python eval.py            # uses SpendMeter, or calls `overcode spend record`
overcode billing          # exp1: cost = agents + evals, evals column
overcode billing show exp1
overcode budget show exp1-lead   # spent includes evals
```

## 7. Open questions for Mike (with the default built or recommended)

1. **`--experiment` sugar / `kind: experiment` on codes?** Default: no;
   an experiment is a billing code. Add the flag only if you want
   `billing --experiments` filtering.
2. **Over budget, should the helper stop the eval?** Built: it reports
   `over_budget`; `stop_on_budget=True` raises. Recommend keeping
   opt-in (killing an eval mid-run can waste the spend so far).
3. **Default provider for external spend**: built: the agent's own
   provider, else config's default. Alternative: always `anthropic` unless
   given (an eval on an API key while the agent is on Bedrock).
4. **Notional vs billed split in reports** (Max subscribers: agent spend
   notional, eval spend real). Recommend a later `--by-provider` split in
   `overcode billing`; rows already carry `provider`/`priced`.
5. **Metering proxy** for zero-touch evals: recommend later, opt-in per
   launch (`--meter`), Anthropic API only first.
6. **Idempotency keys** for flush retries: recommend adding when a
   real double count is seen; cheap (`--event-id`, last N kept per run).
7. **TUI**: cost column = charged cost with a `+` mark (section 5)? Recommend
   yes, next increment.
8. **Should an agent's external spend move with a parent's `budget
   transfer`/`reclaim`?** Built: reclaim uses charged cost (what the child
   spent on evals is spent). No other change.
