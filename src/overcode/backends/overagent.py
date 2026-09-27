"""overagent backend — Claude Code as overcode's own assistant (#484).

Claude Code with three additions, everything else inherited (hooks, stats,
resume, fork, status detection):

- ``--append-system-prompt-file``: the overagent persona
  (overagent.SYSTEM_PROMPT), written to ~/.overcode/overagent/ and passed
  on every launch including resumes, since the flag is not remembered. A
  file, not the text: the launch line is typed into the window's shell,
  and a multi-line argument there is noise in the pane and its history.
- Its own permission allow-list in ``--settings``, replacing the default
  one: read overcode's state and change the view without asking; kill,
  restart, launch, send and budgets always ask. Never bypass permissions:
  a bypass or permissive mode requested at launch is dropped.
- ``--plugin-dir``: its own plugin carrying the overcode-configurator
  skill, loaded for that session only. Nothing is installed into
  ~/.claude/skills: other Claude sessions never see it, and which overcode
  skills the user has globally stays their choice (`overcode skills
  install`).
"""

import dataclasses
import json
from typing import List

from .base import LaunchSpec
from .claude_code import ClaudeCodeBackend


class OveragentBackend(ClaudeCodeBackend):
    name = "overagent"
    display_name = "Overagent (Claude Code)"

    def build_command(self, spec: LaunchSpec) -> List[str]:
        from ..overagent import ALLOW, write_plugin, write_system_prompt

        spec = dataclasses.replace(
            spec,
            dangerously_skip_permissions=False,
            skip_permissions=False,
            include_punchy_perms=False,
            permissiveness_mode=None if spec.permissiveness_mode in ("bypass", "permissive")
            else spec.permissiveness_mode,
        )
        cmd = super().build_command(spec)
        i = cmd.index("--settings")
        settings = json.loads(cmd[i + 1])
        settings["permissions"] = {"allow": list(ALLOW)}
        cmd[i + 1] = json.dumps(settings)
        cmd.extend(["--append-system-prompt-file", str(write_system_prompt())])
        cmd.extend(["--plugin-dir", str(write_plugin())])
        return cmd
