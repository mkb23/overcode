"""
Client for sending control commands to sister overcode instances.

Used by the TUI when acting on remote agents. Each method sends an HTTP
request to the sister's web API and returns a Result.
"""

import json
from dataclasses import dataclass
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass
class ControlResult:
    """Result of a control action sent to a sister."""
    ok: bool
    data: dict = None
    error: str = ""

    def __post_init__(self):
        if self.data is None:
            self.data = {}


class SisterController:
    """HTTP client for sending commands to sister web servers."""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def _request(
        self,
        method: str,
        sister_url: str,
        api_key: str,
        path: str,
        body: Optional[dict] = None,
    ) -> ControlResult:
        """Send an HTTP request to a sister's control API."""
        url = f"{sister_url.rstrip('/')}{path}"

        data = json.dumps(body or {}).encode("utf-8")
        req = Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if api_key:
            req.add_header("X-API-Key", api_key)

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return ControlResult(ok=result.get("ok", True), data=result)
        except HTTPError as e:
            try:
                error_body = json.loads(e.read().decode("utf-8"))
                return ControlResult(ok=False, error=error_body.get("error", str(e)))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return ControlResult(ok=False, error=f"HTTP {e.code}: {e.reason}")
        except (URLError, OSError) as e:
            return ControlResult(ok=False, error=f"Connection error: {e}")

    # --- Agent Interaction ---

    def send_instruction(
        self, sister_url: str, api_key: str, agent_name: str,
        text: str, enter: bool = True,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            f"/api/agents/{agent_name}/send",
            {"text": text, "enter": enter},
        )

    def send_key(
        self, sister_url: str, api_key: str, agent_name: str, key: str,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            f"/api/agents/{agent_name}/keys",
            {"key": key},
        )

    def kill_agent(
        self, sister_url: str, api_key: str, agent_name: str,
        cascade: bool = True,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            f"/api/agents/{agent_name}/kill",
            {"cascade": cascade},
        )

    def restart_agent(
        self, sister_url: str, api_key: str, agent_name: str,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            f"/api/agents/{agent_name}/restart",
        )

    def launch_agent(
        self, sister_url: str, api_key: str,
        directory: str, name: str,
        prompt: Optional[str] = None,
        permissions: str = "normal",
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            "/api/agents/launch",
            {"directory": directory, "name": name,
             "prompt": prompt, "permissions": permissions},
        )

    # --- Agent Configuration ---

    def set_standing_orders(
        self, sister_url: str, api_key: str, agent_name: str,
        text: Optional[str] = None, preset: Optional[str] = None,
    ) -> ControlResult:
        body = {}
        if text is not None:
            body["text"] = text
        if preset is not None:
            body["preset"] = preset
        return self._request(
            "PUT", sister_url, api_key,
            f"/api/agents/{agent_name}/standing-orders",
            body,
        )

    def clear_standing_orders(
        self, sister_url: str, api_key: str, agent_name: str,
    ) -> ControlResult:
        return self._request(
            "DELETE", sister_url, api_key,
            f"/api/agents/{agent_name}/standing-orders",
        )

    def set_budget(
        self, sister_url: str, api_key: str, agent_name: str, usd: float,
    ) -> ControlResult:
        return self._request(
            "PUT", sister_url, api_key,
            f"/api/agents/{agent_name}/budget",
            {"usd": usd},
        )

    def set_value(
        self, sister_url: str, api_key: str, agent_name: str, value: int,
    ) -> ControlResult:
        return self._request(
            "PUT", sister_url, api_key,
            f"/api/agents/{agent_name}/value",
            {"value": value},
        )

    def set_annotation(
        self, sister_url: str, api_key: str, agent_name: str, text: str,
    ) -> ControlResult:
        return self._request(
            "PUT", sister_url, api_key,
            f"/api/agents/{agent_name}/annotation",
            {"text": text},
        )

    def set_sleep(
        self, sister_url: str, api_key: str, agent_name: str, asleep: bool,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            f"/api/agents/{agent_name}/sleep",
            {"asleep": asleep},
        )

    # --- Heartbeat Control ---

    def configure_heartbeat(
        self, sister_url: str, api_key: str, agent_name: str,
        enabled: bool = True,
        frequency: Optional[str] = None,
        instruction: Optional[str] = None,
    ) -> ControlResult:
        body = {"enabled": enabled}
        if frequency is not None:
            body["frequency"] = frequency
        if instruction is not None:
            body["instruction"] = instruction
        return self._request(
            "PUT", sister_url, api_key,
            f"/api/agents/{agent_name}/heartbeat",
            body,
        )

    def pause_heartbeat(
        self, sister_url: str, api_key: str, agent_name: str,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            f"/api/agents/{agent_name}/heartbeat/pause",
        )

    def resume_heartbeat(
        self, sister_url: str, api_key: str, agent_name: str,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            f"/api/agents/{agent_name}/heartbeat/resume",
        )

    # --- Feature Toggles ---

    def set_time_context(
        self, sister_url: str, api_key: str, agent_name: str, enabled: bool,
    ) -> ControlResult:
        return self._request(
            "PUT", sister_url, api_key,
            f"/api/agents/{agent_name}/time-context",
            {"enabled": enabled},
        )

    def set_hook_detection(
        self, sister_url: str, api_key: str, agent_name: str, enabled: bool,
    ) -> ControlResult:
        return self._request(
            "PUT", sister_url, api_key,
            f"/api/agents/{agent_name}/hook-detection",
            {"enabled": enabled},
        )

    # --- Bulk Operations ---

    def transport_all(
        self, sister_url: str, api_key: str,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            "/api/agents/transport",
        )

    def cleanup_agents(
        self, sister_url: str, api_key: str, include_done: bool = False,
    ) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            "/api/agents/cleanup",
            {"include_done": include_done},
        )

    # --- System Control ---

    def restart_monitor(self, sister_url: str, api_key: str) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            "/api/daemon/monitor/restart",
        )

    def start_supervisor(self, sister_url: str, api_key: str) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            "/api/daemon/supervisor/start",
        )

    def stop_supervisor(self, sister_url: str, api_key: str) -> ControlResult:
        return self._request(
            "POST", sister_url, api_key,
            "/api/daemon/supervisor/stop",
        )
