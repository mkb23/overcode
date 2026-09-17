# Model alias resolution: mapping unrecognised model ids onto the catalog

**Status:** Proposal — not implemented
**Date:** 2026-09-17
**Follow-on to:** #473 (bundled models.dev snapshot), #469 (context-window honesty)
**Audience:** contributors; users behind corporate gateways with their own model names

## Executive summary

overcode now resolves a model id in two tiers — the hand-curated tables,
then the bundled models.dev snapshot (`model_metadata.py`, ~1,000 text
models). Anything in neither renders a dash for `CTX%` and falls back to
the configured default rate for `$`. That is the right *honest* behaviour,
but it leaves a real population with permanently blank columns: teams whose
gateway exposes models under internal names (`acme-gpt5-sol-prod`,
`claude-opus-latest-internal`, `deployment-7b3f`) that will never appear in
any public catalog.

This proposes a third tier — an **alias file** that maps a foreign id to a
catalog id — populated two ways:

1. **By hand.** A `model_aliases:` block in `config.yaml` (or a sibling
   `model_aliases.yaml`). Deterministic, zero-risk, and enough for most
   corporate gateways where a handful of names are known.
2. **By an opt-in LLM resolver.** When an unrecognised id shows up, ask the
   already-configured summariser model to pick the best catalog match and
   report a confidence. Above a threshold, write the mapping into the alias
   file (marked as machine-proposed) so it is applied deterministically from
   then on and can be reviewed or corrected in one place.

The LLM is only ever a *proposer* that writes to a human-readable file; the
lookups themselves stay deterministic and offline. Nothing changes for
users who don't opt in.

## Background: what a lookup sees today

```
reported id ──► bare_model_id() ──► curated table ──► models.dev snapshot ──► None
               (strip provider/, [1m])
```

Two things make the miss population larger than it looks:

- **Gateway spellings.** opencode passes through whatever the provider
  block in `opencode.json` names the model. A Bedrock-fronted or
  LiteLLM-fronted deployment often has an id like `bedrock/us.anthropic.claude-opus-5-v1:0`
  or `litellm/prod-opus`. The first survives `bare_model_id()` and the
  snapshot happens to know the Bedrock spelling; the second is opaque.
- **Internal names.** Platform teams routinely re-badge models
  (`gpt-large`, `code-model-v2`) so they can swap the backing model without
  touching clients. These are *designed* to hide the real id.

Both are cases where a human (or a model) looking at the name plus a little
context could say "that's almost certainly `claude-opus-5`" — and be right
often enough to be useful, and wrong often enough that we must not guess
silently.

## Proposal

### Tier 3: the alias file

`~/.overcode/model_aliases.yaml` (path overridable; `OVERCODE_CONFIG_DIR`
honoured like `config.yaml`):

```yaml
# Maps model ids overcode doesn't recognise onto ids it does.
# Keys are matched after the same normalisation as every other lookup
# (provider/ qualifier and [1m]-style suffix stripped, case-insensitive).
aliases:
  acme-gpt5-sol-prod:
    target: gpt-5.6-sol
    source: manual
  prod-opus:
    target: claude-opus-5
    source: llm            # proposed by the resolver, see below
    confidence: 0.94
    proposed_at: 2026-09-17T09:12:44
    proposed_by: gpt-4o-mini
    evidence: "name contains 'opus'; opencode provider block is anthropic; context 1M"
```

Resolution becomes:

```
reported id ──► bare id ──► curated ──► snapshot ──► alias file ──► (resolver?) ──► None
                                                        │
                                                        └─► target id ──► curated ──► snapshot
```

An alias only ever points at an id the first two tiers know; a dangling
alias is a doctor warning, not a silent None. Aliases resolve one level
deep (no alias chains).

Manual aliases are the whole story for many users and cost nothing to
ship: a loader, a hook in `model_context_window()` / `lookup_pricing()`
/ `get_model_pricing()`, a doctor check, and docs. This is **phase 1** and
is worth doing regardless of the LLM half.

### The LLM-assisted resolver (opt-in)

**Trigger.** The monitor daemon already sees every session's model id on
each stats sync. When it meets an id that resolves to None *and* is not in
the alias file *and* has not been attempted in the last N days (a
`model_alias_attempts.json` side file), and `model_aliases.resolver.enabled`
is true, it queues a resolution.

**Prompt.** One call to the configured summariser endpoint
(`summarizer.api_url` / `model` / `api_key_var` — the same corporate-gateway
plumbing already used for AI summaries, so nothing new to configure and no
new credential). Inputs:

- the unrecognised id, exactly as reported, plus the `provider/` qualifier
  if there was one;
- the backend (`opencode` / `codex` / …) and any hints the backend exposes
  for free: opencode's provider block name and, when the session has run a
  turn, the observed context size and per-turn token/cost ratios;
- a **candidate list**, not the whole catalog: `known_model_ids()`
  pre-filtered by cheap string similarity (shared tokens, family words like
  `opus`/`sonnet`/`gpt`/`glm`/`kimi`, digit groups) to the ~20 closest
  ids, each with its family name and context window;
- an instruction to answer with strict JSON: `{"target": "<id>|null",
  "confidence": 0.0-1.0, "evidence": "<one line>"}`, and to prefer `null`
  over a guess when the name carries no family signal.

**Acceptance.** `confidence ≥ 0.85` (configurable) *and* `target` is in the
candidate list → write the alias with `source: llm` and the provenance
fields above; log it at INFO in the daemon log so it shows in the TUI's
log panel. Below threshold → record the attempt (so we don't re-ask every
sync) and leave the columns blank. Never retry more than once per id per
window; never call the resolver from the TUI thread.

**What the user sees.** The `MDL` column shows the *alias target's* short
name with a marker (e.g. `≈Op5`) so an LLM-proposed mapping is visibly
inferred rather than reported. `overcode doctor` lists every `source: llm`
alias with its confidence and evidence, and a one-liner to promote it to
`source: manual` (which drops the marker) or delete it.

### Configuration

```yaml
model_aliases:
  file: ~/.overcode/model_aliases.yaml    # default
  resolver:
    enabled: false                         # opt-in
    min_confidence: 0.85
    retry_after_days: 14
    max_candidates: 20
```

The resolver reuses the `summarizer:` block for endpoint, model and API
key. If the summariser is not configured, the resolver is inert and doctor
says so.

## Why this shape

- **Deterministic first.** Every lookup on the hot path (TUI 250 ms tick,
  daemon sync) stays a dictionary hit. The LLM runs once per unknown id in
  the daemon, off the render path, and its output is a file a human can
  read, edit and commit to dotfiles.
- **Confidence with teeth.** The candidate list is the guardrail: the model
  cannot invent an id, only pick from ids overcode can already price. A
  `null` is a legitimate and expected answer.
- **Reuses what exists.** The summariser client, the corporate-gateway
  config, the daemon's sync loop, doctor, and `known_model_ids()` (added
  with #473 for exactly this purpose) are all in place. Phase 2 is mostly
  prompt + acceptance logic + file writes.
- **Honesty preserved.** #469's principle — never show a number for a
  model you don't actually know — holds: an inferred mapping is marked as
  inferred, is reviewable, and defaults to off.

## Risks and open questions

- **False confidence on look-alike names.** `gpt-large` might be GPT-5.4 or
  GPT-5.6; both plausible, different windows. Mitigations: the observed
  context size and cost ratios are strong tie-breakers (a 1.05M-window
  model can't be `gpt-4o`); the threshold is high; the marker keeps it
  visible; doctor lists it. Still, users on opaque names should expect to
  promote or fix a few by hand.
- **Privacy.** The prompt contains the internal model name and a few token
  counts, sent to whatever endpoint the summariser already uses. That is
  the same data flow AI summaries already have (which send pane content),
  so it is not a new exposure class — but the resolver must stay opt-in and
  the docs must say what leaves the machine.
- **Cost.** One small call per unknown id per fortnight. Negligible, but
  count it in the summariser's own cost line so it isn't invisible.
- **Should the alias file live in the session dir or globally?** Global by
  default (model names are a property of the gateway, not the session);
  possibly allow a per-session override later.
- **Fuzzy matching without an LLM?** A pure-string similarity tier (token
  overlap + digit-group match) could resolve the easy cases
  (`claude-opus-5-internal` → `claude-opus-5`) with no network at all. Worth
  doing as part of phase 1 with a *higher* bar (exact family word + exact
  version digits) — it also serves as the candidate pre-filter for phase 2.

## Phasing and effort

| Phase | Scope | Effort |
|---|---|---|
| 1 | Alias file loader; hook into the three lookups; deterministic string-similarity resolver for exact-family + exact-version matches; doctor check for dangling/inferred aliases; docs | ~1 day |
| 2 | Daemon-side LLM resolver behind `model_aliases.resolver.enabled`; attempts side-file; `≈` marker in `MDL`; doctor listing with promote/delete hints | ~1–2 days |
| 3 (maybe) | `overcode models resolve <id>` CLI to run the resolver interactively and show the candidate list; `overcode models aliases` to list/promote/delete | ~½ day |

## References

- `src/overcode/model_metadata.py` — snapshot, `bare_model_id()`,
  `known_model_ids()`
- `src/overcode/history_reader.py` — `model_context_window()`,
  `model_short_name()`
- `src/overcode/pricing.py`, `src/overcode/settings.py` —
  `lookup_pricing()`, `get_model_pricing()`
- `src/overcode/summarizer_client.py` — the OpenAI-compatible client the
  resolver would reuse
- docs/backends.md, "CTX%: what the context column divides by"
- docs/configuration.md, "Model metadata (context windows and pricing)"
