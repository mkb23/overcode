"""Tier 3: real Claude Code smoke tests.

Gated twice: OVERCODE_E2E_REAL_LLM=1 (set by scripts/e2e.sh --real) and a
CLAUDE_CODE_OAUTH_TOKEN in the environment. Every test asserts machine-
checkable outcomes, never transcript content. A cost fuse aborts the session
if cumulative estimated cost exceeds OVERCODE_E2E_COST_CAP_USD (default $2).
"""

import json
import os

import pytest

from tests.container.harness import OvercodeCLI

COST_CAP_USD = float(os.environ.get("OVERCODE_E2E_COST_CAP_USD", "2.0"))

_cumulative_cost = 0.0


def pytest_collection_modifyitems(config, items):
    # NB: this hook receives ALL session items, not just this directory's —
    # scope strictly to real_llm or we'd skip the entire suite.
    here = os.path.dirname(__file__)
    our_items = [i for i in items if str(i.fspath).startswith(here)]

    if os.environ.get("OVERCODE_E2E_REAL_LLM") != "1":
        skip = pytest.mark.skip(reason="real-LLM tier runs via scripts/e2e.sh --real")
    elif not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        skip = pytest.mark.skip(reason="CLAUDE_CODE_OAUTH_TOKEN not set")
    else:
        skip = None

    for item in our_items:
        item.add_marker(pytest.mark.real_llm)
        if skip is not None:
            item.add_marker(skip)


@pytest.fixture
def roc_wait(roc):
    from tests.container.harness import wait_for

    def _wait(predicate, timeout=30.0, interval=0.5, desc="condition"):
        return wait_for(predicate, timeout=timeout, interval=interval, desc=desc,
                        on_fail=roc.diagnostics)

    return _wait


@pytest.fixture
def roc(make_oc):
    """OverCode instance driving REAL claude (haiku, onboarding pre-seeded)."""
    cli = make_oc("real")
    # Real binary instead of the mock
    cli.env.pop("CLAUDE_COMMAND", None)
    # Pre-seed onboarding so claude doesn't block on theme/trust dialogs
    claude_config = {
        "hasCompletedOnboarding": True,
        "theme": "dark",
        "autoUpdates": False,
    }
    (cli.home / ".claude.json").write_text(json.dumps(claude_config))
    yield cli
    _check_cost_fuse(cli)


def _check_cost_fuse(cli: OvercodeCLI) -> None:
    global _cumulative_cost
    state = cli.daemon_state()
    run_cost = sum(
        s.get("estimated_cost_usd", 0.0) for s in state.get("sessions", [])
    )
    _cumulative_cost += run_cost
    if _cumulative_cost > COST_CAP_USD:
        pytest.exit(
            f"cost fuse blown: ${_cumulative_cost:.2f} > ${COST_CAP_USD:.2f} cap",
            returncode=2,
        )


def launch_real(cli: OvercodeCLI, name: str, prompt: str, workdir, *args):
    """Launch a real haiku agent with a verifiable task."""
    return cli.ok(
        "launch", "-n", name,
        "-d", str(workdir),
        "-p", prompt,
        "--model", "haiku",
        "--bypass-permissions",
        *args,
        timeout=60,
    )
