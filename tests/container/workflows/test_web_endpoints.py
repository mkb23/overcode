"""Web server REST API (coverage matrix row 9).

Read endpoints, control gating (web.allow_control), API key auth, and a
launch-via-API round trip. The embedded dashboard pages get DOM/screenshot
coverage in tests/container/visual/.
"""

import requests
import pytest

from tests.container.harness import wait_for

pytestmark = pytest.mark.timeout(180)

CONTROL_ON = "web:\n  allow_control: true\n"
KEYED = "web:\n  api_key: sekrit-test-key\n  allow_control: true\n"


def _up(url):
    def probe():
        try:
            # Any HTTP answer (incl. 401 when keyed) means the server is up
            return requests.get(f"{url}/health", timeout=2).status_code < 500
        except requests.ConnectionError:
            return False
    wait_for(probe, timeout=30, desc=f"web server up at {url}")


def test_health_and_status_endpoints(oc, oc_wait):
    """/api/status reflects monitor daemon state, so the daemon must run."""
    oc.launch("webby", scenario="startup_idle")
    oc.start_monitor_daemon(interval=1)
    oc_wait(lambda: oc.agent_daemon_state("webby"), timeout=60,
            desc="daemon publishing webby")
    url = oc.start_web()
    _up(url)

    status = requests.get(f"{url}/api/status", timeout=5)
    assert status.status_code == 200
    payload = status.json()
    assert any(a.get("name") == "webby" for a in payload.get("agents", [])), payload

    for page in ("/", "/dashboard"):
        page_resp = requests.get(f"{url}{page}", timeout=5)
        assert page_resp.status_code == 200
        assert "<html" in page_resp.text.lower()


def test_control_disabled_by_default(oc, oc_wait):
    oc.launch("locked", scenario="startup_idle")
    oc_wait(lambda: oc.agent("locked"), desc="agent in registry")
    url = oc.start_web()  # empty config -> allow_control false
    _up(url)

    resp = requests.post(f"{url}/api/agents/locked/send",
                         json={"text": "nope"}, timeout=5)
    assert resp.status_code == 403


def test_control_send_and_kill(oc, oc_wait, sandbox):
    oc.launch("driven", scenario="startup_idle")
    oc_wait(lambda: "Claude" in oc.pane("driven"), desc="mock claude up")
    url = oc.start_web(CONTROL_ON)
    _up(url)

    send = requests.post(f"{url}/api/agents/driven/send",
                         json={"text": "via-the-web-api"}, timeout=10)
    assert send.status_code == 200, send.text
    oc_wait(lambda: "via-the-web-api" in oc.pane("driven"),
            desc="API-sent text in pane")

    kill = requests.post(f"{url}/api/agents/driven/kill", json={}, timeout=10)
    assert kill.status_code == 200, kill.text
    oc_wait(
        lambda: not any(w.startswith("driven") for w in sandbox.list_windows(oc.session)),
        desc="window gone after API kill",
    )


def test_unknown_agent_404(oc):
    url = oc.start_web(CONTROL_ON)
    _up(url)
    resp = requests.post(f"{url}/api/agents/no-such-agent/send",
                         json={"text": "x"}, timeout=5)
    assert resp.status_code == 404


def test_api_key_enforced(oc):
    url = oc.start_web(KEYED)
    _up(url)

    bare = requests.get(f"{url}/api/status", timeout=5)
    assert bare.status_code in (401, 403)

    keyed = requests.get(f"{url}/api/status",
                         headers={"X-API-Key": "sekrit-test-key"}, timeout=5)
    assert keyed.status_code == 200


def test_launch_via_api_roundtrip(oc, oc_wait):
    url = oc.start_web(CONTROL_ON)
    _up(url)

    resp = requests.post(
        f"{url}/api/agents/launch",
        json={"name": "api-born"},
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    oc_wait(lambda: oc.agent("api-born"), timeout=60,
            desc="API-launched agent in registry")
