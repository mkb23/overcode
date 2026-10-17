# Cost model, 0.6.1: Bedrock, subscriptions, reconciliation

Status: proposal, 2026-10-15. Research: an audit of overcode's cost
pipeline, a Bedrock pricing study (AWS Price List published 2026-10-12)
and a subscription study (Claude Code 2.1.300 on a Max 20x account), plus
the agent-types spike (`agent-observability-0.6.1.md`, F5–F8).

overcode is used heavily through Amazon Bedrock in a corporate setting, and
by people on Claude Max. Its dollar figure today is "tokens × an Anthropic
API price", which is wrong in both places: too low on regional Bedrock
profiles, meaningless as a bill on a subscription, and over-counted
everywhere (transcript duplication). This document says what each figure
should mean, how to compute it, and how to check it against the real bill.

## 1. Principles

1. **Three kinds of number, never mixed.**
   - **Billed $**: money someone pays per token (Anthropic API, Bedrock,
     subscription usage credits).
   - **Notional $**: what the work would cost at API rates (a subscription's
     included usage). Always labelled "API-equivalent".
   - **Quota %**: share of a subscription window (5-hour, weekly, Fable
     weekly) consumed.
2. **Estimate, then reconcile.** The live figure is an estimate from tokens.
   Reconciled figures come later from the provider's own records and sit
   *beside* the estimate (adjustment rows); the permanent ledger is never
   rewritten.
3. **Fix the estimator before calibrating.** A price-table error is a bug to
   fix, not a factor to absorb (§5.4).
4. **Every figure says where it came from**: the price table version, the
   billing mode, and "reconciled through <date>" when it is.

## 2. What is wrong today (audit summary)

| # | Problem | Effect |
|---|---|---|
| 1 | Transcript lines repeat a message's usage per content block; no `message.id` dedup | Claude spend ×1.3–2.1 |
| 2 | Workflow transcripts (`subagents/workflows/…`) not read; fork's copied first message counted twice | some spend missing, some doubled |
| 3 | All cache writes priced at the 5-minute rate (1.25×); Claude Code writes mostly 1-hour caches (2×) | cache writes ~37% low |
| 4 | Price rows: Haiku 5.5 falls to the `haiku` row ($1/$5, real $0.10/$0.50); Sonnet 5.5 cache read $0.20 (real $0.10 since 2026-10-07); Fable 5.1 cache read $1.00 (real $0.25) | up to 10× off per model |
| 5 | Haiku 5.5's >100K-prompt tier and Sonnet 4's >200K tier not modelled | long prompts low |
| 6 | Bedrock geo/in-region profiles (`us.`, `eu.`, `apac.`, single region) cost 1.1× list; only `global.` is list. The prefix is lost (sessions record the bare model) | Bedrock ~9% low |
| 7 | `bedrock.discount` applied on top of `model_pricing:` overrides | net rates discounted twice |
| 8 | grok: `inputTokens` already includes cached tokens, counted again | grok tokens/energy high |
| 9 | Summarizer and supervisor spend never reach the ledger; opencode child sessions not read | small gaps |
| 10 | Subscription usage polled from the TUI (not the engine), one account, no history; never linked to spend | nothing to calibrate against |
| 11 | No billing-mode notion: a Max user sees API-rate dollars as if billed | misleading |

Items 1–3 were fixed in Phase 0 of the observability design; 4–8 in Phase A (§8a).

## 3. Billing modes

Each agent has a **billing mode**, detected at launch and confirmable in
config:

| Mode | Detected from | $ meaning |
|---|---|---|
| `anthropic-api` | API key auth | billed $ at Anthropic list price |
| `bedrock` | `CLAUDE_CODE_USE_BEDROCK`, `msg_bdrk_` ids | billed $ at the Bedrock rate for the routing (global / geo / in-region), times the organisation's discount |
| `vertex` | `CLAUDE_CODE_USE_VERTEX` | billed $ at Vertex rates (regional premium to confirm) |
| `subscription` | OAuth login; plan from `~/.claude.json` `oauthAccount` (`organizationType`, `seatTier`, rate-limit tier) | notional $ plus quota %; **credits** periods are billed $ |
| `other` | non-Claude backends | the backend's recorded cost when it has one, else an estimate |

Bedrock routing is recorded at launch from the model id or inference-profile
ARN overcode passes, or `ANTHROPIC_BEDROCK_REGION_PREFIX` and region,
because transcripts carry only the bare model.

## 4. The estimator

Priced **per request**, not per sync total:

```
cost = Σ over unique message.id:
         in × p_in + out × p_out + cw5m × p_cw5m + cw1h × p_cw1h + cr × p_cr
       with p = rate(model, mode, routing, tier(prompt size)) × discount(mode, model, date)
```

- **Dedup** by `message.id` across the parent and every subagent transcript,
  recursively (Phase 0).
- **Cache TTL** from `usage.cache_creation.ephemeral_1h/5m_input_tokens`;
  fall back to the 5-minute rate when absent.
- **Tiers**: a request's prompt size (input + cache read + cache write)
  picks the long-context row (Haiku 5.5 >100K, Sonnet 4 >200K).
- **Price table as data**, with a source and date per row, generated from
  the AWS Price List (`AmazonBedrockFoundationModels`, which also gives the
  global and geo columns) and checked against Anthropic's pricing page. The
  hand-written table becomes a fallback, never an override of a more
  specific generated row. `overcode models refresh` updates it; `doctor`
  warns when a used model has no row or the table is older than 30 days.
- **Discounts** apply once: `model_pricing:` entries are list prices unless
  marked `net: true`.
- **Side calls** Claude Code never writes to transcripts (titles,
  suggestions, summaries; "typically under $0.04 a session") are not
  estimated; reconciliation measures them as coverage (§5).
- Costs stay frozen at the rate in force when the tokens were read
  (conversation spend accrues), so a price-table fix applies forward; a
  table correction can be back-applied with an explicit `overcode billing
  reprice --since` that writes adjustment rows.

## 5. Bedrock

### 5.1 Attribution: getting AWS's bill split by billing code

Two options, both launch-time and admin-assisted:

- **Application inference profiles (recommended).** One tagged AIP per
  (billing code × model × routing). overcode keeps a map
  `bedrock.profiles: {<billing_code>: {opus: arn, sonnet: arn, haiku: arn}}`
  and launches the agent with `ANTHROPIC_MODEL` and
  `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` set to that code's ARNs, so
  subagents and background calls land on tagged profiles too. Needs
  `CreateInferenceProfile` (an admin, or `overcode bedrock profiles create`
  run by one) and the tag activated in the payer account. A rebill takes
  effect at the next launch.
- **IAM session tags.** Assume a role with `billing_code=<code>` per agent
  (`awsCredentialExport`); shows in CUR 2.0 as `iamPrincipal/billing_code`.
  No per-model resources; needs `sts:TagSession`.

Always (cheap, no admin): set `OTEL_RESOURCE_ATTRIBUTES=overcode.billing_code=…,overcode.agent=…`
per agent, so a corporate OpenTelemetry collector can split Claude Code's
own token metrics by billing code.

### 5.2 Measurement sources

| Source | Grain | Latency | Join key | Use |
|---|---|---|---|---|
| Transcript (overcode today) | request | live | `requestId` (= `x-amzn-requestid`) | the estimate |
| Claude Code `cost-state` line | session | session end | session id | estimator check (§5.4) |
| OTel `api_request` events | request | seconds | `request_id` | coverage incl. side calls (`query_source=auxiliary`) |
| CloudWatch `AWS/Bedrock` token metrics | account × region × model id (or AIP) | minutes | none | daily token totals |
| Invocation logs | request | minutes | `requestId` | request-level join, coverage (governance decision: logs full prompts) |
| CUR 2.0 / Cost Explorer | hour/day × usage type × tag | ≤24 h, final at invoice | tag / profile | billed dollars, discount |

### 5.3 Reconciliation: `overcode cost reconcile`

Offline first (no AWS credentials in overcode): the user exports a CUR 2.0
or Cost Explorer CSV and runs `overcode cost reconcile --cur file.csv
[--period 2026-10]`. Optional live pulls (CloudWatch, invocation logs) via
boto3 when configured.

| Check | overcode | AWS | Tells you |
|---|---|---|---|
| tokens | ledger tokens by UTC day × model × type | CloudWatch / CUR usage quantities | coverage (side calls, callers outside overcode) |
| public $ | ledger estimate | CUR `pricing_public_on_demand_cost` | price-table accuracy (should be 1.00 ± 2%) |
| net $ | ledger estimate | CUR `net_unblended_cost` | the organisation's discount |
| requests | transcript `requestId`s | invocation-log `requestId`s | which requests overcode missed |

### 5.4 Calibration

`k = k_price × k_coverage × k_discount`, each a ratio of sums per model per
closed period (≥7 full UTC days, read ≥3 days after it ends, recomputed when
the invoice is final).

- `|k_price − 1| > 2%` → a doctor warning naming the model: fix the table.
- `k_discount` (and optionally `k_coverage`) become dated, versioned
  multipliers, clamped (0.5–1.2), applied only above a minimum spend.
- Applied as **adjustment rows** in the ledger; the raw estimate stays.
- The UI shows "estimated" or "reconciled through 2026-10-31".

## 6. Subscriptions (Pro, Max, Team/Enterprise seats)

### 6.1 Data

- Windows: 5-hour, weekly (all models), weekly Fable (Max); only "% used"
  and a reset time are exposed. Shared with claude.ai, Desktop and Cowork.
- Sources, best first: statusline `rate_limits` (fractional %, per response,
  needs a statusline wrapper); the `anthropic-ratelimit-unified-*` headers
  (same data, not logged by Claude Code); `GET /api/oauth/usage`
  (undocumented, rate limited, whole percents, but includes `extra_usage`,
  `limits[]` with the Fable scope, and a weekly breakdown by surface:
  `claude_code`, `chat`, `cowork`, `other`).
- Usage credits: billed at API rates after a window runs out (and Fable may
  bill credits directly). Detected from `extra_usage.used_credits` deltas.

### 6.2 Collection moves to the engine

The daemon polls once per account (≤ every 5 minutes, backing off on 429),
keeps a history (`~/.overcode/usage/<account>.jsonl`: t, window %s, resets,
credits used, surface breakdown), and publishes the latest sample; the TUI
renders it (AGENTS.md: the TUI computes nothing).

### 6.3 Three figures per agent and billing code

1. **API-equivalent $** (notional; labelled), from the estimator.
2. **Quota share.** For each interval between samples, the window's change
   ΔU is split across agents by their weighted tokens in that interval
   (weights = list prices until calibrated). Unobserved usage (other
   devices, chat) is an explicit **unattributed** row, never spread; the
   surface breakdown lets the `chat` and `cowork` share be removed first.
   Resets restart the interval.
3. **Amortised cost**: seat price ÷ ~4.35 weeks ÷ 100 = $ per weekly % (Max
   20x ≈ $0.46 per 1%); an agent's weekly % × that. Unused capacity is shown
   as "unused plan value", not spread. Credits periods add **billed $** at
   API rates.

### 6.4 Calibration

Regress ΔU(weekly, `claude_code` surface) on per-model, per-token-type token
deltas with non-negative least squares over 2–4 rolling weeks, shrunk
toward the price ratios when data is thin; drop intervals with resets,
credits or plan changes; treat known limit changes (e.g. 2026-05-06) as
breaks. The output is model weight ratios with confidence intervals, which
replace the list-price weights in §6.3.

### 6.5 Reconciliation

| Check | Pair | When |
|---|---|---|
| estimator | overcode per-session $ vs transcript `cost-state` `totalCostUSD` | session end |
| attribution | Σ attributed ΔU vs observed ΔU (coverage %) | each sample |
| plan | `claude_code` surface % vs overcode-attributed weekly % | daily |
| money | credits-period estimate vs `extra_usage.used_credits` / invoice | monthly |

## 7. Ledger and display

- Rows gain `mode` (`anthropic-api`, `bedrock`, `subscription`, …),
  `notional` (bool), `routing`, `pricing_version`, and for subscriptions
  `q5h`, `q7d`, `q7d_fable` (attributed % deltas). Adjustment rows
  (`kind: adjust`) carry calibration and repricing; corrections from Phase 0
  use the same mechanism.
- `overcode billing` shows billed $, notional $ and quota % in separate
  columns, with "estimated" vs "reconciled through".
- TUI cost cell: billed `$1.20`; notional `≈$1.20` (dim); quota mode shows
  `3.1%w`. Hover gives all three and the source.
- `overcode doctor` gains a cost section: price-table age and missing rows,
  estimator vs `cost-state` drift, last reconciliation, coverage.
- Currency: subscriptions may bill in another currency (`extra_usage` here
  is GBP); a `currency:` setting with a dated FX rate for display.

## 8. Phases

| Phase | Content |
|---|---|
| A (now) | Phase 0 of the observability work (dedup, workflows, 1-hour caches); correct price rows; per-request tiers; Bedrock routing premium; discount-once; grok fix; estimator check against `cost-state` in doctor |
| B | billing modes and labels; usage polling in the daemon with history; ledger fields; TUI/CLI display of the three kinds |
| C | `overcode cost reconcile` (CUR/Cost Explorer CSV import); quota-share attribution with the unattributed row |
| D | calibration (Bedrock factors; subscription NNLS weights); adjustment rows |
| E | Bedrock attribution: AIP map and launch wiring, `OTEL_RESOURCE_ATTRIBUTES`, optional session tags |

## 8a. Phase A as built (2026-10-16)

Phase 0 (dedup, workflows, 1-hour caches) shipped earlier. The rest of
Phase A:

- **Price table** (`price_table.py`, `data/claude_prices.json`): one row
  per Claude model id with `source`, `as_of`, the Bedrock `regional`
  multiple and an optional `long_context` tier. Generated by
  `scripts/refresh_price_table.py` from a local copy of the AWS Price List
  (`AmazonBedrockFoundationModels`, us-east-1, published 2026-10-12;
  `global` column = list), plus an overlay for what AWS does not say (the
  Haiku 5.5 100K threshold, from Claude Code's changelog; Sonnet 4's
  long-context rates, from `AmazonBedrockService`). Lookup order:
  `model_pricing:` rows, the table by exact id (Bedrock, Vertex and
  opencode spellings normalised), the family rows, models.dev. A family
  row can no longer shadow a model's own row.
- **Rows corrected**: Haiku 5.5 $1 / $5 / cr $0.10 → $0.10 / $0.50 / cr
  $0.01 (cw $0.125, 1 h $0.20); Sonnet 5.5 cache read $0.20 → $0.10; Fable
  5.1 cache read $1.00 → $0.25. Every other Claude row was audited against
  the AWS global column and agrees; models the AWS list does not price
  plainly (Mythos 5, Mythos Preview: no global column) have no row.
- **Per-request tiers**: the transcript readers keep the part of each count
  spent by requests whose prompt is past the model's threshold (`long_*`;
  `lin`/`lout`/`lcw`/`lcr`/`lcw1h` per conversation); a ten-price row
  prices that part at the tier. Haiku 5.5 (>100K) and Sonnet 4 (>200K)
  only: AWS lists no long-context rate for Sonnet 4.5, and Claude Code's
  docs say the 1M models other than Haiku 5.5 have no premium. The burn
  rate keeps base rates.
- **Bedrock premium**: per row from the AWS data (1.1 from the 4.5
  generation on, 1.0 for older models), applied unless the routing is
  `global`. Routing is recorded at launch (`Session.bedrock_routing`: model
  id, else `ANTHROPIC_MODEL`; `bedrock.routing`, else
  `ANTHROPIC_BEDROCK_REGION_PREFIX`; else Claude Code's derivation from
  `bedrock.region`). An application inference profile is priced as
  regional. Unrecorded agents use config's routing.
- **Discount once**: a `model_pricing:` row with `net: true` gets neither
  premium nor discount; any other row is list. `doctor` warns when a
  discount is set and a row does not say which.
- **grok**: input is `inputTokens − cachedReadTokens`; grok reader
  version 1 replaces old entries, so the daemon writes the ledger
  correction (tokens only; grok's charge is unchanged).
- **doctor**: for each ended Claude conversation (a `cost-state` line),
  overcode's list-price estimate of the same tree against Claude Code's
  `costUSD`, warning past 2% and $0.05, compared only when the cache tokens
  match within 1%. Also: a Claude model priced only by a family row.

Decided differently from §4 / left out:

- **No restatement of history.** Following §4 (costs frozen at the rate in
  force when read; back-application is an explicit later `billing
  reprice`), every fix here applies to tokens read from now on. Sonnet
  5.5's $0.20 cache read was right before 2026-10-07, so repricing at
  today's table would be wrong; entries do not keep per-request detail, so
  an exact reprice needs the transcripts and dated rows (the reprice
  command). Live entries from before the long-context breakdown take it
  unpriced on their first read. The largest historic errors left are
  Haiku 5.5 (10× high) and Fable 5.1 cache reads (4× high).
- The table is vendored per release from a file fetched by hand (owner's
  decision). Since then, for air-gapped hosts: `overcode models refresh
  --prices <offer file>` builds a local override
  (`~/.overcode/cache/claude_prices.json`, same builder and overlay as the
  release script, no network), used while dated after the vendored table,
  its rows over the vendored ones; `models info` names the table in use;
  `doctor` warns past `pricing.table_max_age_days` (90, not 30: a release
  cadence, not a price cadence).
- The doctor check compares list prices, not the Bedrock bill.

### Forks count their copied history once (2026-10-17)

Claude Code 2.1.302, checked live: `claude --bg --resume X "..."` keeps
X's id and appends to its transcript; `--resume X --fork-session`
(`/branch`, `/bg` from an interactive session, `overcode fork`) writes a
new conversation that starts with a copy of X's history, keeping X's
`message.id`s, timestamps and usage. Phase 0's dedup by message id worked
within one conversation and its subagents only, so the copy was billed
twice:

- by a 🌙 background session forked from its agent (`claude_bg.read_spend`
  read its whole transcript) — the main case;
- by an agent with a fork among its own conversations (`/branch`, or
  `/resume` then fork): each conversation was read on its own;
- by an `overcode fork` child: its reader's `since` (the child's start)
  hides the parent's messages only where it works: it compares UTC-naive
  `since` with local-naive message times, so east of UTC the hour(s)
  before the fork were counted by both (and west of UTC, see the note
  below, the child's own first hours are dropped).

Mechanism (Claude reader version 3): a forked conversation's copied
history is the run of its first records (time order) that an *earlier*
conversation holds; that prefix is taken off its totals
(`read_tree_stats(..., earlier=)`, `TranscriptRegistry.fork_prefix`,
`TranscriptIndex.prefix_usage`). Earlier means: for an agent, its
conversations that began before (first history entry,
`conversation_order`) plus its parent's (`overcode fork`;
`StatsSync.fork_sources`, an archived parent's from its record's
conversation keys); for a 🌙 session, its agent's conversations, the one
it resumed and the agent's 🌙 sessions created before it. First seen
owns: the original keeps its messages, the fork's prefix contributes 0.
Incremental: a conversation whose first message no earlier one holds is
not a fork (one lookup, remembered); a fork's prefix is found once, ended
by its first message of its own, and remembered until its index is
rebuilt; a settled answer costs a dictionary lookup per read.

History: entries read at version 2 are replaced by the next read
(`conversation_spend`), which books a ledger `correction` for the drop;
archived agents are restated once by `spend_recompute` (marker
`.spend_rv3`). Known gaps: if the original transcript is pruned (Claude's
`cleanupPeriodDays`) while the fork survives, a daemon restart reads the
fork whole again and its entry grows back; the burn-rate window does not
dedup across conversations (copies keep their old timestamps, so they fall
outside it unless forked within the window); codex/opencode/grok forks are
not covered (opencode and grok bill per session from their own charge).

Found on the way, not fixed here: `get_session_stats` builds `since` as
UTC-naive while transcript times are local-naive (`parse_message_time`),
so west of UTC an agent's first |offset| hours of messages are not
counted (verified with `TZ=America/Los_Angeles`: a message 20 minutes
after the start reads as 0 tokens; London and Tokyo read it).

## 8b. Phase B as built: the subscription quota model (2026-10-18)

Owner decisions (2026-10-18): subscribers see **percentage points of the
weekly quota, two decimals** (`1.37%`) in a new optional column, per agent
and per billing code, with a `$` cycler mode that shows only it and the
5-hour figure in the hover; the statusline wrapper is allowed but optional,
opt-in, for calibration only. This replaces §7's `3.1%w` cell and settles
open questions 4 and 5 for subscribers. Billing modes / labels for Bedrock
(the rest of §8's Phase B) are not part of this.

**No finer ground truth exists.** Claude Code 2.1.302: the statusline's
`rate_limits.seven_day.used_percentage` is an integer, `~/.claude.json`
`cachedUsageUtilization` an integer, `/api/oauth/usage` whole percents, and
the raw `anthropic-ratelimit-unified-7d-utilization` header (checked
through a local proxy) a fraction with two decimals (`"0.05"`): whole
percents again. §6.1's "fractional %" was wrong. So the two-decimal figure
is a model's, and every observation is interval-censored.

**Model** (`quota_model.py`, pure). `weekly pp = Σ_f θ_f · x_f`; features
are list dollars per token type (`in`, `out`, `cw5m`, `cw1h`, `cr`; the
long-context part at the long tier) plus `x:fable` (Fable's list dollars
again, cache reads out: a premium beyond the price ratio). θ is points per
list dollar. Prior: `100 / weekly budget` for the four non-read types,
cache reads 0 (SemiAnalysis: 500M cache reads did not move the meter),
no Fable premium; sd 50% of the base (cache reads 30%). Weekly budgets are
config-overridable priors (`PLAN_WEEKLY_USD`: Pro $170, Max 5x $850, Max
20x $1,550, Team $220 / premium $1,100); the plan comes from Claude Code's
OAuth record (`subscriptionType`, `rateLimitTier`), else `quota.plan`.
List prices are the price table's, never `model_pricing:` overrides or
Bedrock rates: the meter is API-equivalent.

**Calibration** (`quota_calibration.py`, pure). Observations are
history lines (below); a poll reading k says `U ∈ [k, k+1)`, a statusline
tick (k−1 → k with the previous sample ≤ 5 minutes earlier) says `U = k`
(sd 0.1pp for the bracket). `U(t) = θ·x(t) + a_s`, `x` the fleet's
cumulative features since the weekly reset, `a_s` a free level per segment
(absorbing pre-tracking usage and the source's rounding convention, so
only increments inform θ). EM: impute polls as truncated-normal means;
solve the weighted least squares with segment levels profiled out, a
Gaussian prior (shrinkage toward the price ratios) and θ ≥ 0 (coordinate
descent on the normal equations); re-estimate σ; to convergence. Then
robustness: an interval whose observed rise beats the predicted one by
more than 2pp + 4 × the robust (MAD) spread is usage overcode cannot see,
and the segment is split there (two rounds). Exclusions: no token totals;
a window at ≥ 100% or after its usage credits moved; a plan change or a
configured break (`quota.breaks`) starts a new window. Runs of equal
readings are thinned to their first and last (lossless under a monotone
prediction), so four weeks of 5-minute polls fit in under a second.
Posterior sd from the inverse of the normal matrix. The 5-hour window is a
single ratio (5-hour points per weekly point, ratio of summed rises within
a 5-hour window, prior 8). On synthetic data (3 weeks, 5-minute polls,
true weights 25–85% off the prior) the fit recovers the output and
cache-write weights within 15%, cache reads near 0, and the Fable premium
within 0.012.

**Engine** (`quota_engine.py`, in the monitor daemon, every tick):

- token stream `usage/tokens/<YYYY-MM>.jsonl`: each subscription agent's
  (Claude Code backend, `web` provider) token rise by model since its mark,
  from `SessionStats.conversations` (deduplicated per message id, forks'
  copies excluded: the spend readers' own work), written at most once a
  minute per agent with its billing code and account; marks persist
  (`usage/marks-<tmux>.json`), so a restart books downtime tokens once; an
  agent from before counting began is not back-billed;
- polls `/api/oauth/usage` once per account (`quota.poll_minutes` ≥ 5, in
  a thread, a lock across daemons, a fresh line another daemon wrote is
  adopted, 429 backs off doubling to an hour and honours `Retry-After`);
  history `usage/<account>/<YYYY-MM>.jsonl`: `t, src, w7, w5, r7, r5, w7f,
  credits, surf, plan, x` with `x` the fleet's tokens since the weekly
  reset (so the fit reads one file); `surf` (a surface breakdown, when the
  response has one) makes the Claude Code surface the fit's target;
- statusline samples (calibration on): writes a `statusline` line each time
  the weekly figure changes, with `prev` and `dt`;
- `SampleSource`: a finer source plugs in by yielding samples;
- refits hourly when new observations arrived (thread), saves
  `usage/<account>/model.json`; a saved fit against a different prior (plan
  or budget changed) is not used;
- publishes `SessionDaemonState.quota` = `{pp7d, pp5h, w7, w5, conf}`
  (recomputed only when tokens, the model or the readings move; equal
  values keep the same object, so a quiet fleet sends no deltas) and
  `MonitorDaemonState.usage` (the reading, plan, confidence, attributed and
  unattributed points). The TUI's own usage poll stays only as a fallback
  for an engine that publishes none (§2 item 10).

**Display.** Column `quota` (`Q%`, group llm_usage, off by default,
gated on a fleet flag so it never shows without a subscription agent):
`1.37%`, `-` with the value's width, orange like tokens and cost, not bold while the model is the prior;
hover: weekly and 5-hour points, the account's readings, confidence,
"estimate". `$` cycler: tokens → cost → joules → **quota** → all; old
preferences (no `quota` key) match the preset they matched before. Status
bar: Σ of the agents' points in quota mode; the Usage section reads the
engine. `overcode billing`: `quota wk` (this week) or `quota` (a period)
per code from the token stream at the current model, and an
`unattributed` row (observed − Σ predicted, never spread). `overcode
usage model [--json] [--refit]`, `overcode usage calibration on|off`.
`overcode doctor`: prediction above observed by > max(2pp, 20%); RMSE >
1.5pp or under half the polls in their cell; a fit older than two days
while readings arrive.

**Statusline** (`statusline_tap.py`, `overcode statusline`, fast path in
`entrypoint`): appends `{t, sid, csid, model, rl, cost, pc}` (`pc`: any
`*cache*` object, e.g. prompt-cache hits / misses / miss causes) when it
differs from the last line; then runs the user's statusline (project
`settings.local.json`, project `settings.json`, user `settings.json`;
never itself) with the same stdin and prints its output; exit 0 always.
`launch_setting` adds `statusLine` (keeping the user's `padding`) to an
agent's `--settings` only with `quota.calibration: true`.

Decided differently from §6–7 / left out:

- §6.3's interval-split attribution (each interval's ΔU split by weighted
  tokens) is not used: with whole-percent ΔU it is mostly 0 or 1. Agents
  get model predictions; the residual is the unattributed row.
- The ledger gains no `q7d` fields: the token stream *is* the quota
  ledger, priced at report time, so a refit restates the week's split
  (the estimate improves; nothing billed changes).
- Amortised cost (§6.3.3) and credits-period billed $ are not built.
- 🌙 background sessions and the summarizer/supervisor are not in the
  token stream yet: their use lands in "unattributed".
- An API-key user is recognised only by having no OAuth login; a
  subscriber whose Claude Code agent was given an API key is counted as
  subscription.

## 9. Open questions for the owner

1. **Bedrock attribution:** is creating one application inference profile
   per billing code × model realistic in your AWS organisation (who has
   `CreateInferenceProfile` and can activate cost tags)? Or IAM session tags,
   or neither (OTel attributes only)?
2. **A sanitised Bedrock transcript sample** would settle: is the
   transcript `requestId` the invocation-log `requestId`; do Bedrock
   transcripts carry the 1h/5m cache split and any routing prefix; does
   Claude Code sign `ANTHROPIC_CUSTOM_HEADERS` (request metadata)?
3. **Reconciliation inputs:** CSV exports from CUR/Cost Explorer (no
   credentials in overcode), or direct AWS API access?
4. **Statusline wrapper:** may overcode install a statusline command into
   the agents it launches (better subscription data), or poll only?
5. **Default display for subscribers:** notional $, quota %, or amortised $?
6. **Price source of truth:** generate the table from the AWS Price List
   automatically (network fetch at refresh time), or vendor it per release?
7. **Currency:** keep USD everywhere, or support a display currency?
