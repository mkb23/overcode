"""The spend gateway: a corp LLM gateway that evals and sims spend through.

``docs/design/experiment-billing.md``. The gateway itself lives on the corp
network (not in overcode). overcode's side:

* **Identity.** Every agent has a gateway *token*, ``ocg_<agent id>.<mac>``:
  the agent's id and an HMAC of it under a secret only this overcode holds
  (``ledger/.gateway_secret``), so a token cannot be made up for another
  agent and needs no storage (a restart or a rename keeps it). The variables
  of ``billing.gateway.env`` (``{token}`` substituted) are set in every
  agent's launch line and every ``overcode bash`` job an agent starts, so
  every process an agent spawns knows the gateway, and only it, by that
  token. A child agent has its own token.
* **overcode → gateway** (``PUT {url}/allowances``): every live agent's
  token, agent, code and ``limit_usd``: what it may still spend through the
  gateway, its budget less its own conversation's spend (``null``: no
  budget). The gateway refuses a token whose metered spend has reached its
  limit, and any token missing from the last push.
* **gateway → overcode** (``GET {url}/usage?after=<cursor>``): metered usage
  events after a cursor. Each sync records them (``external_spend.
  record_batch``) as one run per agent (``gw-<agent id>``), the cursor saved
  in the same write: each event is billed once. A token that does not
  verify is billed to ``billing.default_code`` (the money stays visible).

One sync at a time (``.gateway_sync.lock``, not waited for): the daemon's
housekeeping thread and ``overcode spend sync`` never read the same cursor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Mapping, Optional

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:  # pragma: no cover - Windows
    HAS_FCNTL = False

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "ocg_"
SECRET_NAME = ".gateway_secret"
CURSOR = "gateway"
RUN_PREFIX = "gw-"
UNATTRIBUTED_RUN = "gw-unattributed"
PAGE_LIMIT = 1000
MAX_PAGES = 50


class GatewayError(RuntimeError):
    """The gateway could not be reached, or answered something unusable."""


# ── tokens ──────────────────────────────────────────────────────────────


def _secret(ledger_dir: Path) -> bytes:
    path = Path(ledger_dir) / SECRET_NAME
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32).encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another process made it first
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(value)
    return value


def _mac(secret: bytes, agent_id: str) -> str:
    return hmac.new(secret, agent_id.encode(), hashlib.sha256).hexdigest()[:32]


def token_for(ledger_dir: Path, agent_id: str) -> str:
    """The gateway token of ``agent_id``."""
    return f"{TOKEN_PREFIX}{agent_id}.{_mac(_secret(ledger_dir), agent_id)}"


def agent_of(ledger_dir: Path, token) -> Optional[str]:
    """The agent id a token was made for, or None when it does not verify."""
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or "." not in token:
        return None
    agent_id, _, mac = token[len(TOKEN_PREFIX):].rpartition(".")
    if not agent_id or not hmac.compare_digest(mac, _mac(_secret(ledger_dir), agent_id)):
        return None
    return agent_id


def worker_env(cfg: Optional[Mapping], ledger_dir: Path, agent_id: Optional[str]) -> Dict[str, str]:
    """The ``billing.gateway.env`` variables for ``agent_id``'s processes ({} without either)."""
    if not cfg or not cfg.get("env") or not agent_id:
        return {}
    token = token_for(ledger_dir, agent_id)
    return {name: value.replace("{token}", token).replace("{agent_id}", agent_id)
            for name, value in cfg["env"].items()}


def configured_worker_env(ledger_dir: Path, agent_id: Optional[str]) -> Dict[str, str]:
    """``worker_env`` from config; {} with no gateway configured or a config error."""
    try:
        from .config import get_gateway_config

        return worker_env(get_gateway_config(), ledger_dir, agent_id)
    except Exception as e:  # never fail a launch for the gateway
        logger.warning("gateway env not set: %s", e)
        return {}


# ── the API ─────────────────────────────────────────────────────────────


class GatewayClient:
    """The gateway's overcode API (module docstring), over HTTP(S)."""

    def __init__(self, url: str, key: Optional[str] = None, timeout: float = 10.0):
        self.url = url.rstrip("/")
        self.key = key
        self.timeout = timeout

    @classmethod
    def from_config(cls, cfg: Mapping, env: Optional[Mapping[str, str]] = None) -> "GatewayClient":
        if not cfg.get("url"):
            raise GatewayError("billing.gateway.url is not set")
        env = os.environ if env is None else env
        return cls(cfg["url"], env.get(cfg.get("admin_key_env") or ""), cfg.get("timeout_seconds", 10.0))

    def _call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.key:
            request.add_header("Authorization", f"Bearer {self.key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8")
        except Exception as e:
            raise GatewayError(f"{method} {path}: {e}") from e
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as e:
            raise GatewayError(f"{method} {path}: not JSON") from e

    def usage(self, after=None, limit: int = PAGE_LIMIT) -> dict:
        """``{"events": [...], "next": cursor, "more": bool}``: events after ``after``."""
        query = {"limit": limit, **({"after": after} if after is not None else {})}
        page = self._call("GET", "/usage?" + urllib.parse.urlencode(query))
        if not isinstance(page, dict) or not isinstance(page.get("events"), list):
            raise GatewayError("GET /usage: no 'events' list")
        return page

    def put_allowances(self, allowances: List[dict]) -> None:
        self._call("PUT", "/allowances", {"agents": allowances})


# ── sync ────────────────────────────────────────────────────────────────


@contextmanager
def _sync_lock(ledger_dir: Path) -> Iterator[bool]:
    """Whether this process may sync now (another sync holds it: False, no wait)."""
    Path(ledger_dir).mkdir(parents=True, exist_ok=True)
    if not HAS_FCNTL:
        yield True
        return
    fd = os.open(Path(ledger_dir) / ".gateway_sync.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def allowances(manager) -> List[dict]:
    """What every live agent may still spend through the gateway (module docstring)."""
    from .ledger import billing_code_of

    ledger = manager.ledger
    out = []
    for session in manager.list_sessions():
        if getattr(session, "is_remote", False) is True:
            continue
        budget = float(session.cost_budget_usd or 0.0)
        own = float(session.stats.estimated_cost_usd or 0.0)
        code = billing_code_of(session)
        out.append({
            "token": token_for(ledger.dir, session.id),
            "agent_id": session.id, "agent_name": session.name,
            "parent_id": session.parent_session_id,
            "code_id": code, "code_name": ledger.codes.name_of(code),
            # What the gateway may let through in total (its own metered
            # spend counts against it): the budget less the agent's own
            "limit_usd": max(0.0, budget - own) if budget > 0 else None,
            "metered_usd": float(getattr(session.stats, "external_cost_usd", 0.0) or 0.0),
        })
    return out


def _num(event: Mapping, *names: str) -> int:
    for name in names:
        value = event.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return 0


def _items(manager, events: List[dict], found: Dict[str, object]):
    """The ``record_batch`` items of usage events; ``found`` caches agent lookups."""
    from .config import get_billing_defaults
    from .external_spend import Item, Spend, Target, _find_agent

    ledger_dir = manager.ledger.dir
    items = []
    for event in events:
        if not isinstance(event, dict):
            continue
        cost = event.get("cost_usd")
        cost = (float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                and cost >= 0 else None)
        spend = Spend(model=event.get("model") or "unknown",
                      tok_in=_num(event, "input_tokens", "prompt_tokens"),
                      tok_out=_num(event, "output_tokens", "completion_tokens"),
                      tok_cw=_num(event, "cache_creation_input_tokens", "cache_write_tokens"),
                      tok_cr=_num(event, "cache_read_input_tokens", "cache_read_tokens"),
                      usd=cost, provider=event.get("provider") or None)
        agent_id = agent_of(ledger_dir, event.get("token"))
        if agent_id is not None and agent_id not in found:
            found[agent_id] = _find_agent(manager, agent_id)
        record = found.get(agent_id) if agent_id else None
        source = {"gateway": True, **({"event": event["id"]} if event.get("id") else {})}
        if record is None:
            code = get_billing_defaults()["default_code"]
            items.append(Item(Target(code_name=code), spend, UNATTRIBUTED_RUN,
                              "gateway: unattributed", source))
            continue
        target = Target(agent_id=record["id"], agent_name=record.get("name"),
                        live=bool(record.get("_live")), record=record)
        items.append(Item(target, spend, RUN_PREFIX + record["id"],
                          f"gateway ({record.get('name') or record['id'][:8]})", source))
    return items


def sync(manager, client: GatewayClient, *, now: Optional[datetime] = None,
         max_pages: int = MAX_PAGES) -> Optional[dict]:
    """Push allowances, then record usage events up to the gateway's newest.

    Returns ``{"agents", "events", "recorded_usd", "pages"}``, or None when
    another sync is running. Raises GatewayError when the gateway fails;
    whatever pages were recorded before stay recorded.
    """
    from .external_spend import _load_state, record_batch

    ledger = manager.ledger
    with _sync_lock(ledger.dir) as mine:
        if not mine:
            return None
        pushed = allowances(manager)
        client.put_allowances(pushed)
        counts = {"agents": len(pushed), "events": 0, "recorded_usd": 0.0, "pages": 0}
        found: Dict[str, object] = {}
        for _ in range(max_pages):
            cursor = _load_state(ledger).get("cursors", {}).get(CURSOR)
            page = client.usage(after=cursor)
            events = page["events"]
            following = page.get("next", cursor)
            counts["pages"] += 1
            if events or following != cursor:
                outs = record_batch(manager, _items(manager, events, found), now=now,
                                    cursor=(CURSOR, following))
                counts["events"] += len(events)
                counts["recorded_usd"] += sum(o["cost"] for o in outs)
            if not page.get("more") or not events:
                break
        return counts
