"""
Live smoke tier: overcode driving the REAL agent CLIs on this host.

Everything else in the suite runs against mocks whose chrome was copied
from a captured build of each CLI. That proves overcode's *grammar* for a
backend, not that today's installed binary still speaks it — and the two
opencode2 bugs found in Sep 2026 (wrong process name under the curl
installer, a doubled binary prefix in the version string) were properties
of the real install that no mock could have surfaced.

This file is the host-side counterpart of the containerized
``tests/container/real_llm`` tier (real Claude Code in docker, nightly):
opt-in, cost-capped by construction (two short prompts per backend on the
cheapest model), outcome-based assertions, never selected by default.

Run it by naming the backends whose real CLI + credentials you have::

    OVERCODE_LIVE_BACKENDS=opencode,codex pytest tests/e2e/test_live_backends.py -m e2e
    OVERCODE_LIVE_BACKENDS=all           pytest tests/e2e/test_live_backends.py -m e2e

Per-backend knobs (all optional):

    OVERCODE_LIVE_<BACKEND>_COMMAND   binary to use instead of the one on PATH
                                      (BACKEND upper-cased, ``-`` → ``_``)
    OVERCODE_LIVE_<BACKEND>_MODEL     model override (defaults below)

What each backend's pass proves, in order:

1. ``overcode launch -B <backend>`` accepts the real binary (pre-flight
   version probe, argv grammar) and the pane shows that backend's chrome.
2. One cheap prompt round-trips: the telemetry side (plugin / hooks file /
   argv hooks) writes ``hook_state_<agent>.json`` and the turn settles to
   ``Stop`` — hook-grade status is wired, not just pane polling.
3. A tool call that must ask for permission surfaces as ``PermissionRequest``
   and the backend-resolved ``overcode send <name> approve`` gesture lands
   on the real dialog — the tool runs and the turn settles again.
4. The backend's stats reader finds the session in the real store
   (tokens > 0) using the ids the telemetry recorded.
5. ``overcode doctor`` reports the agent ``ok`` (process found under the
   pane, telemetry footprint present) and can determine the CLI version.

Prerequisites per backend are checked up front and reported as a skip
reason, never a failure: binary on PATH (or the *_COMMAND override) and a
credential signal (an API key in the environment, or the CLI's own auth
store). hermes's permission step needs ``approvals.mode: manual`` in its
config — the default ``smart`` mode pre-screens with an auxiliary model.
On a host without it every other step still runs and the test then ends
*xfailed* rather than passed, so the gap stays visible in the ``-rsx``
summary that ``make test-live`` prints.

Footprints a live run leaves behind, exactly as a real launch would: the
opencode/opencode2 telemetry plugin under the temp project's
``.opencode/plugins/``; grok's global ``~/.grok/hooks/overcode.json``;
hermes's plugin under ``~/.hermes/plugins/overcode/``. The first two are
marker-tagged and refreshed on every launch anyway; ``overcode hooks
uninstall-backend`` removes each.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pytest

from overcode.backends import get_backend
from overcode.launcher import AgentLauncher
from overcode.status_detector_factory import StatusDetectorDispatcher

from conftest import TEST_TMUX_SOCKET, get_tmux_pane_content

pytestmark = pytest.mark.live

# Real turns on a cheap model take 5–40 s; a tool call with a dialog
# round-trip longer. Generous, because a timeout here is a *failure*.
TURN_TIMEOUT = 120.0
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _env_key(backend: str, suffix: str) -> str:
    return f"OVERCODE_LIVE_{backend.upper().replace('-', '_')}_{suffix}"


def _has_env(*names: str) -> bool:
    return any(os.environ.get(n) for n in names)


def _hermes_manual_approvals() -> bool:
    """True when hermes's config forces the approval dialog (approvals.mode: manual)."""
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    try:
        text = (home / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return False
    in_approvals = False
    for line in text.splitlines():
        if not line.startswith(" ") and line.strip():
            in_approvals = line.strip().startswith("approvals:")
            continue
        if in_approvals and line.strip().startswith("mode:"):
            return line.split(":", 1)[1].strip().strip("'\"") == "manual"
    return False


@dataclass(frozen=True)
class LiveSpec:
    backend: str
    default_model: Optional[str]
    # Credential signal: env var names, or a file the CLI's auth leaves.
    credential_env: Tuple[str, ...]
    credential_file: Optional[Callable[[], Path]]
    # Files to drop into the temp project before launch (path -> content),
    # given the resolved model — how opencode/opencode2 get their model and
    # their "ask before shell" rule.
    project_files: Callable[[Optional[str]], Dict[str, str]]
    # Extra `--backend-arg` values for the launch.
    backend_args: Tuple[str, ...]
    # A prompt that answers with plain text and no tool call.
    simple_prompt: str
    # A prompt whose tool call must raise the permission dialog, and a
    # predicate over the temp project dir that only becomes true once the
    # approved tool has actually run. (A pane substring is no proof here:
    # every real CLI echoes the prompt, so any word from it is on screen
    # before the tool runs.)
    permission_prompt: str
    approved_effect: Callable[[Path], bool]
    # An extra credential check for CLIs whose auth lives in no file/env
    # (claude-code on macOS keeps its OAuth login in the Keychain).
    credential_probe: Optional[Callable[[], bool]] = None
    # Whether the permission step can run on this host right now.
    permission_available: Callable[[], bool] = lambda: True
    # Whether to assert the stats reader finds tokens (claude's reader is
    # exercised by the container tier; its ids do not ride hook state).
    expect_stats: bool = True
    # Substrings of a fresh pane (from the corpus) — proves the chrome.
    idle_markers: Tuple[str, ...] = ()
    extra_env: Dict[str, str] = field(default_factory=dict)


def _opencode_v1_files(model: Optional[str]) -> Dict[str, str]:
    cfg = {"$schema": "https://opencode.ai/config.json",
           "permission": {"bash": "ask"}}
    if model:
        cfg["model"] = model
    return {"opencode.json": json.dumps(cfg, indent=2)}


def _opencode_v2_files(model: Optional[str]) -> Dict[str, str]:
    # v2's grammar: the key is `permissions` (plural) holding
    # {action, resource, effect} rules — a v1-style `permission` key is
    # silently skipped and the dialog never appears (verified live).
    cfg = {"$schema": "https://opencode.ai/config.json",
           "permissions": [{"action": "shell", "resource": "*", "effect": "ask"}]}
    if model:
        cfg["model"] = model
    return {"opencode.json": json.dumps(cfg, indent=2)}


def _no_files(_model: Optional[str]) -> Dict[str, str]:
    return {}


SPECS: List[LiveSpec] = [
    LiveSpec(
        backend="claude-code",
        default_model="haiku",
        credential_env=("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"),
        # macOS keeps the OAuth login in the Keychain, not a file, so ask
        # the CLI itself (`claude auth status` prints {"loggedIn": true}).
        credential_file=None,
        credential_probe=lambda: '"loggedIn": true' in subprocess.run(
            ["claude", "auth", "status"], capture_output=True, text=True, timeout=20
        ).stdout,
        project_files=_no_files,
        backend_args=(),
        simple_prompt="Reply with the single word pong and nothing else.",
        # `echo` runs without asking on Claude Code 2.1 (built-in safe-command
        # classification, verified live with no allow rules) — a file write
        # is what still prompts in manual mode.
        permission_prompt="Use the Bash tool to run exactly this command and then say done: touch live_probe.txt && ls live_probe.txt",
        approved_effect=lambda d: (d / "live_probe.txt").exists(),
        expect_stats=False,
        idle_markers=("Claude Code",),
    ),
    LiveSpec(
        backend="opencode",
        default_model="openai/gpt-4o-mini",
        credential_env=("OPENAI_API_KEY",),
        credential_file=lambda: Path.home() / ".local" / "share" / "opencode" / "auth.json",
        project_files=_opencode_v1_files,
        backend_args=(),
        simple_prompt="Reply with the single word pong and nothing else.",
        permission_prompt="Use the bash tool to run exactly this command and report its output: touch live_probe.txt && echo live-ok",
        approved_effect=lambda d: (d / "live_probe.txt").exists(),
        idle_markers=("┃", "ctrl+p commands"),
    ),
    LiveSpec(
        backend="opencode2",
        default_model="openai/gpt-4o-mini",
        credential_env=("OPENAI_API_KEY",),
        credential_file=lambda: Path.home() / ".local" / "share" / "opencode" / "auth.json",
        project_files=_opencode_v2_files,
        backend_args=(),
        simple_prompt="Reply with the single word pong and nothing else.",
        permission_prompt="Use the shell tool to run exactly this command and report its output: touch live_probe.txt && echo live-ok",
        approved_effect=lambda d: (d / "live_probe.txt").exists(),
        idle_markers=("┃", "ctrl+p commands"),
    ),
    LiveSpec(
        backend="codex",
        default_model=None,  # the account default; a wrong id fails the launch
        credential_env=("OPENAI_API_KEY", "CODEX_API_KEY"),
        credential_file=lambda: Path.home() / ".codex" / "auth.json",
        project_files=_no_files,
        # Under codex's default on-request policy an in-workspace `echo`
        # never asks (corpus README) — and codex 0.153 dropped the
        # `untrusted` approval policy (only on-request / never remain) —
        # so the trigger is the corpus's own: a write OUTSIDE the sandboxed
        # workspace, which escalates to the approval dialog. The file is
        # removed by the fixture's teardown.
        backend_args=(),
        simple_prompt="Reply with the single word pong and nothing else.",
        permission_prompt="Run exactly this shell command and then say done: touch ~/overcode_live_probe.txt",
        approved_effect=lambda _d: (Path.home() / "overcode_live_probe.txt").exists(),
        idle_markers=("Codex",),
    ),
    LiveSpec(
        backend="grok",
        default_model=None,
        credential_env=(),
        credential_file=lambda: Path.home() / ".grok" / "auth.json",
        project_files=_no_files,
        backend_args=(),
        simple_prompt="Reply with the single word pong and nothing else.",
        # Normal mode is `--permission-mode default`, which asks for shell
        # commands (corpus: permission_required.txt).
        permission_prompt="Run the command: touch live_probe.txt && echo live-ok",
        approved_effect=lambda d: (d / "live_probe.txt").exists(),
        idle_markers=("Grok",),
    ),
    LiveSpec(
        backend="hermes",
        default_model=None,  # hermes has no launch-time model flag
        credential_env=("OPENAI_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"),
        credential_file=lambda: Path.home() / ".hermes" / ".env",
        project_files=_no_files,
        backend_args=(),
        simple_prompt="Reply with the single word pong and nothing else.",
        # hermes only boxes *dangerous* commands; a recursive delete of a
        # directory we created is the corpus's own trigger.
        permission_prompt="Delete the directory ./junkdir_a using rm -rf, then say done.",
        approved_effect=lambda d: not (d / "junkdir_a").exists(),
        permission_available=_hermes_manual_approvals,
        idle_markers=("Hermes Agent",),
        # hermes refuses to index a session into the real state.db when it
        # detects a pytest *ancestor* process (its live-system guard —
        # stripping PYTEST_* from the env is not enough), and the stats
        # reader then finds nothing. This is hermes's own documented escape
        # hatch for spawned children; this tier exists to touch the real
        # store, so it is the right thing to set here and nowhere else.
        extra_env={"HERMES_STATE_DB_GUARD_BYPASS": "1"},
    ),
]
_BY_NAME = {s.backend: s for s in SPECS}


def _requested_backends() -> List[str]:
    raw = os.environ.get("OVERCODE_LIVE_BACKENDS", "").strip()
    if not raw:
        return []
    if raw.lower() == "all":
        return [s.backend for s in SPECS]
    return [b.strip() for b in raw.split(",") if b.strip()]


def _prerequisite_problem(spec: LiveSpec) -> Optional[str]:
    """Why this backend cannot run live here, or None when it can."""
    if spec.backend not in _requested_backends():
        return f"{spec.backend} not in OVERCODE_LIVE_BACKENDS"
    binary = os.environ.get(_env_key(spec.backend, "COMMAND")) or get_backend(spec.backend).binary
    if not shutil.which(binary):
        return f"{binary} not on PATH (set {_env_key(spec.backend, 'COMMAND')})"
    has_cred = _has_env(*spec.credential_env) or (
        spec.credential_file is not None and spec.credential_file().exists()
    )
    if not has_cred and spec.credential_probe is not None:
        try:
            has_cred = bool(spec.credential_probe())
        except (OSError, subprocess.SubprocessError):
            has_cred = False
    if not has_cred:
        return f"no credentials for {spec.backend} ({', '.join(spec.credential_env) or 'CLI auth store'})"
    return None


@pytest.fixture(params=SPECS, ids=[s.backend for s in SPECS])
def spec(request) -> LiveSpec:
    problem = _prerequisite_problem(request.param)
    if problem:
        pytest.skip(problem)
    return request.param


@pytest.fixture
def live_env(spec):
    """An isolated overcode state dir + tmux socket around the REAL CLI.

    Mirrors ``clean_test_env`` but leaves every ``<BACKEND>_COMMAND`` at
    the real binary (or the OVERCODE_LIVE_*_COMMAND override) and keeps the
    CLI's own home directories (auth lives there). The host agent's
    identity is stripped so the launcher's auto-parent detection stays out.
    """
    session = f"live-{spec.backend}-{os.getpid()}"
    state_dir = tempfile.mkdtemp(prefix=f"overcode-live-{spec.backend}-")
    subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", session],
                   capture_output=True)

    env = os.environ.copy()
    for key in list(env):
        if key.endswith("_COMMAND") and key.split("_COMMAND")[0] in (
            "CLAUDE", "OPENCODE", "OPENCODE2", "CODEX", "GROK", "HERMES",
        ):
            env.pop(key)
    override = os.environ.get(_env_key(spec.backend, "COMMAND"))
    if override:
        env[_command_env_var(spec.backend)] = override
        # Doctor's version probe deliberately ignores the *_COMMAND override
        # (it asks what is installed, not what a test substituted), so the
        # override has to be discoverable on PATH under the real binary name.
        # No resolve(): an npm `.bin/opencode2` is a symlink to
        # `…/cli/bin/opencode2.exe`, and it is the link's name that matters.
        env["PATH"] = str(Path(override).absolute().parent) + os.pathsep + env.get("PATH", "")
    env["OVERCODE_STATE_DIR"] = state_dir
    env["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET
    env["PYTHONPATH"] = str(_PROJECT_ROOT / "src")
    for key in [
        "OVERCODE_SESSION_NAME", "OVERCODE_SESSION_ID", "OVERCODE_TMUX_SESSION",
        "OVERCODE_PARENT_SESSION_ID", "OVERCODE_PARENT_NAME",
        "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
        # pytest's own markers: the real CLI must look like a user launch
        # (hermes additionally checks process ancestry — see its spec).
        "PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS",
    ]:
        env.pop(key, None)
    env.update(spec.extra_env)

    # The in-process detector/launcher helpers read these from os.environ.
    saved = {k: os.environ.get(k) for k in ("OVERCODE_STATE_DIR", "OVERCODE_TMUX_SOCKET")}
    os.environ["OVERCODE_STATE_DIR"] = state_dir
    os.environ["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET

    workdir = Path(tempfile.mkdtemp(prefix=f"overcode-live-{spec.backend}-proj-"))
    subprocess.run(["git", "init", "-q", str(workdir)], capture_output=True)
    (workdir / "junkdir_a").mkdir()
    (workdir / "junkdir_a" / "x.txt").write_text("x\n")
    model = os.environ.get(_env_key(spec.backend, "MODEL"), spec.default_model)
    for rel, content in spec.project_files(model).items():
        (workdir / rel).write_text(content, encoding="utf-8")

    # The monitor daemon is what turns hook-state files into the fleet's
    # hooks-mode status (waiting_approval etc.) — the in-process dispatcher
    # alone answers with pane polling. Run it like a real deployment would.
    daemon = subprocess.Popen(
        ["python", "-m", "overcode.cli", "monitor-daemon", "start",
         "--interval", "2", "--session", session],
        env=env, cwd=_PROJECT_ROOT,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        yield {"session": session, "state_dir": state_dir, "env": env,
               "workdir": workdir, "model": model}
    finally:
        subprocess.run(["python", "-m", "overcode.cli", "kill", f"live-{spec.backend}"[:20],
                        "--session", session], env=env, capture_output=True,
                       cwd=_PROJECT_ROOT, timeout=60)
        subprocess.run(["python", "-m", "overcode.cli", "monitor-daemon", "stop",
                        "--session", session], env=env, capture_output=True,
                       cwd=_PROJECT_ROOT, timeout=60)
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
        subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", session],
                       capture_output=True)
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(workdir, ignore_errors=True)
        (Path.home() / "overcode_live_probe.txt").unlink(missing_ok=True)
        # Keep the state dir for a failed run's post-mortem (it is in TMPDIR).


def _command_env_var(backend: str) -> str:
    return f"{backend.upper().replace('-', '_').replace('CLAUDE_CODE', 'CLAUDE')}_COMMAND"


class TestLiveBackend:
    """One real agent per backend, exercised through overcode's own CLI."""

    @pytest.fixture(autouse=True)
    def setup(self, spec, live_env):
        self.spec = spec
        self.live = live_env
        self.session = live_env["session"]
        self.env = live_env["env"]
        self.name = f"live-{spec.backend}"[:20]

    # -- helpers ---------------------------------------------------------

    def _cli(self, *args, timeout: int = 60, extra_env: Optional[Dict[str, str]] = None):
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(
            ["python", "-m", "overcode.cli", *args, "--session", self.session],
            capture_output=True, text=True, timeout=timeout, env=env,
            cwd=_PROJECT_ROOT,
        )

    def _window(self) -> str:
        out = subprocess.run(
            ["tmux", "-L", TEST_TMUX_SOCKET, "list-windows", "-t", self.session,
             "-F", "#{window_name}"], capture_output=True, text=True,
        ).stdout
        for w in out.splitlines():
            if w.startswith(f"{self.name}-"):
                return w
        raise AssertionError(f"no window for {self.name!r}: {out!r}")

    def _pane(self) -> str:
        return get_tmux_pane_content(TEST_TMUX_SOCKET, self.session, self._window(), lines=80)

    def _hook_state_path(self) -> Path:
        return Path(self.live["state_dir"]) / self.session / f"hook_state_{self.name}.json"

    def _hook_state(self) -> Optional[dict]:
        try:
            return json.loads(self._hook_state_path().read_text())
        except (OSError, ValueError):
            return None

    def _wait_hook_event(self, *events: str, after: float, timeout: float = TURN_TIMEOUT) -> dict:
        """Wait for hook_state to carry one of ``events`` newer than ``after``."""
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            state = self._hook_state()
            if state and state.get("event") in events and float(state.get("timestamp", 0)) > after:
                return state
            last = state
            time.sleep(0.5)
        raise AssertionError(
            f"hook state never showed {events!r} after {after}; last={last}; pane:\n{self._pane()}"
        )

    def _wait_pane(self, *markers: str, timeout: float = TURN_TIMEOUT) -> str:
        deadline = time.monotonic() + timeout
        content = ""
        while time.monotonic() < deadline:
            try:
                content = self._pane()
            except AssertionError:
                time.sleep(0.5)
                continue
            if all(m in content for m in markers):
                return content
            time.sleep(0.5)
        raise AssertionError(f"pane never showed {markers!r}:\n{content}")

    def _session(self):
        sessions = AgentLauncher(self.session).list_sessions(
            detect_terminated=False, kill_untracked=False
        )
        return next((s for s in sessions if s.name == self.name), None)

    def _status(self) -> Tuple[str, str]:
        """The pane-polling verdict: the backend's patterns vs the REAL chrome."""
        session = self._session()
        assert session is not None, f"{self.name} is not in list_sessions()"
        detector = StatusDetectorDispatcher(self.session)
        status, activity, _ = detector.detect_status(session)
        return status, activity

    def _wait_daemon_status(self, expected: str, timeout: float = 30.0) -> str:
        """The daemon's verdict (hooks mode once hook state is fresh).

        Read from the daemon-written ``stats.current_state`` in the session
        store — the same field the TUI's status column renders.
        """
        store = Path(self.live["state_dir"]) / "sessions" / "sessions.json"
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                rows = json.loads(store.read_text()).values()
            except (OSError, ValueError, AttributeError):
                rows = []
            for row in rows:
                if isinstance(row, dict) and row.get("name") == self.name:
                    last = (row.get("stats") or {}).get("current_state")
                    if last == expected:
                        return last
            time.sleep(1)
        raise AssertionError(f"daemon never reported {expected!r} for {self.name}; last={last!r}")

    # -- the one long test ---------------------------------------------

    def test_real_cli_round_trip(self):
        spec = self.spec
        model = self.live["model"]

        # 1. Launch the real CLI through overcode, with an initial prompt.
        # `--prompt` is what routes the launch through the launcher's
        # wait-for-prompt path, the only place a backend's startup dialogs
        # (codex's and Claude's "Do you trust this directory?" in a fresh
        # temp dir) get dismissed — so this exercises that seam too.
        t0 = time.time()
        cmd = ["launch", "--name", self.name, "--backend", spec.backend,
               "--directory", str(self.live["workdir"]),
               "--prompt", spec.simple_prompt]
        if model and spec.backend not in ("opencode", "opencode2"):
            cmd += ["--model", model]
        for arg in spec.backend_args:
            cmd += ["--backend-arg", arg]
        result = self._cli(*cmd, timeout=120)
        assert result.returncode == 0 and "Cannot launch" not in result.stdout, (
            f"launch failed:\n{result.stdout}\n{result.stderr}"
        )
        subprocess.run(["tmux", "-L", TEST_TMUX_SOCKET, "resize-window",
                        "-t", f"{self.session}:{self._window()}", "-x", "160", "-y", "50"],
                       capture_output=True)
        self._wait_pane(*spec.idle_markers, timeout=60)

        # 2. That first turn: telemetry must write hook state and settle.
        self._wait_hook_event("Stop", after=t0)
        status, activity = self._status()
        assert status == "waiting_user", (status, activity)
        assert self._wait_daemon_status("waiting_user") == "waiting_user"

        # 4a. The stats reader finds the real session by the recorded ids.
        if spec.expect_stats:
            state = self._hook_state() or {}
            session = self._session()
            ids = state.get("agent_session_ids") or ([state["agent_session_id"]] if state.get("agent_session_id") else [])
            assert ids, f"telemetry recorded no agent session id: {state}"
            session.agent_session_ids = ids
            session.active_agent_session_id = ids[-1]
            reader = get_backend(spec.backend).make_stats_reader()
            stats = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not (stats and stats.input_tokens > 0):
                stats = reader.get_stats(session)
                time.sleep(1)
            assert stats is not None and stats.input_tokens > 0, f"stats reader found nothing for {ids}: {stats}"

        # 5 (early, so an xfail below cannot mask it). Doctor sees a healthy, identifiable agent.
        # rich sizes its table to 80 columns on a captured stdout and wraps
        # cells, which can split "✓ ok" across rows; give it room instead.
        result = self._cli("doctor", timeout=120, extra_env={"COLUMNS": "200"})
        out = " ".join(result.stdout.split())
        assert "no process" not in out, out
        assert "could not determine the installed" not in out, out
        assert f"{self.name}" in out and "✓ ok" in out, out

        # 3. A tool call that must ask; the approve gesture lands on it.
        if spec.permission_available():
            t1 = time.time()
            result = self._cli("send", self.name, spec.permission_prompt)
            assert result.returncode == 0, result.stderr
            state = self._wait_hook_event("PermissionRequest", after=t1)
            # Two independent verdicts on the same real dialog: the polling
            # patterns must recognise today's chrome as a permission prompt,
            # and the daemon (hooks mode) must report waiting_approval.
            status, activity = self._status()
            assert status == "waiting_user" and activity.startswith("Permission:"), (status, activity)
            assert self._wait_daemon_status("waiting_approval") == "waiting_approval"

            result = self._cli("send", self.name, "approve")
            assert result.returncode == 0, result.stderr
            self._wait_hook_event("Stop", after=state["timestamp"])
            workdir = self.live["workdir"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not spec.approved_effect(workdir):
                time.sleep(0.5)
            assert spec.approved_effect(workdir), (
                f"approved tool call left no trace under {workdir}:\n{self._pane()}"
            )
            assert self._status()[0] == "waiting_user"
        else:
            pytest.xfail(f"{spec.backend}: permission dialog not forceable on this host "
                         "(hermes needs approvals.mode: manual)")



if __name__ == "__main__":
    pytest.main([__file__, "-v"])
