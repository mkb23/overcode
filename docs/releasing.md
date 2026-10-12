# Testing and releasing

## What runs where

| Tier | Where | When |
|---|---|---|
| Unit tests with a coverage floor, plus ruff | GitHub Actions (`Tests`) | Every push and PR to main, about 5 min |
| Containerized e2e: workflow and visual tiers, mock agents | GitHub Actions (`E2E`) | Every PR, about 10 min |
| Everything above, plus scale and the mock backend matrix | GitHub Actions (`Full suite`) | Pushes to `rc`, and nightly on main |
| Real agent CLIs: `live-backends`, `real-claude`, `container-real` | Your Mac | Before a release, recorded |

The repo is public, so Actions minutes are free. The local tiers stay local
because they need real CLIs, credentials and tokens, not because of runner
cost.

## The local e2e tiers

The tiers are defined in `tests/e2e-reports/tiers.toml`.

```sh
make e2e-status                      # how fresh each report is
make e2e-record TIER=live-backends   # run a tier; writes tests/e2e-reports/live-backends.json
git add tests/e2e-reports && git commit -m "e2e: record live-backends"
```

A report records:

- the commit it ran against, and whether `src/` had uncommitted changes;
- when it ran and how long it took;
- the outcome of each test;
- the versions of the agent CLIs it talked to.

A required tier counts for a release only if its report meets all of these:

- it passed, including every test named in `must_pass` (a skipped test is not a pass);
- it was recorded with no uncommitted changes in `src/`;
- it is at most `max_age_days` old (14);
- its commit is an ancestor of the commit being released;
- `src/` has changed by at most `max_src_lines_changed` lines (5,000) since that commit.

## Releasing

1. Record any stale required tiers (`make e2e-status`) and commit the reports.
2. Push the release commit to `rc` and wait for `Full suite` to go green:
   `git push origin HEAD:rc`.
3. Run `gh release create <version> ...` as before.

The publish job's `gate` step checks two things before anything is built:

- a green `Full suite` run on that exact commit;
- the e2e report check against that commit.

If either check fails, nothing reaches PyPI. Fix the cause, then re-run the
workflow from the release's Actions page.
