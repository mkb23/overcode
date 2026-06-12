"""Fixtures for visual/layout tests (tier 2)."""

import pytest

from tests.container.harness import artifacts_dir


def pytest_collection_modifyitems(config, items):
    # Hook receives ALL session items — mark only this directory's
    import os

    here = os.path.dirname(__file__)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(pytest.mark.visual)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def screenshots():
    """Where this run's screenshots land (mounted out to artifacts/e2e)."""
    path = artifacts_dir() / "screenshots"
    path.mkdir(parents=True, exist_ok=True)
    return path
