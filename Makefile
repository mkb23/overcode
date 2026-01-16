# Makefile for overcode development and testing

.PHONY: help install test test-e2e test-unit clean lint format

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
	@echo "  make test-unit      Run only fast unit tests"
	@echo "  make test-quick     Run E2E test directly (bypass pytest for faster dev)"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint           Run type checking with mypy"
	@echo "  make format         Format code with black"
	@echo "  make format-check   Check if code needs formatting"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean              Remove build artifacts and caches"
	@echo "  make clean-sessions     Kill test sessions and cleanup state (safe)"
	@echo "  make emergency-cleanup  Nuclear option - kill ALL Claude processes (dangerous!)"
	@echo ""

install:
	pip install -e ".[dev]"

test:
	pytest -v

test-e2e:
	pytest -v -m e2e -s

test-unit:
	pytest -v -m unit

test-quick:
	@echo "Running E2E test directly (faster for development)..."
	python tests/test_e2e_multi_agent_jokes.py

lint:
	mypy src/overcode

format:
	black src/ tests/

format-check:
	black --check src/ tests/

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

clean-sessions:
	@echo "Cleaning up test sessions..."
	-pkill -9 claude 2>/dev/null || true
	-tmux kill-session -t overcode-e2e-test 2>/dev/null || true
	-rm -rf /tmp/overcode_e2e_test
	@python3 -c "import json; from pathlib import Path; \
		state_file = Path.home() / '.overcode' / 'sessions' / 'sessions.json'; \
		state = json.load(open(state_file)) if state_file.exists() else {}; \
		updated = {k: v for k, v in state.items() if not v.get('name', '').startswith('test-')}; \
		json.dump(updated, open(state_file, 'w'), indent=2) if state_file.exists() else None" 2>/dev/null || true
	@echo "✓ Test sessions cleaned"

emergency-cleanup:
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
