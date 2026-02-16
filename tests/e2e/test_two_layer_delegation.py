"""
E2E Test: Two-Layer Agent Delegation

Tests the full 2-layer delegation pipeline: controller → 3 sub-agents → 6 sub-sub-agents
(10 agents total). Validates that data flows through the legitimate `overcode report` →
`overcode follow` pipeline, not through file writes or other shortcuts.

Agent hierarchy:
    controller
    ├── animals
    │   ├── animals-english
    │   └── animals-spanish
    ├── food
    │   ├── food-french
    │   └── food-german
    └── tech
        ├── tech-japanese
        └── tech-italian

Requires:
- Real Claude CLI (`claude` binary)
- tmux available

Run with:
    uv run pytest tests/e2e/test_two_layer_delegation.py -v -s --timeout=960
"""

import glob as glob_mod
import json
import os
import shutil
import subprocess
import tempfile
import time

import pytest

TEST_TMUX_SOCKET = "overcode-test"

pytestmark = [
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude CLI not available",
    ),
    pytest.mark.skipif(
        shutil.which("tmux") is None,
        reason="tmux not available",
    ),
]


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def test_session_name() -> str:
    return "agents"


@pytest.fixture
def real_claude_env(test_session_name):
    """Set up a clean test environment using the real Claude binary.

    Identical to test_child_delegation.py — isolated tmux socket, temp state
    dir, env var stripping, cleanup.
    """
    state_dir = tempfile.mkdtemp(prefix="overcode-2layer-test-")

    subprocess.run(
        ["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", test_session_name],
        capture_output=True,
    )

    env = os.environ.copy()
    env["OVERCODE_STATE_DIR"] = state_dir
    env["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET

    for key in [
        "OVERCODE_SESSION_NAME",
        "OVERCODE_TMUX_SESSION",
        "OVERCODE_PARENT_SESSION_ID",
        "OVERCODE_PARENT_NAME",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
    ]:
        env.pop(key, None)

    orig_state_dir = os.environ.get("OVERCODE_STATE_DIR")
    orig_tmux_socket = os.environ.get("OVERCODE_TMUX_SOCKET")
    orig_claudecode = os.environ.get("CLAUDECODE")
    orig_entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT")
    os.environ["OVERCODE_STATE_DIR"] = state_dir
    os.environ["OVERCODE_TMUX_SOCKET"] = TEST_TMUX_SOCKET
    os.environ.pop("CLAUDECODE", None)
    os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)

    try:
        yield {
            "session_name": test_session_name,
            "state_dir": state_dir,
            "env": env,
            "tmux_socket": TEST_TMUX_SOCKET,
        }
    finally:
        try:
            sessions_file = os.path.join(state_dir, "sessions", "sessions.json")
            if os.path.exists(sessions_file):
                with open(sessions_file) as f:
                    sessions = json.load(f)
                for sess in sessions.values():
                    name = sess.get("name", "")
                    if name:
                        subprocess.run(
                            [
                                "python", "-m", "overcode.cli", "kill", name,
                                "--session", test_session_name,
                            ],
                            capture_output=True,
                            timeout=10,
                            env=env,
                        )
        except Exception:
            pass

        subprocess.run(
            ["tmux", "-L", TEST_TMUX_SOCKET, "kill-session", "-t", test_session_name],
            capture_output=True,
        )

        if orig_state_dir is None:
            os.environ.pop("OVERCODE_STATE_DIR", None)
        else:
            os.environ["OVERCODE_STATE_DIR"] = orig_state_dir
        if orig_tmux_socket is None:
            os.environ.pop("OVERCODE_TMUX_SOCKET", None)
        else:
            os.environ["OVERCODE_TMUX_SOCKET"] = orig_tmux_socket
        if orig_claudecode is not None:
            os.environ["CLAUDECODE"] = orig_claudecode
        if orig_entrypoint is not None:
            os.environ["CLAUDE_CODE_ENTRYPOINT"] = orig_entrypoint

        shutil.rmtree(state_dir, ignore_errors=True)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _read_sessions(state_dir: str) -> dict:
    """Read sessions.json, returning {} on any error."""
    sessions_file = os.path.join(state_dir, "sessions", "sessions.json")
    try:
        with open(sessions_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _capture_pane(socket: str, session: str, window: int, lines: int = 50) -> str:
    """Capture tmux pane content for a window."""
    result = subprocess.run(
        [
            "tmux", "-L", socket,
            "capture-pane", "-t", f"{session}:{window}",
            "-p", "-S", f"-{lines}",
        ],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _last_n_lines(text: str, n: int = 3) -> str:
    """Return last n non-empty lines of text."""
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-n:])


def _verify_hierarchy(state_dir: str) -> dict:
    """Validate parent_session_id chains in sessions.json.

    Returns dict with:
        valid: bool
        root: str (controller name)
        subs: list of sub-agent names
        sub_subs: list of sub-sub-agent names
        errors: list of error strings
    """
    sessions = _read_sessions(state_dir)
    errors = []

    by_id = {sid: s for sid, s in sessions.items()}

    # Find root (no parent)
    roots = [
        (sid, s)
        for sid, s in sessions.items()
        if not s.get("parent_session_id")
    ]
    if len(roots) != 1:
        names = [s.get("name") for _, s in roots]
        errors.append(f"Expected 1 root agent, found {len(roots)}: {names}")
        return {
            "valid": False, "root": None,
            "subs": [], "sub_subs": [], "errors": errors,
        }

    root_id, root = roots[0]
    root_name = root.get("name", "?")

    # Find subs (parent = root)
    subs = [
        (sid, s)
        for sid, s in sessions.items()
        if s.get("parent_session_id") == root_id
    ]
    sub_names = [s.get("name", "?") for _, s in subs]
    sub_ids = {sid for sid, _ in subs}

    # Find sub-subs (parent = one of the subs)
    sub_subs = [
        (sid, s)
        for sid, s in sessions.items()
        if s.get("parent_session_id") in sub_ids
    ]
    sub_sub_names = [s.get("name", "?") for _, s in sub_subs]

    # Validate expected structure
    if len(subs) < 3:
        errors.append(f"Expected ≥3 sub-agents, found {len(subs)}: {sub_names}")
    if len(sub_subs) < 4:
        errors.append(
            f"Expected ≥4 sub-sub-agents (of 6), found {len(sub_subs)}: {sub_sub_names}"
        )

    # Verify no orphans
    for sid, s in sessions.items():
        parent_id = s.get("parent_session_id")
        if parent_id and parent_id not in by_id:
            errors.append(
                f"Agent {s.get('name', '?')} has invalid parent_session_id {parent_id}"
            )

    return {
        "valid": len(errors) == 0,
        "root": root_name,
        "subs": sub_names,
        "sub_subs": sub_sub_names,
        "errors": errors,
    }


def _find_report_files(state_dir: str) -> dict:
    """Find all report_*.json files in the state dir and parse them.

    Returns dict: agent_name → parsed report data (or error info).
    """
    reports = {}
    pattern = os.path.join(state_dir, "**", "report_*.json")
    for path in glob_mod.glob(pattern, recursive=True):
        filename = os.path.basename(path)
        if filename.startswith("report_") and filename.endswith(".json"):
            agent_name = filename[len("report_"):-len(".json")]
            try:
                with open(path) as f:
                    data = json.load(f)
                reports[agent_name] = data
            except (json.JSONDecodeError, IOError) as exc:
                reports[agent_name] = {"error": str(exc)}
    return reports


def _check_for_cheating_files(project_root: str) -> list:
    """Scan project root for suspicious data-sync files agents might create."""
    suspicious_patterns = [
        "jokes*.md", "jokes*.txt", "jokes*.json",
        "results*.json", "results*.txt", "results*.md",
        "summary*.txt", "summary*.md", "summary*.json",
        "output*.txt", "output*.md", "output*.json",
        "data_sync*", "agent_data*",
    ]
    found = []
    for pat in suspicious_patterns:
        for path in glob_mod.glob(os.path.join(project_root, pat)):
            if "overcode-2layer-test" not in path and "node_modules" not in path:
                found.append(path)
        for path in glob_mod.glob(os.path.join(project_root, "**", pat), recursive=True):
            if "overcode-2layer-test" not in path and "node_modules" not in path:
                found.append(path)
    return list(set(found))


def _judge_outputs(
    all_outputs: dict[str, str],
    reports: dict,
    hierarchy_result: dict,
) -> dict:
    """Use Claude CLI as judge to evaluate the 2-layer delegation.

    Receives pane captures, report file contents, and hierarchy verification.
    """
    # Truncate each pane output to ~150 lines
    truncated = {}
    for name, output in all_outputs.items():
        lines = output.splitlines()
        if len(lines) > 150:
            truncated[name] = "\n".join(
                lines[:75] + ["... (truncated) ..."] + lines[-75:]
            )
        else:
            truncated[name] = output

    captures_text = ""
    for name, output in truncated.items():
        captures_text += f"\n\n=== Agent: {name} ===\n{output}"

    # Format report file contents (truncated to 500 chars per reason)
    reports_text = ""
    for name, data in reports.items():
        if isinstance(data, dict) and "error" not in data:
            reason = str(data.get("reason", ""))[:500]
            status = data.get("status", "unknown")
            preview = reason[:200] + ("...(truncated)" if len(reason) > 200 else "")
            reports_text += f"\n  {name}: status={status}, reason={preview}"
        else:
            reports_text += f"\n  {name}: ERROR — {data}"

    hierarchy_text = json.dumps(hierarchy_result, indent=2)

    prompt = f"""You are evaluating an automated test of a 2-layer AI agent delegation system called "overcode".

A controller agent was given instructions to:
1. Use the /delegating-to-agents skill
2. Launch 3 sub-agents (animals, food, tech) — each handles a joke theme
3. Each sub-agent launches 2 sub-sub-agents for different languages
4. Sub-sub-agents produce 2 jokes each in their assigned language with 3s sleep between
5. Data flows ONLY through `overcode report` (no file writes for data sync)
6. Sub-subs report to their sub-agent parent, subs report to controller

Expected hierarchy:
    controller
    ├── animals → animals-english, animals-spanish
    ├── food → food-french, food-german
    └── tech → tech-japanese, tech-italian

=== HIERARCHY VERIFICATION (automated) ===
{hierarchy_text}

=== REPORT FILE CONTENTS ===
{reports_text}

=== PANE CAPTURES FROM ALL AGENTS ===
{captures_text}

Evaluate each criterion as true or false. Be generous — agents may use slightly different names or structures, but the core behaviors must be present:

1. skill_activated: Did the controller discover and use the delegating-to-agents skill? (Look for /delegating-to-agents or skill activation messages)
2. three_subs_launched: Did the controller launch ~3 sub-agents using `overcode launch`? (Look for overcode launch commands in controller's pane)
3. two_layer_delegation: Did sub-agents THEMSELVES run `overcode launch` to create sub-sub-agents? (Look for overcode launch in non-controller panes — this is the KEY test: delegation went 2 levels deep, not just 1)
4. correct_languages: Are jokes present in at least 4 of the 6 expected languages (English, Spanish, French, German, Japanese, Italian)? (Look for non-English text or language indicators in panes/reports)
5. jokes_produced: Do actual jokes exist in the outputs? (Not just placeholders like "joke here" — real attempts at humor)
6. report_pipeline_used: Is `overcode report` visible in pane outputs AND do report files contain joke-related content in their reason field?
7. no_file_cheating: Agents did NOT use Write tool or bash file redirects (> / >>) to write joke data to files for cross-agent synchronization. (Small config files or tool usage is fine — look specifically for writing joke/result data to files as a way to pass data between agents)
8. no_hierarchy_bypass: Sub-sub-agent joke content appears in sub-agent reports (data flowed through the middle layer, not directly from sub-subs to controller). Check if sub-agent report reasons contain content that originated from their sub-sub-agents.
9. results_collected: Did the controller produce a final summary containing multi-theme, multi-language jokes? (The controller's pane should show collected results from multiple themes)

Respond with ONLY a JSON object (no markdown fencing):
{{
  "skill_activated": true/false,
  "three_subs_launched": true/false,
  "two_layer_delegation": true/false,
  "correct_languages": true/false,
  "jokes_produced": true/false,
  "report_pipeline_used": true/false,
  "no_file_cheating": true/false,
  "no_hierarchy_bypass": true/false,
  "results_collected": true/false,
  "explanation": "brief explanation of what happened across all layers"
}}"""

    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"Claude judge failed: {result.stderr}"

    text = result.stdout.strip()
    # Parse JSON from response (handle potential markdown fencing)
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


# ── The Test ─────────────────────────────────────────────────────────────────

CONTROLLER_PROMPT = """\
Use the /delegating-to-agents skill to orchestrate a 2-layer joke research project.

You are the CONTROLLER. You must launch exactly 3 sub-agents using `overcode launch` \
(WITHOUT --follow, so they run in parallel):

1. "animals" — jokes about animals
2. "food" — jokes about food
3. "tech" — jokes about technology

CRITICAL: Each sub-agent must ITSELF launch 2 sub-sub-agents (second layer of delegation) \
for different languages:
- animals launches: "animals-english" (English jokes) and "animals-spanish" (Spanish jokes)
- food launches: "food-french" (French jokes) and "food-german" (German jokes)
- tech launches: "tech-japanese" (Japanese jokes) and "tech-italian" (Italian jokes)

Each sub-sub-agent produces exactly 2 jokes in its assigned language, sleeping 3 seconds \
between jokes to simulate research time.

RULES (include these in every sub-agent's prompt, and instruct sub-agents to include \
them in every sub-sub-agent's prompt):
- Data flows ONLY through `overcode report --status success --reason "..."` — NO file \
writes for data sync
- Sub-sub-agents report their jokes to their parent sub-agent via overcode report
- Sub-agents collect their children's reports using `overcode follow <child-name>`, \
then report the combined results to the controller via overcode report
- Each agent calls `overcode report` exactly once when done
- Sub-agents must use `overcode launch` (not Task tool or other mechanisms) to create \
sub-sub-agents
- Sub-agents must launch their sub-sub-agents WITHOUT --follow so they run in parallel, \
then follow each child separately to collect results

After launching all 3 sub-agents, follow each one to collect results, then produce a \
final summary of all jokes organized by theme and language.\
"""

POLL_INTERVAL = 10  # seconds
TIMEOUT = 900  # 15 minutes
NO_CHILD_TIMEOUT = 180  # 3 min
NO_GRANDCHILD_TIMEOUT = 300  # 5 min


class TestTwoLayerDelegation:
    """Test that a controller agent delegates 2 levels deep:
    controller → sub-agents → sub-sub-agents, with data flowing
    through overcode report."""

    def test_two_layer_delegation(self, real_claude_env):
        env = real_claude_env
        session = env["session_name"]
        state_dir = env["state_dir"]
        socket = env["tmux_socket"]
        run_env = env["env"]

        # ── 1. Launch controller agent ───────────────────────────────────
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(__file__))
        )
        launch_result = subprocess.run(
            [
                "python", "-m", "overcode.cli", "launch",
                "--name", "controller",
                "--session", session,
                "--bypass-permissions",
                "--prompt", CONTROLLER_PROMPT,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=run_env,
            cwd=project_root,
        )
        assert launch_result.returncode == 0, (
            f"Failed to launch controller:\nstdout: {launch_result.stdout}\n"
            f"stderr: {launch_result.stderr}"
        )
        print(f"\n[  0s] Controller launched: {launch_result.stdout.strip()}")

        # ── 2. Poll until completion ─────────────────────────────────────
        start = time.time()
        seen_agents: set[str] = set()
        milestones: dict[str, float] = {}
        first_child_time: float | None = None
        first_grandchild_time: float | None = None
        consecutive_all_idle: int = 0

        EXPECTED_SUBS = {"animals", "food", "tech"}
        EXPECTED_SUB_SUBS = {
            "animals-english", "animals-spanish",
            "food-french", "food-german",
            "tech-japanese", "tech-italian",
        }

        while True:
            elapsed = time.time() - start
            if elapsed > TIMEOUT:
                sessions = _read_sessions(state_dir)
                print(f"\n[TIMEOUT] {elapsed:.0f}s elapsed. Final state:")
                for sid, s in sessions.items():
                    print(f"  {s.get('name', '?')}: {s.get('status', '?')}")
                pytest.fail(
                    f"Timed out after {TIMEOUT}s waiting for controller to finish"
                )

            sessions = _read_sessions(state_dir)
            agent_summary = []
            controller_status = None
            controller_terminated = False

            for sid, s in sessions.items():
                name = s.get("name", "?")
                status = s.get("status", "?")
                agent_summary.append(f"{name}={status}")

                if name not in seen_agents:
                    seen_agents.add(name)

                if name == "controller":
                    controller_status = status
                    if status == "terminated":
                        controller_terminated = True

            # Print progress
            summary_str = (
                ", ".join(agent_summary) if agent_summary else "no agents yet"
            )
            print(f"[{elapsed:4.0f}s] {len(sessions)} agents: {summary_str}")

            # Print last lines of each agent's pane (2 lines to keep output compact)
            for sid, s in sessions.items():
                name = s.get("name", "?")
                window = s.get("tmux_window")
                tmux_session = s.get("tmux_session", session)
                if window is not None:
                    snippet = _last_n_lines(
                        _capture_pane(socket, tmux_session, window, lines=20),
                        n=2,
                    )
                    if snippet:
                        for line in snippet.splitlines():
                            print(f"        {name}: {line}")

            # Categorize agents
            children = {
                s.get("name"): s
                for sid, s in sessions.items()
                if s.get("name") != "controller"
            }
            sub_agents = {
                n: s for n, s in children.items() if n in EXPECTED_SUBS
            }
            sub_sub_agents = {
                n: s for n, s in children.items() if n in EXPECTED_SUB_SUBS
            }
            # Also detect agents with hyphenated names as potential sub-subs
            for n, s in children.items():
                if (
                    n not in EXPECTED_SUBS
                    and n != "controller"
                    and n not in sub_sub_agents
                    and "-" in n
                ):
                    sub_sub_agents[n] = s

            # ── Track milestones ─────────────────────────────────────────
            if children and "first_child" not in milestones:
                milestones["first_child"] = elapsed
                first_child_time = elapsed
                first_name = list(children.keys())[0]
                print(
                    f"        ** MILESTONE: first child appeared ({first_name}) **"
                )

            if (
                len(sub_agents) >= 3
                and "all_subs_launched" not in milestones
            ):
                milestones["all_subs_launched"] = elapsed
                print("        ** MILESTONE: all 3 sub-agents launched **")

            if sub_sub_agents and "first_grandchild" not in milestones:
                milestones["first_grandchild"] = elapsed
                first_grandchild_time = elapsed
                first_gc = list(sub_sub_agents.keys())[0]
                print(
                    f"        ** MILESTONE: first grandchild appeared ({first_gc}) **"
                )

            if (
                len(sub_sub_agents) >= 6
                and "all_grandchildren_launched" not in milestones
            ):
                milestones["all_grandchildren_launched"] = elapsed
                print("        ** MILESTONE: all 6 sub-sub-agents launched **")

            for cname, cdata in children.items():
                key = f"{cname}_done"
                if cdata.get("status") == "done" and key not in milestones:
                    milestones[key] = elapsed
                    print(f"        ** MILESTONE: {cname} reached 'done' **")

            # ── Detect completion ────────────────────────────────────────
            controller_finished = controller_status == "done"
            any_agent_active = False

            if not controller_finished and first_child_time is not None:
                all_children_done = children and all(
                    c.get("status") in (
                        "done", "waiting_oversight", "terminated",
                    )
                    for c in children.values()
                )

                for sid, s in sessions.items():
                    window = s.get("tmux_window")
                    tmux_sess = s.get("tmux_session", session)
                    if window is not None:
                        pane = _capture_pane(
                            socket, tmux_sess, window, lines=10
                        )
                        if "esc to interrupt" in pane:
                            any_agent_active = True

                    name = s.get("name", "?")
                    if name == "controller" and window is not None:
                        pane = _capture_pane(
                            socket, tmux_sess, window, lines=10
                        )
                        has_claude_bar = (
                            "bypass permissions" in pane
                            or "shift+tab" in pane
                        )
                        is_processing = "esc to interrupt" in pane
                        if not has_claude_bar and pane.strip():
                            controller_finished = True
                            print(
                                "        ** MILESTONE: controller Claude "
                                "exited (no status bar) **"
                            )
                        elif (
                            all_children_done
                            and has_claude_bar
                            and not is_processing
                        ):
                            controller_finished = True
                            print(
                                "        ** MILESTONE: controller idle at "
                                "prompt, all children done **"
                            )

                # Fallback: all agents idle for 3+ consecutive polls
                if (
                    not controller_finished
                    and not any_agent_active
                    and children
                ):
                    consecutive_all_idle += 1
                    if consecutive_all_idle >= 3:
                        controller_finished = True
                        print(
                            f"        ** MILESTONE: all agents idle for "
                            f"{consecutive_all_idle} polls — assuming "
                            f"complete **"
                        )
                else:
                    consecutive_all_idle = 0

            if controller_finished:
                milestones["all_done"] = elapsed
                print(
                    "        ** MILESTONE: controller finished — "
                    "test complete **"
                )
                break

            # ── Early aborts ─────────────────────────────────────────────
            if controller_terminated:
                for sid, s in sessions.items():
                    if s.get("name") == "controller":
                        output = _capture_pane(
                            socket,
                            s.get("tmux_session", session),
                            s["tmux_window"],
                            lines=50,
                        )
                        print(
                            f"\n[ABORT] Controller terminated. "
                            f"Last output:\n{output}"
                        )
                        break
                pytest.fail("Controller agent terminated unexpectedly")

            if elapsed > NO_CHILD_TIMEOUT and first_child_time is None:
                for sid, s in sessions.items():
                    if s.get("name") == "controller":
                        output = _capture_pane(
                            socket,
                            s.get("tmux_session", session),
                            s["tmux_window"],
                            lines=50,
                        )
                        print(
                            f"\n[ABORT] No children after "
                            f"{NO_CHILD_TIMEOUT}s. Controller "
                            f"output:\n{output}"
                        )
                        break
                pytest.fail(
                    f"No child agents appeared after {NO_CHILD_TIMEOUT}s"
                )

            if (
                elapsed > NO_GRANDCHILD_TIMEOUT
                and first_grandchild_time is None
                and first_child_time is not None
            ):
                print(
                    f"\n[ABORT] No grandchildren after "
                    f"{NO_GRANDCHILD_TIMEOUT}s "
                    f"(sub-agents not delegating)"
                )
                for sid, s in sessions.items():
                    name = s.get("name", "?")
                    window = s.get("tmux_window")
                    if window is not None:
                        output = _capture_pane(
                            socket,
                            s.get("tmux_session", session),
                            window,
                            lines=30,
                        )
                        print(f"  {name}:\n{output}")
                pytest.fail(
                    f"No sub-sub-agents appeared after "
                    f"{NO_GRANDCHILD_TIMEOUT}s — sub-agents are not "
                    f"delegating"
                )

            # Warn if any child terminated
            for cname, cdata in children.items():
                warn_key = f"{cname}_terminated_warned"
                if (
                    cdata.get("status") == "terminated"
                    and warn_key not in milestones
                ):
                    milestones[warn_key] = elapsed
                    print(f"        ** WARNING: {cname} terminated **")

            time.sleep(POLL_INTERVAL)

        # ── 3. Structural verification (Layer 1) ────────────────────────
        print("\n--- Layer 1: Structural verification ---")

        hierarchy = _verify_hierarchy(state_dir)
        print(
            f"  Hierarchy: root={hierarchy['root']}, "
            f"subs={hierarchy['subs']}, "
            f"sub_subs={hierarchy['sub_subs']}"
        )
        if hierarchy["errors"]:
            for err in hierarchy["errors"]:
                print(f"  WARNING: {err}")

        reports = _find_report_files(state_dir)
        print(f"  Report files found: {list(reports.keys())}")
        for name, data in reports.items():
            if isinstance(data, dict) and "error" not in data:
                reason_preview = str(data.get("reason", ""))[:80]
                print(
                    f"    {name}: status={data.get('status')}, "
                    f"reason={reason_preview}..."
                )
            else:
                print(f"    {name}: ERROR — {data}")

        cheating_files = _check_for_cheating_files(project_root)
        if cheating_files:
            print(f"  WARNING: Suspicious data files found: {cheating_files}")
        else:
            print("  No suspicious data sync files found")

        # ── 4. Capture all agent outputs ─────────────────────────────────
        print("\n--- Capturing final outputs for judge ---")
        all_outputs: dict[str, str] = {}
        sessions = _read_sessions(state_dir)
        for sid, s in sessions.items():
            name = s.get("name", "?")
            window = s.get("tmux_window")
            tmux_session = s.get("tmux_session", session)
            if window is not None:
                output = _capture_pane(
                    socket, tmux_session, window, lines=200
                )
                all_outputs[name] = output
                print(f"  {name}: {len(output)} chars captured")

        assert all_outputs, "No agent outputs captured"

        # ── 5. Claude-as-judge (Layers 2 & 3) ───────────────────────────
        print("\n--- Running Claude-as-judge ---")
        verdict = _judge_outputs(all_outputs, reports, hierarchy)
        print(f"Verdict: {json.dumps(verdict, indent=2)}")

        # ── 6. Assert all criteria ───────────────────────────────────────
        assert verdict.get("skill_activated"), (
            f"FAIL: Controller did not activate /delegating-to-agents skill.\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("three_subs_launched"), (
            f"FAIL: Controller did not launch 3 sub-agents.\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("two_layer_delegation"), (
            f"FAIL: Sub-agents did not delegate to sub-sub-agents "
            f"(no 2nd layer).\n{verdict.get('explanation')}"
        )
        assert verdict.get("correct_languages"), (
            f"FAIL: Jokes not in enough languages (need ≥4 of 6).\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("jokes_produced"), (
            f"FAIL: No actual jokes produced.\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("report_pipeline_used"), (
            f"FAIL: overcode report pipeline not used properly.\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("no_file_cheating"), (
            f"FAIL: Agents used file writes for data sync (cheating).\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("no_hierarchy_bypass"), (
            f"FAIL: Data bypassed middle layer "
            f"(sub-subs reported directly to controller).\n"
            f"{verdict.get('explanation')}"
        )
        assert verdict.get("results_collected"), (
            f"FAIL: Controller did not collect/summarize multi-theme "
            f"multi-language results.\n{verdict.get('explanation')}"
        )

        # Structural anti-cheating assertion
        if cheating_files:
            pytest.fail(f"Cheating files detected: {cheating_files}")

        print("\n--- All criteria passed! ---")
        print(
            f"Milestones: {json.dumps(
                {k: f'{v:.0f}s' for k, v in milestones.items()},
                indent=2,
            )}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--timeout=960"])
