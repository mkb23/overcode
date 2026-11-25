"""The mock agent CLIs a backend-neutral workflow runs over (#534).

A workflow that holds for every backend takes the ``mock_backend`` fixture
and launches through ``OvercodeCLI.launch_on``; it names scenarios by their
mock_claude names and each backend maps them onto its own mock's.

opencode agents get a project directory of their own: overcode installs the
telemetry plugin into ``<project>/.opencode/plugins/`` at launch, and the
repo is mounted read-only at /workspace. ``tests/mock_opencode.py`` writes
its conversations to the per-test store the harness names with
``OPENCODE_DATA_DIR`` and feeds the installed plugin, so overcode learns
the agent's ``ses_…`` id, tokens and cost the way it does from opencode.
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class MockBackend:
    name: str                 # overcode's backend name (`launch -B`)
    script: str               # the mock's file name, as typed into the pane
    ready_text: str           # on screen once the mock is up
    fresh_text: str           # on screen once an idle launch is up (not mid-turn)
    scenarios: Dict[str, str] = field(default_factory=dict)
    own_project_dir: bool = False

    def scenario(self, key: str) -> str:
        """This mock's scenario for a mock_claude scenario name."""
        return self.scenarios.get(key, key)

    def __str__(self) -> str:  # pytest ids
        return self.name


CLAUDE = MockBackend(
    name="claude-code",
    script="mock_claude.py",
    ready_text="Claude",
    fresh_text="Claude",
    scenarios={"one_turn": "task_complete"},
)

OPENCODE = MockBackend(
    name="opencode",
    script="mock_opencode.py",
    # The input box's hint line, there from the first frame to the last
    ready_text="ctrl+p commands",
    fresh_text="Ask anything",
    scenarios={
        "startup_idle": "oc_launch_and_idle",
        "task_running": "oc_task_running",
        "permission_bash": "oc_permission_bash",
        "task_complete": "oc_task_complete",
        "crash_mid_task": "oc_crash_mid_task",
        # One finished turn: the store holds a conversation with spend
        "one_turn": "oc_simple_response",
    },
    own_project_dir=True,
)

BACKENDS = (CLAUDE, OPENCODE)
