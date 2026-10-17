"""``overcode statusline``: a statusline command that samples the quota for calibration.

Opt-in only (``quota.calibration: true``, ``overcode usage calibration
on``): with it on, agents overcode launches get ``statusLine`` in their
``--settings`` pointing here (``launch_setting``). Off, nothing changes:
no setting is added and nothing here runs.

Claude Code runs the statusline command after each assistant message with
a JSON document on stdin. This command:

1. appends a compact sample (time, the overcode agent id, Claude's session
   id, model, ``rate_limits``, ``cost.total_cost_usd`` and any prompt-cache
   statistics) to ``<usage dir>/statusline/<agent>.jsonl`` when it differs
   from the last one written there; the engine reads these
   (``quota_engine``) for the exact times the weekly percentage ticks over;
2. runs the user's own statusline command, if they configured one
   (project ``.claude/settings.local.json``, then ``.claude/settings.json``,
   then the user's ``settings.json``), with the same stdin, and prints its
   output, so their statusline looks as it did. Without one it prints
   nothing.

It never fails: any error in (1) is swallowed, (2) has a timeout, and the
exit status is always 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Mapping, Optional

CHAIN_TIMEOUT_SECONDS = 10.0
MAX_FILE_BYTES = 20 * 1024 * 1024
# A configured statusline that is this one (or an older spelling) is not chained
_SELF_MARKERS = ("overcode statusline", "overcode.statusline_tap", "-m overcode.cli statusline")


def launch_setting(overcode_bin: str, config: Optional[Mapping] = None) -> Optional[dict]:
    """The ``statusLine`` for an agent's ``--settings``, None when calibration is off."""
    if config is None:
        try:
            from .config import get_quota_config
            config = get_quota_config()
        except Exception:
            return None
    if not config.get("calibration"):
        return None
    setting = {"type": "command", "command": f"{overcode_bin} statusline"}
    user = find_user_statusline(None)
    if user and isinstance(user.get("padding"), int):
        setting["padding"] = user["padding"]
    return setting


def _settings_files(cwd: Optional[str]) -> list:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    user_dir = Path(base) if base else Path.home() / ".claude"
    files = []
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.local.json", Path(cwd) / ".claude" / "settings.json"]
    files.append(user_dir / "settings.json")
    return files


def find_user_statusline(cwd: Optional[str]) -> Optional[dict]:
    """The statusLine the user configured (the most specific settings file's), else None."""
    for path in _settings_files(cwd):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        line = data.get("statusLine") if isinstance(data, dict) else None
        if not isinstance(line, dict) or line.get("type", "command") != "command":
            continue
        command = line.get("command")
        if not isinstance(command, str) or not command.strip():
            continue
        if any(marker in command for marker in _SELF_MARKERS):
            continue
        return line
    return None


def _project_dir(data: Mapping) -> Optional[str]:
    ws = data.get("workspace")
    if isinstance(ws, Mapping):
        for key in ("project_dir", "current_dir"):
            if isinstance(ws.get(key), str):
                return ws[key]
    cwd = data.get("cwd")
    return cwd if isinstance(cwd, str) else None


def sample_of(data: Mapping, env: Mapping, now: float) -> dict:
    """The compact record kept for the engine."""
    model = data.get("model")
    cost = data.get("cost")
    sample = {
        "t": round(now, 3),
        "sid": env.get("OVERCODE_SESSION_ID") or None,
        "csid": data.get("session_id"),
        "model": model.get("id") if isinstance(model, Mapping) else model,
        "rl": data.get("rate_limits"),
        "cost": cost.get("total_cost_usd") if isinstance(cost, Mapping) else None,
    }
    for key, value in data.items():
        if "cache" in key and isinstance(value, (Mapping, list)):
            sample.setdefault("pc", {})[key] = value
    return sample


def _sample_path(sample: Mapping) -> Path:
    from . import paths
    key = sample.get("sid") or sample.get("csid") or "unknown"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))[:80]
    return paths.usage_dir() / "statusline" / f"{safe}.jsonl"


def _last_line(path: Path) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            f.seek(max(0, size - 16384))
            lines = f.read().splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            return row
    return None


def record(data: Mapping, env: Mapping, now: Optional[float] = None) -> Optional[Path]:
    """Append the sample when it says something new; the file written, else None."""
    sample = sample_of(data, env, time.time() if now is None else now)
    if not sample.get("rl") and sample.get("cost") is None:
        return None
    path = _sample_path(sample)
    last = _last_line(path)
    news = ("rl", "cost", "pc", "model")
    if last is not None and all(last.get(k) == sample.get(k) for k in news):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            os.replace(path, path.with_suffix(".jsonl.1"))
    except OSError:
        pass
    with open(path, "a") as f:
        f.write(json.dumps(sample, separators=(",", ":"), sort_keys=True) + "\n")
    return path


def chain(raw: str, data: Mapping) -> str:
    """The user's own statusline's output for ``raw`` ('' without one or on failure)."""
    cwd = _project_dir(data)
    line = find_user_statusline(cwd)
    if line is None:
        return ""
    try:
        done = subprocess.run(
            line["command"], shell=True, input=raw, capture_output=True, text=True,
            timeout=CHAIN_TIMEOUT_SECONDS,
            cwd=cwd if cwd and os.path.isdir(cwd) else None,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout or ""


def main(stdin: Optional[IO[str]] = None, stdout: Optional[IO[str]] = None,
         env: Optional[Mapping] = None) -> int:
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    env = env if env is not None else os.environ
    try:
        raw = stdin.read()
    except (OSError, ValueError):
        return 0
    try:
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}
    try:
        record(data, env)
    except Exception:  # never break the user's statusline
        pass
    try:
        out = chain(raw, data)
        if out:
            stdout.write(out)
            stdout.flush()
    except Exception:
        pass
    return 0


if __name__ == "__main__":  # python -m overcode.statusline_tap
    sys.exit(main())
