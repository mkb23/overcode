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

Items 1–3 are being fixed now (Phase 0 of the observability design).

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
