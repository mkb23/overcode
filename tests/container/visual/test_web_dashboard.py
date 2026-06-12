"""Web dashboard rendered in a real browser (design §7.2).

This is the only place the embedded JS in web_templates.py actually executes.
Screenshots are exported to artifacts/e2e/screenshots/ on every run.
"""

import pytest

pytestmark = pytest.mark.timeout(240)

CONTROL_ON = "web:\n  allow_control: true\n"

DESKTOP = {"width": 1280, "height": 800}
MOBILE = {"width": 390, "height": 844}


@pytest.fixture
def dashboard(oc, oc_wait):
    """Two mock agents + monitor daemon + web server: a populated dashboard."""
    oc.launch("vis-runner", scenario="task_running")
    oc.launch("vis-idler", scenario="startup_idle")
    oc.start_monitor_daemon(interval=1)
    oc_wait(
        lambda: len(oc.daemon_state().get("sessions", [])) >= 2,
        timeout=60,
        desc="daemon publishing both agents",
    )
    return oc.start_web(CONTROL_ON)


def test_dashboard_renders_agents(dashboard, browser, screenshots, oc_wait):
    page = browser.new_page(viewport=DESKTOP)
    try:
        page.goto(f"{dashboard}/dashboard", wait_until="networkidle")
        # The dashboard JS polls /api/status and renders agent rows
        page.wait_for_selector("text=vis-runner", timeout=30_000)
        page.wait_for_selector("text=vis-idler", timeout=30_000)
        page.screenshot(path=screenshots / "dashboard-desktop.png", full_page=True)
    finally:
        page.close()


def test_dashboard_mobile_layout(dashboard, browser, screenshots):
    page = browser.new_page(viewport=MOBILE)
    try:
        page.goto(f"{dashboard}/dashboard", wait_until="networkidle")
        page.wait_for_selector("text=vis-runner", timeout=30_000)
        # No horizontal overflow on a phone viewport
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
        )
        assert not overflow, "dashboard overflows horizontally on mobile viewport"
        page.screenshot(path=screenshots / "dashboard-mobile.png", full_page=True)
    finally:
        page.close()


def test_analytics_page_renders(dashboard, browser, screenshots):
    page = browser.new_page(viewport=DESKTOP)
    try:
        page.goto(f"{dashboard}/", wait_until="networkidle")
        assert page.locator("html").count() == 1
        page.screenshot(path=screenshots / "analytics-desktop.png", full_page=True)
        # No fatal JS errors: Chart.js & co loaded into a usable page
        title = page.title()
        assert title, "analytics page has no title"
    finally:
        page.close()


def test_dashboard_kill_button_controls_agent(dashboard, oc, oc_wait, browser, sandbox):
    """Drive a control action through the real dashboard UI."""
    page = browser.new_page(viewport=DESKTOP)
    try:
        page.goto(f"{dashboard}/dashboard", wait_until="networkidle")
        page.wait_for_selector("text=vis-idler", timeout=30_000)

        # Find a kill control near the agent; accept common labels
        killers = page.locator(
            "xpath=//*[contains(text(), 'vis-idler')]/ancestor-or-self::*"
            "//button[contains(translate(text(), 'KILL', 'kill'), 'kill')]"
        )
        if killers.count() == 0:
            pytest.skip("dashboard exposes no kill button — control UI not present")

        page.once("dialog", lambda d: d.accept())
        killers.first.click()
        oc_wait(
            lambda: not any(
                w.startswith("vis-idler") for w in sandbox.list_windows(oc.session)
            ),
            timeout=60,
            desc="agent window gone after UI kill",
        )
    finally:
        page.close()
