# Experiment billing: agent spend plus the evals they run

Status: proposal + working increment, 2026-11-25 (branch
`billing-experiments`, not merged). Asked for by Mike: measure the full cost
of an experiment, meaning the tokens its agents spend *and* the tokens spent
by eval / simulation runs those agents kick off, rolled up into overcode's
budget totals.

Revised the same day after Mike's steer: client-side recording is too
fragile to book-keeping errors. The primary path is now a **spend gateway**
on the corp network (Mike builds it; there is no direct API access to
OpenAI or Anthropic there anyway). overcode's job is to put the right
identity in every worker's environment and to talk to the gateway through a
small API. Mike's decisions: the gateway meters **evals and sims only**
(agents' own conversations stay billed from their transcripts), and it
**refuses requests** from an agent that is over budget.

## What exists, and what is missing

overcode already bills every *agent* precisely:

- Every agent has a sticky billing code (`codes.py`, an id with a renamable
  name). Children and forks always inherit the parent's code; `rebill` is
  forward only. So a whole tree, at any depth and on any backend, bills to
  one code.
- The ledger (`ledger.py`) keeps cumulative spend rows per agent segment for
  ever; `totals.json` folds them incrementally; `overcode billing` reports
  per code.
- Budgets: a per-agent `cost_budget_usd`, granted out of the parent's;
  `budget_gate` refuses prompts once the agent's spend reaches it.

What is invisible: tokens spent **outside an agent's own conversation**, such
as `python eval.py` calling an LLM API 2,000 times, a sim harness, or an
`overcode bash` batch job. For an experiment that is often most of the bill.

## 1. The unit of accounting: an experiment is a billing code

No new entity. An experiment *is* a billing code: sticky, inherited by every
descendant agent (Claude Code subagents and Workflow fan-outs run inside the
agent's own conversation and are in its spend already; child agents inherit
the code; every backend is launched the same way). Start one with `overcode
launch --billing-code exp-foo …` (or `rebill` a root onto it). A second tag
next to the code would mean two roll-ups and two propagation rules to keep
consistent. Optional sugar is Q1.

## 2. The spend gateway

```
 overcode (laptop / box)                       corp network
 ┌──────────────────────────┐   PUT /allowances  ┌────────────────────┐    ┌──────────┐
 │ daemon: gateway.sync     │ ─────────────────▶ │  spend gateway     │──▶ │ LLM APIs │
 │  every sync_seconds      │ ◀───────────────── │  (Mike's, OpenAI-  │    └──────────┘
 │                          │   GET /usage?after │   compatible)      │
 │ agent / job panes:       │                    │                    │
 │  OPENAI_BASE_URL=gw      │ ── eval traffic ─▶ │  auth by token,    │
 │  OPENAI_API_KEY=ocg_…    │    (token = key)   │  meter, refuse     │
 └──────────────────────────┘                    └────────────────────┘
```

### Identity: a token per agent, in the env

- Every agent has a gateway token `ocg_<agent id>.<mac>`: its id plus an
  HMAC-SHA256 (128 bits shown) under a secret only this overcode holds
  (`ledger/.gateway_secret`, 0600). It cannot be made up for another agent,
  needs no storage, and survives restarts and renames.
- `billing.gateway.env` in config.yaml names the variables to set, with
  `{token}` (and `{agent_id}`) substituted, e.g. `OPENAI_BASE_URL` → the
  gateway and `OPENAI_API_KEY` → `{token}`. They go in every agent's
  launch line, so every process the agent spawns (scripts, a script's
  subprocesses, Workflow shells) inherits them, and in every `overcode bash`
  job an agent starts. Workers only know the gateway, and know it only by
  the token. The real provider credentials live in the gateway.
- A child agent has its own token. Its spend rolls up to the experiment
  through the code it inherits, and to its parent's `SUB$` as today.
- `overcode spend env <agent>` prints the exports, for a process started
  outside the agent's tree that should still bill to it.

### The API between overcode and the gateway (proposed contract)

Both calls carry `Authorization: Bearer <key>`, the key read from the env
var `billing.gateway.admin_key_env` (default `OVERCODE_GATEWAY_KEY`). overcode
calls the gateway; the gateway never calls overcode, so a laptop behind NAT
or a VPN works.

**`PUT {url}/allowances`**, every sync, the complete set of live agents:

```json
{"agents": [{"token": "ocg_…", "agent_id": "…", "agent_name": "exp1-lead",
             "parent_id": null, "code_id": "c_…", "code_name": "exp1",
             "limit_usd": 7.0, "metered_usd": 2.5}]}
```

- `limit_usd`: how much the token may have spent through the gateway in
  total: the agent's budget less its own conversation's spend; `null` = no
  budget. The gateway refuses (HTTP 402, or 429 with a clear body) a request
  whose token has metered `>= limit_usd`.
- A token **absent** from the last push is unknown: refuse it. That revokes
  finished agents without a separate call.
- `metered_usd` is overcode's view of what the gateway metered for it, for
  the gateway's own drift check (optional).
- The gateway should meter against its *own* running total, not
  `metered_usd`: between syncs overcode's view lags.

**`GET {url}/usage?after=<cursor>&limit=<n>`**: usage events after a cursor:

```json
{"events": [{"id": "e81", "t": "2026-11-25T18:01:02Z", "token": "ocg_…",
             "model": "claude-sonnet-4-5", "provider": "bedrock",
             "input_tokens": 1200, "output_tokens": 300,
             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
             "cost_usd": 0.0081}],
 "next": 81, "more": false}
```

- `next` is an opaque, monotonic cursor (a sequence number works); `more`
  says another page is ready. overcode reads up to 50 pages per sync.
- `cost_usd` is optional. If given (the gateway knows the corp's real
  prices: Bedrock discounts, a negotiated rate), it is taken as is
  (`priced: reported`). Otherwise overcode prices the tokens through
  `pricing.py` at the event's `provider` (default `anthropic`).
- OpenAI names (`prompt_tokens`, `completion_tokens`) are accepted too.

### Recording: exactly once, no client book-keeping

Each sync records the page's events with `external_spend.record_batch`: one
sessions transaction, one ledger lock, **the cursor saved in the same atomic
write as the run state**. So an event is billed once whatever crashes:

- Run state + cursor are saved first, flagged `unrowed`; then the ledger
  rows; then the flag is cleared. A crash between them leaves the rows for
  the next batch to write (it heals any `unrowed` run). A crash before the
  sessions write is healed from the runs' totals (an agent's
  `external_cost_usd` is never below the sum of its runs).
- One sync at a time (`ledger/.gateway_sync.lock`, never waited for): the
  daemon and `overcode spend sync` cannot read the same cursor.
- A gateway failure records nothing; the daemon retries at a tenth of the
  rate and warns once.
- A token that does not verify (forged, or from another overcode) is billed
  to `billing.default_code` as run `gw-unattributed`: the money stays
  visible rather than being dropped.

### The fallback: recording from the eval

Without a gateway (a laptop with direct API access), an eval can still record
its spend itself: `overcode spend record --model … --in … --out …` / `--usd`,
or the stdlib-only helper `overcode.spend_client.SpendMeter`
(`add_usage(resp.usage)`, batched, flushed at exit). Attribution is by env
(`OVERCODE_BILL_TO`, a job's owner, `OVERCODE_SESSION_ID`). Same ledger runs,
same roll-up. This is the fragile path Mike pointed at: an eval that
forgets to record is invisible, and a retried flush can double count (Q6).

## 3. The ledger: provenance and roll-up

External spend is recorded as **runs**: ledger pseudo-agents, like 🌙
background sessions. A gateway run is one per agent (`ext:gw-<agent id>`);
a fallback run is one per eval invocation.

```
{"sid": "ext:gw-<agent>", "name": "gateway (exp1-lead)", "parent": "<agent id>",
 "code": "<code id>", "billing_code": "exp1", "seg": 0, "kind": "final",
 "external": true, "provider": "bedrock", "priced": "reported",
 "cost": <cumulative>, "tokens", "tok_in", "tok_out", "tok_cache", "tok_cw", "tok_cr",
 "px": null, "base_cost", "base_tokens", "base_usage", "t", "seq"}
```

- A run's rows are cumulative and always `final`: the fold already replaces
  a closed segment's share with its newer closing row, so the last row wins
  and nothing is counted twice. There is no open state to leak.
- The code is the agent's at record time. A rebill between records starts a
  new segment on the new code (base = the cumulative so far): forward only.
- An older overcode reading these rows sees ordinary closed segments: the
  money is right, they just count as "agents".

Roll-ups:

- **Per code**: external rows add to `cost`/`tokens` and also to `ext_cost`
  / `ext_tokens`; they count in `runs`, not `agents`. `overcode billing`
  shows `evals` and `runs` columns when any code has some; `--json` carries
  them. `overcode billing show <code>` lists the code's agents and eval runs
  with the agent / eval split.
- **Per agent**: `SessionStats.external_cost_usd` / `external_tokens`
  (sessions.json; old files load with 0). The agent's own
  `estimated_cost_usd` stays "what its conversation cost".
- **Charged cost** (`budget_gate.charged_cost`) = own + external: the
  prompt gate, the daemon's `budget_exceeded`, `SUB$` subtree spend,
  `budget reclaim`, `budget show` all use it.

### External spend counts against the budget, and the gateway enforces it

A budget exists to stop an agent spending past what it was given; an agent
that can launch an unmetered eval loop can blow through any budget while its
own conversation costs $2. So charged cost includes evals, and the gateway
refuses an over-budget token's requests: a real hard stop for eval traffic,
at the one place all of it passes. overcode's prompt gate keeps stopping the
agent's own conversation. The two meet through `limit_usd`. Between syncs
the gateway's own running total enforces, so the overshoot is bounded by the
requests in flight plus what the agent's own conversation spent since the
last push (the limit is budget less own spend as of that push).

## 4. Double counting, retention, crash

- **The agent's own traffic must not use the token.** Mike's call: the
  gateway meters evals only; agent conversations stay transcript-billed. If
  a backend reads the same variables (codex / opencode read
  `OPENAI_BASE_URL` / `OPENAI_API_KEY`), its own calls would reach the
  gateway with the token and be counted twice. Q2 below.
- An eval that drives `claude -p` or launches an overcode child: overcode
  already bills that through transcripts; if that traffic also passes the
  gateway with the parent's token it is counted twice. Same Q2.
- Retention: ledger rows kept for ever. `external.json` (run state, cursors)
  forgets a run 62 days after its last record (`CLOSED_KEEP_DAYS`, as the
  totals do); a gateway run of an agent idle that long restarts its
  cumulative, and since the totals forgot it too nothing doubles.
- An agent archived while its evals still run: its late events land under
  the code it ended on (found in the archive); no stats update.

## 5. What the TUI shows (next increment)

The TUI computes nothing: the daemon publishes `external_cost_usd` in
`SessionDaemonState` (absolute, still between syncs, so a quiet fleet sends no
deltas). Proposed: the cost column shows charged cost with a `+` mark when part
of it is external, and the hover gives "agent $x · evals $y" (same width
placeholder); `SUB$` sums charged cost; the billing dialog gets an evals
column. Until then the cost column under-reports agents with evals, and
`overcode budget show` gives the split.

## 6. Built on this branch

- `gateway.py`: tokens, `worker_env`, `GatewayClient` (stdlib urllib),
  `allowances`, `sync` (lock, pages, cursor, unattributed).
- `config.get_gateway_config` (`billing.gateway`: `url`, `admin_key_env`,
  `env`, `sync_seconds`, `timeout_seconds`; warns, never raises).
- Launch lines and agents' jobs carry `billing.gateway.env`.
- Daemon: `Housekeeping.maybe_sync_gateway`, on a thread every
  `sync_seconds` (a config stat per tick; no per-tick work otherwise).
- `external_spend.record_batch`: atomic batch + cursor, heal.
- Ledger fold: `ext_cost`, `ext_tokens`, `runs`.
- `budget_gate.charged_cost` everywhere a budget is checked.
- CLI: `overcode spend sync | env | record | helper-path`; `overcode billing`
  evals column; `overcode billing show CODE`.
- Fallback: `spend_client.SpendMeter`, `OVERCODE_BILL_TO` in jobs.

Config for Mike's setup:

```yaml
billing:
  gateway:
    url: https://llm-gw.corp.example/overcode
    admin_key_env: OVERCODE_GATEWAY_KEY
    env:
      OPENAI_BASE_URL: https://llm-gw.corp.example/v1
      OPENAI_API_KEY: "{token}"
    sync_seconds: 30
```

End to end:

```
overcode launch --name exp1-lead --billing-code exp1 --budget 25 -p "…"
# the agent runs `python eval.py` (any OpenAI SDK): it reaches the gateway by its token
overcode spend sync              # or wait for the daemon
overcode billing                 # exp1: cost = agents + evals
overcode billing show exp1       # agents vs eval runs
overcode budget show exp1-lead   # spent includes evals; the gateway refuses past the budget
```

## 7. Open questions for Mike (with the default built or recommended)

1. **`--experiment` sugar / `kind: experiment` on codes?** Default: no; an
   experiment is a billing code.
2. **Keeping agents' own traffic off the token** (codex / opencode read
   `OPENAI_*`). Recommend: the gateway env goes under variables the agent's
   backend does not read, plus a per-backend exclude list in config
   (`billing.gateway.skip_backends: [codex, opencode]` keeps the env off
   their launch lines, and their evals use `overcode spend env`). Not built;
   needs your view of which backends run on the corp network.
3. **The gateway API shape** (section 2) is a proposal: change anything to
   suit what you build; overcode's side is two small methods.
4. **Refusal status**: 402 vs 429 with a body naming the agent and its
   budget. Recommend 429 (SDKs surface it, and don't retry 402 forever),
   with `"error": {"type": "overcode_budget_exceeded"}`.
5. **Notional vs billed** in reports (Max: agent spend notional, eval spend
   real). Rows carry `provider`/`priced`; recommend a later
   `billing --by-provider` split.
6. **Idempotency keys for the fallback path** (`spend record` retries).
   Recommend adding only if the fallback sees real use.
7. **TUI** (section 5): charged cost with a `+` mark. Recommend yes, next.
8. **Token exposure**: the token rides in the launch line and in job
   windows (visible in the pane and its scrollback). It only grants the
   agent's own remaining allowance, and only while the agent is live. Fine
   on a single-user box; flag it if panes are shared.
