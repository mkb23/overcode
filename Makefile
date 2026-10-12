# Makefile for overcode development and testing

.PHONY: help install test test-e2e test-unit clean lint typecheck format coverage metrics e2e e2e-real e2e-shell e2e-record e2e-status

help:
	@echo "Overcode Development Commands"
	@echo "================================"
	@echo ""
	@echo "Setup:"
	@echo "  make install        Install package in development mode with dev dependencies"
	@echo ""
	@echo "Testing:"
	@echo "  make test           Run all tests"
	@echo "  make test-e2e       Run only E2E integration tests (slow)"
	@echo "  make test-matrix    Mock-backed backend matrix (all backends, no keys)"
	@echo "  make test-live      Live smoke vs the real CLIs (OVERCODE_LIVE_BACKENDS=...)"
	@echo "  make test-unit      Run only fast unit tests"
	@echo "  make test-quick     Run E2E test directly (bypass pytest for faster dev)"
	@echo "  make e2e            Containerized E2E: workflow + visual tiers (Docker)"
	@echo "  make e2e-real       Containerized E2E: real-LLM smoke tier (needs CLAUDE_CODE_OAUTH_TOKEN)"
	@echo "  make e2e-shell      Interactive shell inside the E2E container"
	@echo "  make e2e-record TIER=<tier>  Run an expensive local e2e tier and write its report"
	@echo "  make e2e-status     How fresh each e2e report is (tests/e2e-reports/tiers.toml)"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint           Lint with ruff (the CI gate)"
	@echo "  make typecheck      Type check with mypy (not yet clean)"
	@echo "  make format         Format code with black"
	@echo "  make format-check   Check if code needs formatting"
	@echo "  make coverage       Unit suite under branch + thread coverage (htmlcov/)"
	@echo "  make metrics        Complexity, dead code, lint and size summary (build/metrics/)"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean              Remove build artifacts and caches"
	@echo "  make clean-sessions     Remove leftovers of interrupted test runs (safe)"
	@echo "  make emergency-cleanup  Kill ALL Claude processes, delete sessions.json (CONFIRM=1)"
	@echo ""

install:
	pip install -e ".[dev]"

test:
	pytest -v

test-e2e:
	pytest -v -m e2e -s

# Mock-backed backend matrix: every backend through launch/status/
# permission gestures/restart/kill in a real tmux, no credentials needed.
test-matrix:
	pytest -m e2e tests/e2e/test_backend_matrix.py tests/e2e/test_opencode2_backend.py

# Live smoke against the REAL agent CLIs on this host (spends real tokens).
# Name the backends: OVERCODE_LIVE_BACKENDS=opencode,codex make test-live
test-live:
	pytest -m e2e -rsx tests/e2e/test_live_backends.py

test-unit:
	pytest -q tests/unit -p no:cacheprovider --timeout=120

test-quick:
	@echo "Running E2E test directly (faster for development)..."
	python tests/test_e2e_multi_agent_jokes.py

# Containerized E2E (docs/design/e2e-devcontainer-testing.md)
e2e:
	./scripts/e2e.sh

e2e-real:
	./scripts/e2e.sh --real

e2e-shell:
	./scripts/e2e.sh --shell

# The expensive tiers (real CLIs, credentials, tokens) run here, not in CI.
# Record one, then commit tests/e2e-reports/<tier>.json: the release gate
# reads it (scripts/e2e_reports.py check).
e2e-record:
	@test -n "$(TIER)" || (echo "usage: make e2e-record TIER=<tier>"; python scripts/e2e_reports.py status; exit 2)
	python scripts/e2e_reports.py record $(TIER)

e2e-status:
	python scripts/e2e_reports.py status

lint:
	ruff check src

typecheck:
	mypy src/overcode

format:
	black src/ tests/

# Branch + thread coverage of the unit suite (.coveragerc); report and htmlcov/
coverage:
	rm -f .coverage .coverage.*
	coverage run -m pytest -q tests/unit -p no:cacheprovider --timeout=120
	coverage combine -q
	coverage report --sort=cover --skip-covered | tail -40
	coverage html -q
	@echo "HTML report: htmlcov/index.html"

metrics:
	python scripts/quality_metrics.py

format-check:
	black --check src/ tests/

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf htmlcov/ build/metrics/
	rm -f .coverage .coverage.*
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Test runs isolate themselves (tests/conftest.py: a temp state dir and a
# private tmux server, removed at the end), so this only clears what an
# interrupted run left behind. It never touches real agents or ~/.overcode.
clean-sessions:
	@echo "Cleaning up leftovers from interrupted test runs..."
	-@for s in $$(ls $${TMUX_TMPDIR:-/tmp}/tmux-$$(id -u) 2>/dev/null | grep -E '^(overcode-pytest-|overcode-test|oc-rename-)'); do \
		tmux -L "$$s" kill-server 2>/dev/null || true; done
	-rm -rf /tmp/overcode_e2e_test $${TMPDIR:-/tmp}/overcode-pytest-* /tmp/ocpt-*
	@echo "✓ Test leftovers cleaned"

# Destructive: kills EVERY Claude process (your live agents included) and
# deletes ~/.overcode/sessions/sessions.json. Requires CONFIRM=1.
emergency-cleanup:
	@test "$(CONFIRM)" = "1" || (echo "This kills every Claude process and deletes sessions.json. Run: make emergency-cleanup CONFIRM=1"; exit 2)
	@echo "🚨 EMERGENCY CLEANUP - Killing all Claude processes..."
	-pkill -9 claude
	-tmux kill-session -t overcode-e2e-test
	-rm -rf /tmp/overcode_e2e_test
	-rm -f ~/.overcode/sessions/sessions.json
	@echo "✓ Emergency cleanup complete"
	@echo ""
	@echo "Verifying cleanup..."
	@pgrep -f claude && echo "⚠️  WARNING: Claude processes still running" || echo "✓ No Claude processes found"
	@tmux has-session -t overcode-e2e-test 2>&1 && echo "⚠️  WARNING: Test tmux session still exists" || echo "✓ No test tmux session found"

# Development workflow
dev: install format lint test

# Quick feedback loop during development
quick: format-check test-quick
