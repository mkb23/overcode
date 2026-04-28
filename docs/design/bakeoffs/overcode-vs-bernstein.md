# Overcode vs Bernstein: Feature Bakeoff

## Overview

| | **Bernstein** | **Overcode** |
|---|---|---|
| **Repo** | [chernistry/bernstein](https://github.com/chernistry/bernstein) | This project |
| **Language** | Python 3.12+ (FastAPI + Textual) | Python 3.12+ (Textual TUI) |
| **Stars** | See README badge (not fetched) | N/A (private) |
| **License** | Apache 2.0 (`LICENSE`) | Proprietary |
| **First Commit** | ~2026-03-28 (sampled window) | 2025 |
| **Last Commit** | 2026-04-15 (active) | Active |
| **Purpose** | Deterministic orchestrator that decomposes a goal, spawns parallel agents in git worktrees, verifies output, and merges results | Claude Code supervisor/monitor with instruction delivery |

Scale indicators from `~/Code/bernstein`:
- ~1,984 commits, ~341K LOC of Python in `src/`, 989 source files, 1,043 test files, 135 docs
- Version 1.7.5 in `pyproject.toml:7`

## Core Philosophy

Bernstein's thesis is that **scheduling should be deterministic code, not an LLM**. The orchestrator is a pure-Python state machine that takes a goal, asks a "manager" LLM to decompose it once into tasks with owned files and completion signals, then autonomously spawns short-lived CLI agents in isolated git worktrees, verifies their output with concrete gates (tests pass, lint clean, types correct, PII absent, fingerprint not copy-pasted from training data), and merges on success. The README's catchphrase — "Zero LLM tokens on scheduling" (`README.md:39`) — captures the design principle. The supporting manifesto lives in `docs/the-bernstein-way.md:43-49` and `docs/WHY_DETERMINISTIC.md`.

Agents are treated as **disposable, interchangeable workers** (`docs/the-bernstein-way.md:1-120`). Any of ~20 CLI adapters (Claude Code, Codex, Gemini, Cursor, Aider, Ollama, Goose, etc., registered in `src/bernstein/adapters/registry.py:33-53`) can be swapped in, and roles can be routed to different models via `role_model_policy` in `bernstein.yaml`. The workflow loop is: `bernstein init` → `bernstein -g "goal"` (or `bernstein run plan.yaml`) → decompose → spawn-in-worktrees → verify → merge → `bernstein recap`. A `--evolve` mode lets Bernstein iterate on its own codebase autonomously under a budget and cycle cap (`src/bernstein/cli/main.py:440-464`, `src/bernstein/evolution/loop.py`).

Where Overcode is a **control tower over long-lived, human-driven Claude Code sessions in tmux**, Bernstein is a **batch job runner for short-lived agents** that prizes reproducibility and CI-friendliness (`bernstein --headless` emits structured JSON and non-zero exit codes on failure — `README.md:73`, `src/bernstein/cli/main.py:485`). Human interaction in Bernstein happens at goal entry, approval gates, and post-run review — not live keystrokes into agent shells.

## Feature Inventory

### 1. Agent Support

**Bernstein:**
- Hardcoded registry of ~18 built-in adapters (`src/bernstein/adapters/registry.py:33-53`): Claude Code, Codex, Gemini, Qwen, Aider, Ollama, Cursor, Continue.dev, Cody, Goose, IaC (Terraform/Pulumi), Kilo, Kiro, OpenCode, Roo Code, Tabby, Amp, CloudflareAgents, CloudflareCodex.
- `GenericAdapter` fallback for any CLI accepting `--prompt` (`src/bernstein/adapters/registry.py:99-100`).
- Third-party adapters discoverable via pluggy entry-point group `bernstein.adapters` (`src/bernstein/adapters/registry.py:58-80`, `pyproject.toml:106-109`). Adapters subclass `CLIAdapter` (`src/bernstein/adapters/base.py`) and implement `spawn/kill/is_alive/name`.
- Auto-discovery cached on startup via `discover_agents_cached()` in `src/bernstein/cli/main.py:411-416`.
- Agent-agnostic: the scheduler doesn't care which adapter runs a task; mix cheap local + heavy cloud models in one run (`README.md:40`, `bernstein.yaml` `role_model_policy`).
- Any adapter can also serve as the **internal scheduler LLM** (`internal_llm_provider` / `internal_llm_model` in `bernstein.yaml`).

**Overcode:** Claude Code only. No plugin system for other agents.

### 2. Agent Launching

**Bernstein CLI (Click-based, entry point `bernstein.cli.main:cli`, `pyproject.toml:99`). Commands observed:**
- Core: `bernstein -g "GOAL"`, `bernstein run <plan.yaml>`, `bernstein run --dry-run <plan.yaml>`, `bernstein init`, `bernstein init-wizard`, `bernstein quickstart`, `bernstein demo`, `bernstein stop`, `bernstein stop --force`.
- Monitoring: `bernstein live`, `bernstein dashboard`, `bernstein status`, `bernstein ps`, `bernstein cost`, `bernstein doctor`, `bernstein recap`, `bernstein trace <ID>`, `bernstein run-changelog --hours 48`, `bernstein dry-run`, `bernstein dep-impact`, `bernstein explain <cmd>`, `bernstein aliases`, `bernstein config-path`, `bernstein debug-bundle`.
- Agents: `bernstein agents list`, `bernstein agents sync`, `bernstein agents discover`.
- Evolution: `bernstein evolve` (with `--budget`, `--max-cycles`, `-y`).
- Git/PR: `bernstein fingerprint build --corpus-dir ~/oss-corpus`, `bernstein fingerprint check src/foo.py`.
- Cloud: `bernstein cloud init`, `bernstein cloud deploy`, `bernstein cloud run plan.yaml`.
- Persistence: `bernstein checkpoint`, `bernstein wrap-up`.
- Task control: `approve`, `reject` (`src/bernstein/cli/task_cmd.py`).
- Server: `bernstein mcp-server --port 3000` (MCP gateway).
- Flags on main run: `--headless`, `--auto-approve`, `-y`, `--fresh`, `--evolve`/`-e`, `--budget`, `--max-cycles`, `--classic` (Rich Live instead of Textual). Source: `src/bernstein/cli/main.py:478-497`.
- Command aliases resolved by `_RichGroup` (`src/bernstein/cli/main.py:372-405`, `src/bernstein/cli/aliases.py`) — e.g. `s` → `status`.

**How agents are spawned:**
- Default: short-lived local subprocess per task (`src/bernstein/core/agents/spawner_core.py:1-76`, `AgentSpawner`).
- Optional: Docker container via `ContainerManager` (imported in `spawner_core.py:21`).
- Optional: Cloudflare Workers via `CloudflareBridge` (`src/bernstein/bridges/cloudflare.py:46-86`).
- Each spawn creates a git worktree at `.sdd/worktrees/{session_id}` on branch `agent/{session_id}` (`src/bernstein/core/git/worktree.py:38,53`).

**Prompt delivery:**
- Role-specific system prompt via `CLIAdapter.spawn(request)` (`src/bernstein/adapters/base.py`).
- Task context file (owned files, completion signals, project context) assembled in `src/bernstein/core/context.py`.
- MCP tools auto-injected via `src/bernstein/adapters/skills_injector.py`.

**Templates / plans:** YAML plan files with `goal`, `cli`, `max_agents`, `role_model_policy`, `budget`, `internal_llm_provider`, `evolution_enabled`, `quality_gates`, `constraints`, `context_files` (`bernstein.yaml:1-45`). Manager LLM decomposes into tasks with `role`, `owned_files`, `completion_signals`, `dependencies` (`src/bernstein/core/tasks/models.py`, `src/bernstein/core/orchestration/manager_parsing.py`).

**Overcode:** TUI action or CLI launch, prompt delivered via `tmux send-keys`. Preset launch templates exist but no YAML plan format.

### 3. Session / Agent Lifecycle

**Bernstein agent states** (`src/bernstein/tui/agent_states.py:24-38`):
`SPAWNING` (⊚̴ yellow), `RUNNING` (● green), `STALLED` (◐ orange), `MERGING` (⇄ blue), `DEAD` (○ red), `IDLE` (▢ gray), `UNKNOWN` (⊙ dim). Each has a color and animated spinner frame (`agent_states.py:52-79`).

**Task states:** `open`, `claimed`, `done`, `failed`, `retry`, `blocked` (waiting on dependency) — enumerated in `.sdd/backlog/{open,claimed,closed}/` directory structure.

**Persistence** — entirely file-based under `.sdd/` (`docs/the-bernstein-way.md:82`):
- `.sdd/worktrees/{session_id}/` — per-agent checkout
- `.sdd/backlog/{open,claimed,closed}/` — task YAML files
- `.sdd/runtime/` — metrics, agent metadata, costs
- `.sdd/metrics/kill_audit.jsonl` — kill events
- `.sdd/quarantine/{session_id}.json` — quarantined branches
- `.sdd/index/knowledge_graph.db` — SQLite codebase graph (`src/bernstein/core/knowledge/knowledge_graph.py:18`)
- `.sdd/evolution/experiments.jsonl` — evolve-mode log
- `.sdd/routing/{policy.json,bandit_state.json}` — ML router state
- `.sdd/audit/YYYY-MM-DD.jsonl` — HMAC-chained audit log

**Crash recovery:** Write-ahead log in `src/bernstein/core/persistence/wal.py`, replay on start in `wal_replay.py`. Session continuity in `session_continuity.py`, explicit `bernstein checkpoint` command in `checkpoint.py`.

**Resume:** Restart loads `.sdd/runtime/agents.json` + task store; `--fresh` forces clean start (`src/bernstein/cli/main.py:488`).

**Cleanup:** Graceful `stop` drains in-flight agents and merges successful branches; `stop --force` kills immediately and marks tasks failed (`src/bernstein/cli/stop_cmd.py`). Worktree cleanup + orphan pruning in `src/bernstein/core/agents/spawner_core.py:45-49`.

**Overcode:** Session states include running / waiting_user / waiting_approval / error / idle / stopped. Sessions persist in JSON via `session_manager.py`, survive TUI restarts, can be resumed with `--resume`. No WAL; cleanup ends the tmux window.

### 4. Isolation Model

**Bernstein:**
- Git worktrees by default, one per agent, branch `agent/{session_id}` (`src/bernstein/core/git/worktree.py:53`).
- `WorktreeSetupConfig` supports shallow checkouts, symlinks for large directories like `node_modules`, `venv`, `dist` (`worktree.py:42-73`) with platform-specific warnings for Windows symlink permissions (`worktree.py:57-70`).
- Sparse checkout via `sparse_paths` (`worktree.py:77`) for monorepos.
- `worktree: false` in `bernstein.yaml` disables isolation; agents share main checkout using file-ownership declarations to avoid races (`docs/the-bernstein-way.md:57`).
- Merge workflow: janitor verifies completion signals, then merges `agent/{session_id}` → main; failed merges → quarantine at `.sdd/quarantine/{session_id}.json`. Merge orchestration in `src/bernstein/core/agents/spawner_merge.py`.
- Sub-task trees represented as task dependencies in the backlog (not worktree-of-worktree); manager LLM emits a DAG (`src/bernstein/core/orchestration/manager.py:1-100`).
- Conflicts are **not** auto-resolved — task is marked failed for human review.

**Overcode:** Shared repo, no worktrees, no automated merge. Agents coordinate via parent/child hierarchy and standing instructions, not filesystem isolation.

### 5. Status Detection

**Bernstein:**
- Heartbeat + PID monitoring (`src/bernstein/core/agents/heartbeat.py`, `HeartbeatMonitor`) — stalled agents flagged for circuit breaker.
- Log parsing against `completion_signals` declared per task (`src/bernstein/core/tasks/task_completion.py`): tests passed, files exist, lint clean, types clean.
- Web dashboard polls `/status`, `/agents`, `/tasks` endpoints (HTTP in-process).
- `bernstein live` refresh interval configurable, default 2.0s (`src/bernstein/cli/commands/advanced_cmd.py:49-52`).
- No LLM is used for status detection. Cost of detection: essentially free.

**Overcode:** 442 regex patterns + Claude Code hook events (instant, authoritative) with per-session toggle. Richer taxonomy for interactive Claude Code sessions.

### 6. Autonomy & Auto-Approval

**Bernstein:**
- `--headless` for CI (JSON output, non-zero exit on failure, `src/bernstein/cli/main.py:485`).
- `--auto-approve` skips task approval prompt (`main.py:497`).
- `-y` / `--yes` skips cost confirmation in evolve mode (`main.py:487`).
- **Evolve mode safety:** `--evolve` requires a hard `--budget` and `--max-cycles`; `_validate_evolve_mode()` refuses to start without them (`main.py:440-464`).
- **Quality gates** (`src/bernstein/core/quality/`, 60+ modules): `lint_clean`, `test_passes`, `path_exists`, `llm_judge`, `type_check`, coverage, PII scan, fingerprint, arch conformance — configured per-role in `bernstein.yaml:33-38`.
- **Circuit breaker** (`src/bernstein/core/observability/circuit_breaker.py:1-120`): trips on scope violations (edits outside `owned_files`), budget violations, or behavioral anomalies (infinite loops). Writes `.sdd/kill/{session_id}.json`, appends to `kill_audit.jsonl`, quarantines branch. Orchestrator picks up signals in `agent_lifecycle.check_kill_signals()`.

**Overcode:** Claude-powered supervisor daemon with standing instructions (25 presets), risk-assessed auto-approval, fork-with-context, oversight with stuck detection.

### 7. Supervision & Instruction Delivery

**Bernstein:**
- **Manager LLM** (`src/bernstein/core/orchestration/manager.py:1-100`, prompts in `manager_prompts.py`) runs once at decomposition — default Claude Sonnet.
- **Janitor** module runs inside the orchestrator event loop, responsible for verification + merge decisions.
- **Supervisor daemon** at `src/bernstein/core/server/server_supervisor.py` watches the task queue when the server is running.
- **Mid-flight control** is coarse: change task priority via `POST /tasks/{id}`, `bernstein approve` / `bernstein reject` to walk approval gates (`src/bernstein/cli/task_cmd.py`, `src/bernstein/core/orchestration/trigger_manager.py`), or write a kill signal to `.sdd/kill/{id}.json`. No keystroke-level injection into running agents.
- **Cross-model review** (`src/bernstein/core/quality/cross_model_verifier.py`) — an independent model grades another agent's diff.
- **Token growth monitoring** (`src/bernstein/core/tokens/token_monitor.py`) + Z-score anomaly flagging (`src/bernstein/core/cost/cost_anomaly.py`); auto-intervention pauses spawning when thresholds trip.
- **Audit log**: HMAC-chained JSONL in `.sdd/audit/YYYY-MM-DD.jsonl` (`src/bernstein/core/security/audit.py`); integrity check in `audit_integrity.py`.

**Overcode:** Heartbeat system delivers periodic instructions to idle agents; 25 standing-instruction presets; intervention history logged per agent; fork-with-context inherits conversation.

### 8. Cost & Budget Management

**Bernstein:**
- `TokenUsage` dataclass (`src/bernstein/core/cost/cost_tracker.py:52-80`) tracks `input_tokens`, `output_tokens`, `cache_hit`, `cache_read_tokens`, `cache_write_tokens`.
- Per-model pricing table `_MODEL_COST_USD_PER_1K` (`cost_tracker.py:27`) covers Claude opus/sonnet/haiku, Codex gpt-5.4/mini, Gemini, etc.
- Cache economics: read @ ~10%, write @ ~25% of base price; optimizer in `src/bernstein/core/tokens/claude_prompt_cache_optimizer.py`.
- Cost records persisted to `.sdd/runtime/costs/{run_id}.json`.
- **Budget enforcement** is hard: thresholds at 80% (warn), 95% (critical), 100% (stop spawning) — `DEFAULT_HARD_STOP_THRESHOLD` (`cost_tracker.py:42-44`).
- `bernstein cost [--since 7d] [--by-model] [--by-task]` reporting (`src/bernstein/cli/commands/cost.py:1-100`).
- **Z-score anomaly detection** flags tasks >2σ from baseline (`src/bernstein/core/cost/cost_anomaly.py`) and emits predictive alerts (`notifications.py:53-66`: `predictive.budget_exhaustion`, `predictive.completion_decline`, `predictive.run_overrun`).

**Overcode:** Per-agent $ budgets with **soft** enforcement (warn + skip, not kill). Cost display in TUI columns.

### 9. Agent Hierarchy & Coordination

**Bernstein:**
- **No peer agent-to-agent chat** — explicitly unsupported (`README.md:148`).
- **Manager/worker** pattern only: one-shot LLM decomposes the goal, deterministic scheduler assigns tasks to workers.
- Roles (backend, frontend, QA, docs, security) defined in role policy; workers never see each other's raw output — only merged results in main.
- **Task dependencies** create an implicit DAG (`src/bernstein/core/tasks/models.py` — `dependencies` field).
- **Cascade routing**: on failure, `src/bernstein/core/routing/cascade_router.py` escalates to a stronger model (haiku → sonnet → opus).
- **Bandit router** (`src/bernstein/core/cost/bandit_router.py:1-120`) learns optimal (model, effort) per task type via LinUCB.
- **Cascade operations**: kill signals propagate, budget limits cap all children under a run.

**Overcode:** Parent/child trees 5 levels deep, cascade kill, fork with full context, agent-to-agent messaging via instruction delivery.

### 10. TUI / UI

**Bernstein:**
- **Framework:** Textual ≥1.0 (`pyproject.toml:52`); main app `BernsteinApp(App[None])` in `src/bernstein/tui/app.py:15-56`.
- **Layout:** three-column `bernstein live` dashboard — agent list (left), task list (center), activity feed + sparkline + chat input (right). Widgets in `src/bernstein/tui/widgets.py` (`AgentLogWidget`, `TaskListWidget`, `CoordinatorDashboard`, `TimelineEntry`).
- **Keybindings** declared in `BINDINGS` (`app.py:66-82`): `/` search, `q` quit, `?` help, `Space` approve, arrow keys navigation. Additional bindings configurable in `~/.bernstein/keybindings.yaml` (resolver in `keybinding_config.py`).
- **Classic mode:** `--classic` swaps Textual for Rich Live (simpler, no mouse — `src/bernstein/cli/commands/advanced_cmd.py:56-58`).
- **Customization:** keybindings, refresh interval, theme via config.

**Overcode:** ~50+ keybindings, timeline view, configurable columns, Textual framework, side-question subagents.

### 11. Terminal Multiplexer Integration

**Bernstein:** **Not used.** Agents are bare subprocesses with piped stdio; no tmux/zellij/screen dependency. The orchestrator owns lifecycle directly.

**Overcode:** tmux-backed, one window per agent, layout management, live pane visibility.

### 12. Configuration

**Bernstein.yaml options** (observed in `bernstein.yaml:1-45` and `docs/CONFIG.md`):
- `goal`, `cli` (adapter), `max_agents`, `role_model_policy`, `budget` (USD; 0 = unlimited), `internal_llm_provider`, `internal_llm_model`, `evolution_enabled`, `auto_decompose`, `constraints` (e.g. "Pyright strict", "Ruff"), `quality_gates` (per-role gate list), `context_files`, `agency.path`, `worktree: true/false`.

**Environment variables** (grep of `BERNSTEIN_*` + related):
- `BERNSTEIN_SERVER_URL` (default `http://localhost:8052`, `src/bernstein/tui/app.py:91`)
- `BERNSTEIN_WORKDIR`, `BERNSTEIN_ADAPTER`, `BERNSTEIN_HEADLESS`, `BERNSTEIN_DEBUG`
- `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET` (`src/bernstein/github_app/app.py:62-80`)

**Hook / event system** (`docs/hook-system.md`, `src/bernstein/plugins/hookspecs.py:92-150`):
- pluggy-based. Entry-point group `bernstein.plugins`.
- Hooks: `before_spawn`, `after_spawn`, `before_merge`, `after_merge`, `on_failure`, `on_completion`, `on_budget_warning`.
- Orchestration by `PluginManager` (`src/bernstein/plugins/manager.py`).

**Overcode:** TOML config, per-project + global, supervisor/heartbeat-focused options, shell hooks into Claude Code.

### 13. Web Dashboard / Remote Access

**Bernstein:**
- **Stack:** FastAPI ≥0.115 + uvicorn ≥0.30 (`pyproject.toml:46-47`), built via `src/bernstein/core/server/server_app.py:20-100`.
- **Port:** 8052 default (`src/bernstein/cli/commands/advanced_cmd.py:181`).
- **Routes:** 51 modules under `src/bernstein/core/routes/` — `/status`, `/tasks`, `/agents`, `/cost`, `/logs`, `/health`, MCP, A2A.
- **Real-time:** Server-Sent Events bus in the server app factory.
- **Cluster mode:** multi-node via shared task store (Postgres backend in `src/bernstein/core/persistence/store_postgres.py`; node registry in `src/bernstein/core/cluster.py`; Redis-optional leader election).
- **Multi-repo workspaces:** task.`repo_path` targets individual repositories.

**Overcode:** HTTP API + web dashboard with analytics; Sister integration for cross-machine monitoring.

### 14. Git / VCS Integration

**Bernstein:**
- **Worktree lifecycle:** `git worktree add .sdd/worktrees/{id} -b agent/{id}` on spawn; `git worktree remove` + branch delete on cleanup (`src/bernstein/core/git/git_ops.py`).
- **Commits:** made inside the worktree by agents; janitor verifies before merge (`src/bernstein/core/git/git_ops.py`).
- **PRs:** optional — successful tasks can open PRs instead of direct merges (`src/bernstein/core/git/git_pr.py`).
- **GitHub App:** webhooks, JWT + installation token auth (`src/bernstein/github_app/app.py:1-100`, `webhooks.py`), slash commands `/bernstein fix-lint`, `/bernstein run-tests` (`slash_commands.py`), check runs (`check_runs.py`).
- **Conflict handling:** conflicts fail the task — no auto-resolution.

**Overcode:** No automated merges or PRs (relies on user); Claude Code sessions do commits.

### 15. Notifications & Attention

**Bernstein:** Multi-channel fan-out in `src/bernstein/core/communication/notifications.py:1-100` and `desktop_notify.py`:
- Desktop (macOS/Linux via `notify-send` or native APIs).
- Slack (Block Kit), Discord (embeds), Telegram (bot), PagerDuty (severity-mapped, `notifications.py:89-96`), SMTP email (`notifications.py:32-37`), generic webhooks.
- Events: `run.started`, `task.completed`, `task.failed`, `run.completed`, `budget.warning` (80%), `budget.exhausted` (100%), `approval.needed`, `incident.critical`, predictive alerts (`notifications.py:53-66`).

**Overcode:** No native notifications (documented gap).

### 16. Data & Analytics

**Bernstein:**
- **Session history:** everything under `.sdd/` is inspectable; WAL is JSONL (`src/bernstein/core/persistence/wal.py`).
- **Export formats:** JSON (task records, costs, logs), Parquet (columnar, optional), CSV (cost summaries).
- **Metrics:** Prometheus `/metrics` endpoint (`prometheus-client` in `pyproject.toml:58`, metric defs in `src/bernstein/core/prometheus.py`) — `agents_spawned_total`, `tasks_completed_total`, `task_duration_seconds`, `cost_usd`, `tokens_consumed`.
- **OpenTelemetry** exporter presets; Grafana dashboards shipped as pre-built bundles.
- **`bernstein recap`** post-run summary and **`bernstein run-changelog --hours 48`** for diff-based change logs.

**Overcode:** Parquet export, web analytics, timeline view.

### 17. Extensibility

**Bernstein:**
- **pluggy plugin system** (`src/bernstein/plugins/manager.py`): write `@hookimpl` functions, register via `bernstein.plugins` entry point.
- **MCP** bidirectional: serve via `bernstein mcp-server --port 3000` (`src/bernstein/core/protocols/mcp_gateway.py`), consume via skills injection into agents (`src/bernstein/adapters/skills_injector.py`). MCP 1.0 and 1.1 compatible (README badges).
- **A2A protocol** (experimental) with `/a2a/tasks/send`, `/a2a/messages`, `/a2a/agent.json` discovery (`src/bernstein/core/protocols/a2a.py:10-42`) — use case is federation with external orchestrators.
- **Custom adapters:** subclass `CLIAdapter` (`src/bernstein/adapters/base.py:1-150`), `register_adapter()` or entry-point (`src/bernstein/adapters/registry.py:114-122`).

**Overcode:** Limited extensibility; no public plugin system.

### 18. Developer Experience

**Bernstein:**
- **Install:** pip / pipx / uv / `brew install chernistry/tap/bernstein` / Fedora COPR / `npx bernstein-orchestrator` (`README.md:183-192`). VS Code + Open VSX extensions.
- **First run:** `bernstein init` scaffolds `.sdd/` and `bernstein.yaml`; `bernstein init-wizard` is interactive (`src/bernstein/cli/commands/init_wizard_cmd.py`); `bernstein quickstart` / `bernstein demo` (60-second Flask TODO).
- **Docs:** 135 markdown files under `docs/`, including `GETTING_STARTED.md`, `ARCHITECTURE.md`, `FEATURE_MATRIX.md`, `GLOSSARY.md`, `KNOWN_LIMITATIONS.md`, `WHY_DETERMINISTIC.md`, `the-bernstein-way.md`, migration guides from CrewAI / LangGraph, Cloudflare setup. ReadTheDocs (`mkdocs.yml`).
- **Tests:** 1,043 test files spanning `tests/unit`, `tests/integration`, `tests/golden` (deterministic), `tests/chaos` (failure injection), `tests/pentest` (security), `tests/benchmarks`.
- **CI:** GitHub Actions (badge in README, `.github/workflows/ci.yml`); codecov (`codecov.yml`); SonarCloud (`sonar-project.properties`); mutation testing (`mutmut_config.py`); dead-code detection (`vulture_whitelist.py`); `typos.toml`.
- **Key deps** (`pyproject.toml`): click≥8.1, rich≥13, textual≥1.0, fastapi≥0.115, uvicorn≥0.30, httpx≥0.27, websockets≥14, pydantic-settings≥2.13.1, pluggy≥1.5, mcp≥1.0, cryptography≥45, prometheus-client≥0.21, opentelemetry-*≥1.30, watchdog≥4.

**Overcode:** Python project, ~1700 tests, fixtures-based testing, no public install path.

## Unique / Notable Features

1. **"Zero LLM tokens on scheduling"** — the orchestrator is pure deterministic Python; the LLM is invoked once per run for decomposition and optionally for cross-model review. Auditable and reproducible (`docs/WHY_DETERMINISTIC.md`, `docs/the-bernstein-way.md:43-49`).
2. **Self-evolution (`--evolve`)** — Bernstein reads its own codebase, proposes improvements, executes them, and can merge to main, all under a hard budget and cycle cap with experiment log at `.sdd/evolution/experiments.jsonl` (`src/bernstein/evolution/loop.py`, safety checks at `src/bernstein/cli/main.py:440-464`).
3. **Contextual bandit router (LinUCB)** — `src/bernstein/core/cost/bandit_router.py:1-120`. Feature vector includes complexity, scope, priority, repo size, token estimate, task type, language, role; reward is `quality_score * (1 - normalized_cost)`. 50-task warmup, state persisted to `.sdd/routing/bandit_state.json`.
4. **Output fingerprinting** — MinHash/LSH similarity check against an OSS corpus to detect copy-paste from training data (license risk). `bernstein fingerprint build --corpus-dir ~/oss-corpus` and `bernstein fingerprint check` (`src/bernstein/core/quality/output_fingerprint.py:1-100`).
5. **HMAC-chained tamper-evident audit log** — each entry's HMAC includes the previous entry's HMAC; `bernstein audit check` verifies the chain (`src/bernstein/core/security/audit.py`, `audit_integrity.py`).
6. **Knowledge graph** of codebase (SQLite) — files, functions, classes, edges (imports, calls, inherits), used for owned-file suggestions, task routing, and LLM context (`src/bernstein/core/knowledge/knowledge_graph.py:1-100`).
7. **Cloudflare edge execution** — agents run in V8 isolates on Workers with R2 workspace sync, Workers AI as LLM provider, D1 for metrics, Vectorize for semantic cache, browser rendering (`src/bernstein/bridges/cloudflare.py:46-100`).
8. **Semantic cache** — Vectorize-backed chunk cache reduces re-reading of the same files (`src/bernstein/core/knowledge/semantic_cache.py`).
9. **Quality gate matrix** — lint + type-check + tests + PII scan + arch conformance + fingerprint + `llm_judge` (`src/bernstein/core/quality/`, 60+ gate modules) composable per role.
10. **Predictive alerts** — `predictive.budget_exhaustion`, `predictive.completion_decline`, `predictive.run_overrun` fired before they actually happen (`src/bernstein/core/communication/notifications.py:53-66`).
11. **MCP + A2A federation** — bidirectional MCP and experimental A2A protocol for agent discovery across orchestrators (`src/bernstein/core/protocols/mcp_gateway.py`, `src/bernstein/core/protocols/a2a.py`).
12. **Cross-model verifier** — an independent model judges another agent's diff as a gate (`src/bernstein/core/quality/cross_model_verifier.py`).

## What This Tool Does Better Than Overcode

- **End-to-end merge workflow.** Bernstein owns the loop from goal → decomposition → spawn → verify → merge, with PR creation and conflict quarantining. Overcode stops at "agent produced changes; user reviews." Specific: worktree creation (`src/bernstein/core/git/worktree.py`), janitor verification (`src/bernstein/core/quality/gate_runner.py`), merge with quarantine fallback (`spawner_merge.py`).
- **Concrete verification gates.** Overcode trusts Claude's self-reporting; Bernstein checks tests-pass, lint-clean, type-check, path-exists as hard signals before merge. The `completion_signals` field forces authors of goals to state what success looks like up front.
- **Provider agnosticism.** 18+ adapters with pluggy third-party extension. Overcode is Claude-Code-only.
- **CI/headless mode.** `bernstein --headless` emits JSON, non-zero exit on failure, integrates with GitHub App check runs. Overcode is interactive-first.
- **Cost governance.** Hard budget stops at 80/95/100%, Z-score anomaly detection, per-model tracking with cache-aware pricing. Overcode's budget is soft-skip.
- **Audit and compliance.** HMAC-chained audit log satisfies regulated-industry requirements Overcode doesn't address.
- **Installable.** PyPI, Homebrew, COPR, npm. Overcode is private.
- **Git isolation by default.** Worktree-per-agent prevents the "two agents editing the same file" class of problems entirely. Overcode assumes human supervision catches this.
- **GitHub App integration.** Slash commands on PRs (`/bernstein fix-lint`), check runs, webhook-driven triggers.
- **ML routing.** LinUCB bandit learns model selection per task. Overcode has no model selection policy.
- **Documentation depth.** 135 docs, migration guides, ReadTheDocs, ADRs in `docs/decisions/`. Overcode has design docs only.
- **Observability.** Prometheus/OTel/Grafana out of the box vs Overcode's in-TUI metrics only.

## What Overcode Does Better

- **Live supervision of long-lived sessions.** Overcode is built for the "agent that's been running for an hour and is stuck" case. Bernstein's agents are short-lived; once spawned they run to completion or die. Overcode's heartbeat + standing instructions (25 presets) + Claude-powered supervisor daemon is a whole layer Bernstein doesn't have.
- **tmux-native.** Users who want to drop into a pane and type at the agent can. Bernstein agents run as piped subprocesses with no shell visibility.
- **Agent hierarchy and fork-with-context.** Overcode supports parent/child trees 5 levels deep with cascade kill and conversation forking. Bernstein's hierarchy is a flat task DAG emitted by the manager.
- **Status detection for Claude specifically.** 442 regex patterns + Claude Code hooks give zero-latency authoritative detection with a rich taxonomy (waiting_user, waiting_approval, error, compact_running, side_question, etc.). Bernstein's status is coarse (RUNNING/STALLED/DEAD) because it doesn't need more.
- **Keyboard-dense TUI.** ~50+ bindings and a configurable column model; Bernstein's TUI is lighter-weight.
- **Mid-flight intervention.** Overcode can inject an instruction into a running agent's tmux pane. Bernstein's equivalents are coarse (kill signal, approve/reject at gates).
- **Side-question subagents with token accounting.** Overcode handles compact/side-question subagents and keeps their tokens from double-counting (bcfb3a6). Bernstein doesn't expose a comparable interactive aside.
- **Sister cross-machine integration** for monitoring agents across hosts.

## Ideas to Steal

| Idea | Value | Complexity | Notes |
|---|---|---|---|
| **Completion-signal fields on tasks** (`tests_pass`, `path_exists`, `lint_clean`, `type_check`) with an automatic verifier before marking done | High | Medium | Would force users to articulate "done" up front; Overcode could run these as an optional lint before the user clears an agent. Source of pattern: `src/bernstein/core/quality/` gate modules and `src/bernstein/core/tasks/task_completion.py`. |
| **Hard budget thresholds at 80/95/100%** with predictive alerts before exhaustion | High | Low | Overcode's soft-skip is easy to miss; a hard 100% stop plus an 80% warning is a small change with large blast-radius reduction. See `src/bernstein/core/cost/cost_tracker.py:42-44` and `notifications.py:53-66`. |
| **HMAC-chained audit log** for agent spawn/kill/instruction/budget events | Medium | Medium | Small compliance upside for users in regulated industries; implementation is ~200 LOC. Reference: `src/bernstein/core/security/audit.py` + `audit_integrity.py`. |
| **Z-score cost anomaly detection** per task type | Medium | Low | Flags a runaway agent 10× faster than a flat threshold. Reference: `src/bernstein/core/cost/cost_anomaly.py`. |
| **`--headless` mode with JSON output and non-zero exit on failure** | High | Low | Unlocks CI/cron use cases and makes Overcode scriptable outside a terminal. Reference: `src/bernstein/cli/main.py:485`. |
| **`init-wizard` / `doctor` / `debug-bundle` commands** for onboarding and support | Medium | Low | Huge DX wins: `bernstein doctor` pre-flights environment; `bernstein debug-bundle` zips logs + config for bug reports. |
| **Predictive alerts** (budget exhaustion, completion-rate decline, run-overrun) | Medium | Medium | Acts on trend, not state. Reference: `src/bernstein/core/communication/notifications.py:53-66`. |
| **Pluggy plugin system** for user-defined hooks (before_spawn, after_spawn, on_failure) | Medium | Medium | Lets users add org-specific approval gates, telemetry, secret scanners. Reference: `src/bernstein/plugins/hookspecs.py:92-150`. |
| **Structured agent state enum with color + spinner frame per state** | Low | Low | Nicer-looking TUI. Reference: `src/bernstein/tui/agent_states.py:24-79`. |
| **Prometheus `/metrics` endpoint** from the Overcode web dashboard | Medium | Low | Overcode already has a dashboard; adding `/metrics` is trivial and opens Grafana integration. Reference: `src/bernstein/core/prometheus.py`. |
| **Multi-channel notification fan-out** (Slack/Discord/PagerDuty/email/webhook) with event taxonomy | Medium | Medium | Overcode's documented gap is notifications; copy the event catalogue. Reference: `src/bernstein/core/communication/notifications.py`. |
| **Optional worktree isolation mode** for Overcode users who do want it | High | High | Would close the biggest feature gap. Bernstein's `WorktreeSetupConfig` with shallow clone + symlinks (`src/bernstein/core/git/worktree.py:42-73`) is a good reference implementation. |

---

*Analyzed: 2026-04-15. Source repo cloned to `~/Code/bernstein` at commit HEAD of main.*
