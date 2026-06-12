# Containerized End-to-End Testing for OverCode

**Status:** Proposed design
**Date:** 2026-06-11
**Replaces:** ad-hoc host-based E2E suite (`tests/e2e/`, `tests/test_e2e_multi_agent_jokes.py`, `run_e2e_test.sh`)

## 1. Problem statement

The current E2E suite runs directly on the developer's machine and has well-documented hygiene
failures:

| Problem | Evidence |
|---|---|
| Hardcoded shared paths | `tests/test_e2e_multi_agent_jokes.py:70` writes to `/tmp/overcode_e2e_test` |
| Global `os.environ` mutation with fragile restore | `tests/e2e/conftest.py:87-139`; leaks on crash, breaks parallel runs |
| Orphaned daemon/Claude processes | `aggressive_cleanup()` literally prints "Run: `kill -9 ...`" when it gives up |
| Shared tmux socket across all tests | `TEST_TMUX_SOCKET = "overcode-test"` (`tests/e2e/conftest.py:29`) |
| Cleanup races | daemon PID file polled for only 1s; slower startup → daemon never killed |
| Zero E2E coverage in CI | `.github/workflows/tests.yml` runs unit tests only |
| No visual/layout verification | TUI and web dashboard regressions ship undetected |

The root cause is structural: tests that spawn real tmux servers, daemons, and Claude processes
share the host's filesystem, process table, env, and tmux namespace. Per-test cleanup code can
never be fully trusted — a hung test defeats any `finally` block.

## 2. Design principle: the container is the cleanup boundary

Every E2E run happens inside a **disposable Docker container**. The container's lifetime *is*
the test run's lifetime:

- Orphaned daemons, tmux servers, and Claude processes die when the container dies — by
  construction, not by cleanup code.
- `$HOME`, `~/.overcode`, `~/.claude`, `/tmp` are all container-private. Nothing can spray onto
  the host filesystem.
- The repo is mounted **read-only**; the image installs OverCode from it into a writable
  location. Results (JUnit XML, logs, screenshots) are written to a single mounted
  `artifacts/` directory — the only writable host surface.
- The same image doubles as a **VS Code dev container** so "the environment the tests run in"
  and "the environment you debug them in" are identical.

Per-test isolation *inside* the container is still fixed (per-test tmux socket, `tmp_path`
state dirs, `monkeypatch.setenv`) — see §7 — but it becomes a parallelism/debuggability
concern, not a host-safety concern.

## 3. Test tiers

A single monolithic "test everything with a real LLM" suite would be slow, flaky, and
expensive. The design is a pyramid of four tiers, all sharing the same container image and
harness:

| Tier | Name | LLM | When it runs | Target wall-clock |
|---|---|---|---|---|
| 0 | Unit | none | every push (existing workflow, unchanged) | < 2 min |
| 1 | **Workflow E2E** | `mock_claude.py` scenarios | every PR, in container | < 10 min |
| 2 | **Visual** | mock | every PR, in container | < 5 min |
| 3 | **Real-LLM smoke** | real Claude Code + API key (Haiku) | nightly + manual dispatch | < 20 min, cost-capped |

- **Tier 1** carries the breadth: every CLI command, daemon behavior, web API endpoint, and
  multi-agent workflow, driven by the existing (and extended) `tests/mock_claude.py` scenario
  engine. Deterministic, no credentials, parallelizable.
- **Tier 2** verifies *layout*: TUI snapshots and Playwright screenshots of the web dashboard.
- **Tier 3** answers the one question mocks can't: "does OverCode still work against the real
  Claude Code binary and real API?" It is a narrow smoke set (~8 scenarios) asserting
  *outcomes*, never transcript content.

## 4. Container image and dev container

### 4.1 Image contents (`docker/e2e/Dockerfile`, multi-stage)

```
Base: python:3.12-slim (linux/amd64 + arm64)

System packages:
  tmux (>= 3.0)            # core runtime dependency
  git, openssh-client      # agent workflows
  nodejs + npm             # Claude Code CLI
  fonts-dejavu-core        # tui-eye PNG rendering (a path renderer.py probes)
  curl, procps             # health checks, process assertions

NPM global:
  @anthropic-ai/claude-code   # pinned via CLAUDE_CODE_VERSION build arg

Python — installed into the SYSTEM interpreter, deliberately no venv:
  tmux panes get a login-shell PATH from /etc/profile, so the bare `python`
  the launcher bakes into pane commands (and the overcode/tui-eye shims) must
  already have every dependency. Deps from docker/e2e/requirements-e2e.txt;
  overcode itself runs uninstalled from the read-only mount via
  PYTHONPATH=/workspace/src + /usr/local/bin/overcode shim -> python -m
  overcode.cli — local changes need no rebuild or reinstall.
  pytest, pytest-timeout, pytest-xdist, pytest-asyncio
  playwright + chromium (PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers, root-installed)
  pytest-textual-snapshot, pyte, pillow, pyarrow, requests

User: non-root `overcode` user with $HOME=/home/overcode
      (tests then re-point HOME at a per-test tmp dir for full isolation)
```

Two stages: `e2e` (the test runner, default CMD runs pytest) and `dev` (adds zsh,
dev conveniences) referenced by `.devcontainer/devcontainer.json`. CI builds `e2e`; the
`dev` stage exists for debugging E2E failures interactively, not as the recommended
contributor environment — host-based development stays the documented default.

### 4.2 Entry points

- `scripts/e2e.sh [--real|--shell|--rw|--no-build] [pytest args]` — host-side runner: builds
  the image, runs the container with the repo mounted read-only at `/workspace` and
  `artifacts/e2e/` mounted writable, and tier-appropriate env. Exits with pytest's exit code.
  This is the only supported way to run E2E tests.
- Make targets: `make e2e` (tier 1+2), `make e2e-real` (tier 3, requires
  `CLAUDE_CODE_OAUTH_TOKEN`), `make e2e-shell` (drop into the container interactively).
- Multi-container compose was planned for sister scenarios but proved unnecessary: sister
  polling is plain HTTP, so two fully-isolated instances inside one container exercise the
  identical code path (`tests/container/workflows/test_sisters.py`).

### 4.3 Real LLM availability (tier 3)

- Credential injected as `CLAUDE_CODE_OAUTH_TOKEN` (Claude subscription token, generated via
  `claude setup-token`) via `docker run -e`; never baked into the image, never written to
  artifacts. The harness redacts it from captured pane content before saving logs. Tokens
  expire — the nightly job fails with an explicit "token expired, re-run `claude setup-token`
  and update the secret" message rather than a generic auth error.
- All real-LLM scenarios force `--model haiku`, small `max-turns`, and tasks with
  machine-checkable outcomes ("create a file `done.txt` containing exactly DONE").
- A **cost fuse**: the harness reads OverCode's own cost tracking after each scenario and
  aborts the run if cumulative estimated cost exceeds a configurable cap (default $2/run).
  With a subscription token there's no marginal dollar spend, but the fuse stays as a
  runaway-usage guard (rate limits are shared with Mike's interactive use) — and it dogfoods
  the budget feature.
- First-run Claude Code onboarding (theme prompt, trust dialog) is handled once per container
  by a `claude-bootstrap` fixture that pre-seeds `~/.claude.json` with accepted defaults.

## 5. Harness architecture

```
tests/
  container/                  # all containerized E2E (host runs skip via env gate)
    conftest.py               # make_oc/oc/sandbox/oc_wait fixtures + LeakRegistry audit
    harness/
      core.py                 # wait_for (the only sanctioned wait), paths, artifacts
      tmux_sandbox.py         # per-test tmux server (-L socket), pane capture, send-keys
      overcode_cli.py         # OvercodeCLI: env-scoped CLI runner, launch(scenario=...),
                              #   daemon Popen management, state/registry readers, web
                              #   server control, diagnostics dump for wait_for failures
    workflows/                # tier 1 — one module per coverage-matrix row (§6)
    visual/                   # tier 2 — TUI layout/PNG + Playwright dashboard specs
    real_llm/                 # tier 3 — @pytest.mark.real_llm smoke set + cost fuse
  e2e/                        # legacy suite: coverage replicated, deletion pending soak
```

Key harness behaviors:

- **`tmux_sandbox`**: every test gets `tmux -L oc-{worker}-{testid}` — a private tmux *server*,
  not just a session. Teardown kills the server (`tmux kill-server`), which takes every pane
  and process in it down in one call. With pytest-xdist, sockets can't collide.
- **`overcode_env`** fixture: builds a complete env dict (`OVERCODE_DIR`, `OVERCODE_STATE_DIR`,
  `OVERCODE_TMUX_SOCKET`, `CLAUDE_COMMAND` → mock or real) rooted in `tmp_path`, applied via
  `monkeypatch.setenv` and passed explicitly to subprocesses. No bare `os.environ[...] = ...`
  anywhere; a lint rule (`grep` in CI) enforces this.
- **Status polling**: a single `wait_for(predicate, timeout, interval)` helper with rich
  failure messages (dumps pane capture + daemon state JSON on timeout). No bare `time.sleep`
  assertions.
- **`pytest-timeout`** globally (`timeout = 120` per test, method = thread) so a wedged test
  fails rather than hanging the run.

## 6. Coverage matrix (tier 1 unless noted)

Each row becomes one test module under `tests/container/workflows/`.

| # | Area | Scenarios |
|---|---|---|
| 1 | **Install & doctor** | fresh container: `overcode doctor` passes; reports missing tmux/claude correctly when PATH-masked |
| 2 | **Agent lifecycle** | `launch` → appears in `list` with correct status; `show`, `send` (text + enter), `attach` (capture pane proves it), `kill`, `cleanup`; `restart` recovers a crashed mock (`crash_mid_task` scenario) |
| 3 | **Status detection** | hook-based and polling detectors each produce correct states across mock scenarios (`startup_idle`, `task_running`, `permission_bash`, `task_complete`); dispatcher prefers fresh hook data; stale-hook fallback to polling |
| 4 | **Delegation & inheritance (#433, #244)** | child via `--parent` and via `OVERCODE_SESSION_NAME` auto-detect; provider/model/wrapper/permission inheritance with and without overrides; depth-5 limit enforced; cascade kill; `follow` blocks until child completes and returns report (#432) |
| 5 | **Budget** | `budget set/show`; transfer parent→child debits parent; reclaim refunds; mock token consumption drives `budget_exceeded` status; follow-mode refund on completion |
| 6 | **Standing instructions & heartbeat** | set via CLI and web PUT; heartbeat fires after frequency elapses (frequency set to seconds in test config); pause/resume; sleep mode excludes agent from heartbeats and stats |
| 7 | **Monitor daemon** | start/stop/status; stale PID file recovery; `monitor_daemon_state.json` schema validated against all consumers; interval downshifts when idle; status history CSV rows appended |
| 8 | **Supervisor daemon** | with `CLAUDE_COMMAND` mocked, supervisor detects a `permission_bash` stall, spawns daemon-claude, intervention recorded in `supervisor_stats.json`, hidden window cleaned up |
| 9 | **Web API** | every GET/POST/PUT/DELETE endpoint (the full table in `web_api.py`/`web_control_api.py`): happy path, unknown agent → 404, `X-API-Key` enforcement on/off, `allow_control: false` rejects mutations; launch-via-API round-trips to `list` |
| 10 | **Sister instances** (two isolated instances in one container) | sister list/status; poller aggregates the remote's `/api/status`; remote agent visible in `list --sisters`; API-key auth honored. **Implementation finding:** sister polling is HTTP-only (`sister_poller.py`) — the `ssh` config field is attach-target metadata, not a transport — so the planned sshd-in-image SSH polling test has no code path to exercise; the `ssh` field is covered as config plumbing |
| 11 | **Jobs** | `bash` launches background job; `jobs list/tail/attach/kill/clear` |
| 12 | **Skills / hooks / perms / wrappers** | install/status/uninstall round-trip into container-private `~/.claude`; stale-skill detection; `wrappers install` + a trivial custom wrapper script actually wraps the launch command |
| 13 | **History & export** | status history accumulates; `usage`, `history`; `export` produces a readable Parquet file (pyarrow in image) |
| 14 | **Config** | `config init/show/path`; env-var > config > default precedence; `new_agent_defaults` respected; pricing overrides change cost estimates |
| 15 | **Tags / annotate / set-value** | CRUD round-trips visible in `list`/`show`/web API |
| 16 | **Error recovery** | kill -9 the monitor daemon mid-run → restart recovers; corrupt `sessions.json` → graceful error, not stacktrace; tmux server death detected |
| 17 | **Real-LLM smoke** (tier 3) | launch with real claude → reaches `running` then idle; one parent→child delegation with `follow` and a verifiable file-creation task; one permission-prompt approval via `send` keys; hook status detection against real hook files; supervisor approves one real stall; cost tracking reports non-zero tokens within sane bounds |

Mock scenario gaps found while building the matrix become new YAML scenarios for
`mock_claude.py` (e.g. token-burn pacing for budget tests) — the mock engine is the
load-bearing component of tier 1 and gets its own unit tests.

## 7. Visual & layout testing (tier 2)

Two distinct frontends, two techniques:

### 7.1 TUI (Textual)

- **Primary: `pytest-textual-snapshot`.** Textual apps render deterministically headless;
  snapshot tests produce SVGs diffed on content, not pixels. Cover: main dashboard with 0 / 1 /
  many agents (fixture-injected daemon state), each modal (new agent, standing instructions,
  heartbeat config), command bar, split-mode layout, narrow-terminal (80×24) and wide (200×50)
  renders. Dynamic regions (clocks, costs) are frozen via injected fake state — snapshots stay
  stable.
- **Secondary: `tui-eye`** (already in-repo: tmux capture → pyte → Pillow PNG). Used not for
  assertions but to emit human-reviewable PNGs of *real* full-stack runs (real daemons, mock
  agents) into `artifacts/screenshots/tui/`. CI uploads these on every run; a failed workflow
  test's last-known PNG is attached to its failure report. `tui-eye`'s hardcoded
  `/tmp/tui-eye-state` is parameterized as part of this work.

### 7.2 Web dashboard (Playwright + Chromium, in-container)

- Boot the real web server against fixture daemon state; for each page (`/dashboard`, `/`
  analytics) assert key DOM elements (agent rows, summary cards, charts rendered = canvas
  non-blank) and capture screenshots at desktop (1280×800) and mobile (390×844) viewports.
- Exercise the *control* path through the browser: click kill/send on an agent row and assert
  the API effect — this is the only place the embedded JS in `web_templates.py` gets executed
  at all today.
- **Pixel comparison is opt-in, not gating** initially: screenshots are uploaded as artifacts
  with an HTML index page; `--update-baselines` writes goldens under
  `tests/container/visual/baselines/` with dynamic regions (timestamps, costs) masked. Promote
  to gating once flake rate is known.

### 7.3 Leak auditing as a test assertion

A `leak_registry` fixture records each test's unique identifiers (tmux socket name, state
dir path) and, after teardown, scans the process table for any survivor referencing them —
daemons, claudes, tmux servers. Survivors are force-killed and the test FAILS naming the
culprit process. File leakage is structurally prevented rather than audited: every writable
path (HOME, OVERCODE_DIR, state dir) is rooted in pytest's per-test `tmp_path`, and the repo
mount is read-only. This turns "tests leak processes" from a chronic annoyance into an
immediate red test.

## 8. CI integration (`.github/workflows/e2e.yml`)

```
on: pull_request, workflow_dispatch, schedule (nightly 03:00 UTC)

jobs:
  build-image:    buildx with GHA cache → push to ghcr.io/<org>/overcode-e2e:sha
  e2e-workflows:  needs build-image; tier 1, pytest-xdist -n 4; JUnit + logs as artifacts
  e2e-visual:     needs build-image; tier 2; screenshots always uploaded as artifacts
  e2e-real-llm:   needs build-image; only on schedule/dispatch; environment: e2e-real
                  (holds CLAUDE_CODE_OAUTH_TOKEN secret); cost fuse $2; summary comment
                  with tokens/cost on the nightly issue
```

- PR-blocking: tiers 1 and 2. Nightly-only: tier 3, with failures opening/updating a single
  pinned GitHub issue rather than failing silently.
- Image rebuilds only when `docker/`, `uv.lock`, or pinned Claude Code version change
  (path-filtered), otherwise pulled from ghcr — keeps PR wall-clock dominated by tests, not
  builds.

## 9. Migration plan

| Phase | Deliverable | Status |
|---|---|---|
| 1 | Dockerfile, devcontainer, `scripts/e2e.sh`, harness (`tmux_sandbox`, per-test env, leak audit), and the **agent lifecycle** module green in-container | **Done** — `docker/e2e/`, `tests/container/harness/` |
| 2 | Replicate `tests/e2e/` coverage in `tests/container/workflows/`; then delete the legacy suite, `run_e2e_test.sh` and the jokes script | Coverage replicated for lifecycle/status/delegation/standing-instructions/supervisor; legacy deletion pending a soak period |
| 3 | Fill matrix gaps (web API, jobs, config, budget, sisters, error recovery) | **Done** — 14 workflow modules |
| 4 | Tier 2 visual layer (TUI layout + PNG artifacts, Playwright dashboard) | **Done** — `tests/container/visual/` |
| 5 | Tier 3 real-LLM smoke + nightly workflow + cost fuse | **Done** — `tests/container/real_llm/`, `.github/workflows/e2e.yml`; needs `CLAUDE_CODE_OAUTH_TOKEN` repo secret (from `claude setup-token`) |
| 6 | Sister SSH-transport tests (dropped — no such code path; see §6 row 10); devcontainer-wrapper Docker-in-Docker test (future, nightly-only privileged exception) | Future work |

## 10. Product bugs found while building the suite

Fixed in the same change-set; each now has E2E coverage that would catch a regression:

1. **Token tracking broken for paths containing `_`** — `encode_project_path`
   (history_reader.py) only dashed `/` and `.`, but Claude Code dashes every
   non-alphanumeric character. Any project path with an underscore silently reported
   0 tokens / $0 cost. Caught by the real-LLM token-tracking smoke test.
2. **`monitor-daemon status` crashed whenever the daemon was running** —
   `format_ago()` was handed the ISO *string* from daemon state instead of a datetime
   (cli/daemon.py).
3. **`jobs tail` / follow-mode captured from the wrong tmux server** —
   `follow_mode._capture_pane` (and a dozen other call sites in tui.py,
   hook_status_detector.py, supervisor_daemon.py, launcher.py, implementations.py,
   tmux_manager.py, cli/split.py) invoked bare `tmux`, ignoring `OVERCODE_TMUX_SOCKET`.
   All now route through `_build_tmux_cmd()`.
4. **Supervisor daemon ignored `CLAUDE_COMMAND`** — hardcoded `claude`, unlike the
   launcher; now consistent (and mockable).
5. **`overcode bash --follow` could not be disabled** — typer bool flag lacked the
   `--no-follow` form.
6. **`tests/mock_claude.py` rejected modern launcher flags** (`--session-id`,
   `--settings` from #373/#435) — now tolerant of unknown Claude flags.

## 11. Decisions

**Taken in this design (revisit if wrong):**
- Mock-first breadth, real-LLM-narrow depth — real-LLM-everything is unaffordable and flaky.
- pytest stays the runner; no new test framework.
- The legacy `tests/e2e/` suite is *migrated then deleted*, not maintained in parallel.
- Web dashboard JS finally gets executed via Playwright rather than adding a JS unit layer.

**Decided with Mike (2026-06-11):**
1. **Credentials:** nightly tier authenticates with a `CLAUDE_CODE_OAUTH_TOKEN` from Mike's
   Claude subscription (no metered API key). Accepted trade-offs: tokens expire (nightly job
   surfaces this explicitly) and CI usage shares the subscription's rate limits.
2. **Cost fuse:** $2/run default stands — under the subscription it's a usage guard rather
   than a dollar cap.
3. **Devcontainer scope:** test infrastructure only. The `dev` stage is for debugging E2E
   failures; the README will not push it as the contributor environment.
4. **Sister testing:** cover both the web-port path and the full SSH path (sshd + pre-seeded
   keys in the sister container) in phase 6. *Revised during implementation:* the sister
   poller has no SSH transport — polling is HTTP-only and `ssh` is attach metadata
   (sister_poller.py:28) — so the SSH half reduces to config-plumbing coverage; sshd in the
   image would test nothing real. If SSH-transport polling is ever added, revisit.
