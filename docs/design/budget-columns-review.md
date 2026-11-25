# Budget columns: how they work, and how intuitive they are

Review, 2026-11-25 (branch `billing-experiments`), asked for by Mike with
the EVAL column. Findings 1 and 2 are bugs, reproduced against a temp state
dir; the rest are about how readable the columns are.

## How they work today

The `$` key cycles presets (`tui_actions/view.py` `_COST_PRESETS`):
tokens (default) → cost/budget → energy → weekly quota → tokens+cost+energy.

| Column | Shows | Source | Notes |
|---|---|---|---|
| `$` | the agent's own conversation cost; `↺` since its trip reset | daemon `estimated_cost_usd` (trip: `trip`) | red at the budget, yellow from 80%; **now** coloured by charged cost (own + evals), as the gate counts |
| `EVAL` (new) | `+$x`: its evals and sims (gateway or `spend record`) | daemon `external_cost_usd` | only in the cost presets, only when some agent has eval spend; same colour as `$`; hover splits own / evals / charged / budget / left |
| `BDG` | `/ $x`: the agent's own budget | `cost_budget_usd` | dim; no hover; shrinks when it grants a child budget |
| `SUB$` | `Σ$x`: it + all descendants (+ released children's carry); **now** evals included | daemon `subtree_cost_usd` | only on parents; `↺` on trips |
| status bar `Σ` | the fleet's cost; **now** `Σ$x (+$y evals)` | engine | cost mode only |

Enforcement: the prompt gate (and Claude's hook) refuse prompts once
charged cost ≥ budget; the gateway refuses the token past `limit_usd`. A
child's budget is granted out of the parent's (`transfer_budget`), and the
unspent remainder goes back when the child finishes (auto or manual reclaim).

At the default width, a row with the `$` preset reads:

```
↺ $0.02 +$6.25/ $5.00   ↺Σ $1.00
  own    evals  budget   subtree
```

## Findings

1. **Bug: giving a child all of its budget makes the parent unlimited.**
   `cost_budget_usd == 0` means "no budget", and `transfer_budget` subtracts
   down to 0. A parent with $10 that launches a child with `--budget 10` (or
   `budget transfer … 10`) then spends without limit. Reproduced: parent
   spends $50 afterwards, `is_budget_exceeded` is False.
2. **Bug: a transfer checks the budget, not what is left of it.** A parent
   that has spent $9 of $10 can still grant $5: the tree may now spend $14
   against the $10 it was given (the parent is over its budget, the child
   has $5 to spend).
3. **Budgets are invisible by default.** The default preset is tokens, which
   hides `$`, `EVAL`, `BDG` and `SUB$`. Without pressing `$`, the only
   budget signal is the red "rejected" status, and that only after a prompt
   was refused. An agent running over budget (its current turn, its evals)
   looks normal.
4. **`BDG` and `SUB$` don't compare.** `BDG` is the agent's *own* remaining
   grant (it drops when it grants children); `SUB$` is the *subtree's*
   spend. A parent row `$2.0 / $3.0 Σ$9.0` looks badly over budget and is
   not; nothing shows the tree's allowance. Intuitive pairs are own vs own
   and tree vs tree.
5. **No "left" anywhere in the row.** You have to subtract `$` (+ `EVAL`)
   from `BDG` yourself. `BDG` has no hover. (The EVAL hover now says what is
   left; `budget show` does too.)
6. **A trip row's `$` is coloured by the lifetime total.** `↺ $0.02` in red
   means "lifetime over budget", not "$0.02 is a problem". The hover explains,
   but at a glance it reads wrong.
7. **The energy preset shows `SUB$`** (dollars) while hiding `$` and `BDG`:
   mixed units, and a dollar subtree with no dollar rows to explain it.
8. **Narrow terminals lose the budget.** Below `full`, nothing gives way
   except tags: a row too wide is cut at the edge. At 120 columns everything
   after `$` (`EVAL`, `BDG`, `SUB$`) is cut, with no sign it is there.
9. **Trips don't cover eval spend.** A trip reset zeroes `$`, not `EVAL`,
   and `SUB$`'s `↺` variant (rows' trip sums) leaves evals out. Built that way
   in this increment: trips predate eval spend.

## Recommendations (in order)

1. **Fix 1 and 2 together** (needs one decision: how to say "budget used up"
   without 0 meaning unlimited):
   - (a) cap a grant at what is left (budget − charged), and when a parent
     grants everything, set its budget to what it has spent (exactly used
     up, which the gate treats as exceeded), with a $0.01 floor for a parent
     that has spent nothing. No schema change; tiny wart.
   - (b) make "no budget" `null` and 0 a real zero: a sessions.json
     migration (old 0.0 → null) and every reader checked. Cleaner; more
     risk.
   Recommend (a) now, (b) if budgets get reworked.
2. **Show budget state without the `$` preset**: when an agent has a budget
   and is at ≥ 80% (charged), put a small badge on its row in every preset
   (e.g. `💰80%` yellow / `💰` red), the way trips mark `↺`. The fleet flag
   keeps it at zero width when no agent is near.
3. **Make `BDG` show what is left**, `$3.0 left` / `over`, coloured like `$`,
   with a hover: budget, own, evals, granted to children, left.
4. **Give parents a tree view**: `SUB$` against the tree's allowance (own
   budget + what children were granted and still hold), shown as
   `Σ$9.0/$15` and coloured the same way. Then the parent's row compares like
   with like.
5. Drop `SUB$` from the energy preset (or show the subtree's energy there).
6. Let `BDG` and `EVAL` give way before the row is cut (`gives_way`), or
   move them left of the timing columns, so a 120-column terminal keeps the
   budget.
7. Trip resets cover `EVAL` too (store `external_cost_usd` in the trip
   base), so `↺` means the same thing in every money column.
