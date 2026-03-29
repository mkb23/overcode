"""
SSH provisioner — ensures remote overcode instances are running and version-matched.

When a sister has SSH configured, this module can:
1. Check if overcode web server is running on the remote
2. Compare versions and upgrade if needed
3. Bootstrap overcode via uvx if not installed/running
4. Start the monitor daemon on the remote
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# SSH flags for ControlMaster multiplexing — reused by proxy windows
SSH_CONTROL_OPTS = [
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=/tmp/overcode-ssh-%r@%h:%p",
    "-o", "ControlPersist=300",
]


@dataclass
class ProvisionResult:
    """Result of a remote provisioning attempt."""
    ok: bool
    remote_version: str = ""
    action: str = ""  # "already_running", "upgraded", "bootstrapped", "failed"
    error: str = ""


def _get_local_version() -> str:
    """Get the locally installed overcode version."""
    try:
        import importlib.metadata
        return importlib.metadata.version("overcode")
    except Exception:
        return "dev"


def _ssh_run(ssh_target: str, command: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a command on the remote host via SSH."""
    cmd = [
        "ssh",
        *SSH_CONTROL_OPTS,
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        ssh_target,
        command,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _check_health_local(sister_url: str, api_key: str = "") -> Optional[dict]:
    """Check if overcode web is reachable via the sister URL (no SSH needed).

    Returns parsed health JSON, or None if not reachable.
    """
    from urllib.request import Request, urlopen
    from urllib.error import URLError
    import socket as _socket

    url = f"{sister_url.rstrip('/')}/health"
    try:
        req = Request(url, method="GET")
        if api_key:
            req.add_header("X-API-Key", api_key)
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("status") == "ok":
                return data
    except (URLError, _socket.timeout, json.JSONDecodeError, OSError):
        pass
    return None


def _check_health_ssh(ssh_target: str, port: int) -> Optional[dict]:
    """Check if overcode web is running via SSH (for servers bound to localhost).

    Returns parsed health JSON, or None if not reachable.
    """
    try:
        result = _ssh_run(
            ssh_target,
            f"curl -s --connect-timeout 3 http://127.0.0.1:{port}/health 2>/dev/null",
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            if data.get("status") == "ok":
                return data
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    return None


def _check_version_local(sister_url: str, api_key: str = "") -> str:
    """Get the remote overcode version via the sister URL."""
    from urllib.request import Request, urlopen
    from urllib.error import URLError
    import socket as _socket

    url = f"{sister_url.rstrip('/')}/api/status"
    try:
        req = Request(url, method="GET")
        if api_key:
            req.add_header("X-API-Key", api_key)
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("version", "")
    except (URLError, _socket.timeout, json.JSONDecodeError, OSError):
        return ""


def _start_overcode(ssh_target: str, version: str, port: int, host: str) -> bool:
    """Start overcode web + monitor daemon on the remote via uvx."""
    version_spec = f"overcode@{version}" if version != "dev" else "overcode"
    try:
        # Start web server (nohup + background so SSH can exit)
        result = _ssh_run(
            ssh_target,
            f"nohup uvx {version_spec} web --port {port} --host {host} > /dev/null 2>&1 & "
            f"sleep 1 && "
            f"nohup uvx {version_spec} monitor-daemon start > /dev/null 2>&1 &",
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _stop_overcode(ssh_target: str, port: int) -> bool:
    """Stop overcode web server on the remote."""
    try:
        # Try using overcode's built-in stop first, fall back to curl
        result = _ssh_run(
            ssh_target,
            f"curl -s --connect-timeout 3 -X POST http://127.0.0.1:{port}/api/shutdown 2>/dev/null; sleep 1",
            timeout=15,
        )
        return True
    except (subprocess.TimeoutExpired, OSError):
        return False


def ensure_remote_ready(
    ssh_target: str,
    sister_url: str,
    api_key: str = "",
    port: int = 8080,
    host: str = "127.0.0.1",
) -> ProvisionResult:
    """Check remote overcode, install/upgrade if needed, start if not running.

    Args:
        ssh_target: SSH target (e.g., "user@host")
        sister_url: Sister's HTTP URL (e.g., "http://host:8081")
        api_key: API key for sister authentication
        port: Remote web server port (extracted from sister_url)
        host: Remote web server bind host

    Returns:
        ProvisionResult with status of the provisioning
    """
    local_version = _get_local_version()

    # Step 1: Check if overcode web is running — try direct HTTP first, fall back to SSH
    health = _check_health_local(sister_url, api_key) or _check_health_ssh(ssh_target, port)
    if health and health.get("status") == "ok":
        # Running — check version
        remote_version = health.get("version", "") or _check_version_local(sister_url, api_key)

        if not remote_version or remote_version == local_version:
            # No version reported (old remote) or versions match — accept as-is
            return ProvisionResult(
                ok=True,
                remote_version=remote_version or "unknown",
                action="already_running",
            )

        # Version mismatch — don't downgrade if remote is newer
        if remote_version > local_version:
            logger.warning(
                "Remote %s has newer overcode (%s > %s), skipping upgrade",
                ssh_target, remote_version, local_version,
            )
            return ProvisionResult(
                ok=True,
                remote_version=remote_version,
                action="already_running",
            )

        # Upgrade: stop old, start new
        logger.info("Upgrading remote %s: %s -> %s", ssh_target, remote_version, local_version)
        _stop_overcode(ssh_target, port)
        if _start_overcode(ssh_target, local_version, port, host):
            return ProvisionResult(
                ok=True,
                remote_version=local_version,
                action="upgraded",
            )
        return ProvisionResult(
            ok=False,
            remote_version=remote_version,
            action="failed",
            error="Failed to restart after upgrade",
        )

    # Step 2: Not running — bootstrap
    logger.info("Bootstrapping overcode on %s via uvx", ssh_target)
    if _start_overcode(ssh_target, local_version, port, host):
        # Verify it came up
        health = _check_health_local(sister_url, api_key) or _check_health_ssh(ssh_target, port)
        if health and health.get("status") == "ok":
            return ProvisionResult(
                ok=True,
                remote_version=local_version,
                action="bootstrapped",
            )
        return ProvisionResult(
            ok=False,
            action="failed",
            error="Started but health check failed — uvx or overcode may not be installed on remote",
        )

    return ProvisionResult(
        ok=False,
        action="failed",
        error="Failed to start overcode on remote — ensure uvx is installed",
    )
