"""The foreground classifier agrees across Python and the opencode plugins.

hook_handler classifies a foreground Bash command for Claude/codex/grok/
Hermes hooks; the opencode plugins (v1 imports it from the v2 core) do the
same in JS inside opencode. The same commands go through both.
"""

import json
import shutil
import subprocess

import pytest

from overcode.backends.opencode2_plugin_install import bundled_plugin_dir_v2
from overcode.hook_handler import _classify_foreground_blocked_on, _compute_foreground

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")

COMMANDS = [
    "gh run watch 123",
    "gh pr checks --watch",
    "gh pr checks 12",
    "tail -f server.log",
    "tail -F app.log | grep err",
    "tail -n 20 app.log",
    "kubectl wait --for=condition=ready pod/x",
    "docker wait abc",
    "npx wait-on http://localhost:3000",
    "sleep 30",
    "   sleep 5 && echo done",
    "echo hi && sleep 5",
    "cd /tmp\nsleep 5",
    "sleepy 5",
    "sleep",
    "pytest -q",
    "",
    "make watch",
]

_SCRIPT = """
import { pathToFileURL } from "node:url"
const core = await import(pathToFileURL(process.argv[1]).href)
const commands = JSON.parse(process.argv[2])
console.log(JSON.stringify({
  blocked: commands.map((c) => core.classifyBlockedOn(c)),
  foreground: commands.map((c) => core.computeForeground("PreToolUse", "Bash", { command: c })),
  other: [
    core.computeForeground("PostToolUse", "Bash", { command: "sleep 5" }),
    core.computeForeground("PreToolUse", "Read", { command: "sleep 5" }),
    core.computeForeground("PreToolUse", null, null),
  ],
}))
"""


@pytest.fixture(scope="module")
def js():
    core = bundled_plugin_dir_v2() / "overcode-telemetry-core.mjs"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", _SCRIPT, str(core), json.dumps(COMMANDS)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_blocked_on_matches(js):
    assert js["blocked"] == [_classify_foreground_blocked_on(c) for c in COMMANDS]


def test_foreground_matches(js):
    assert js["foreground"] == [
        _compute_foreground("PreToolUse", "Bash", {"command": c}) for c in COMMANDS
    ]
    assert js["other"] == [
        _compute_foreground("PostToolUse", "Bash", {"command": "sleep 5"}),
        _compute_foreground("PreToolUse", "Read", {"command": "sleep 5"}),
        _compute_foreground("PreToolUse", None, None),
    ]


def test_commands_cover_every_kind(js):
    assert {k for k in js["blocked"] if k} == {"ci", "process", "sleep"}
