"""Read and write Claude Code settings.json files.

Provides a reusable editor for Claude Code's JSON settings, with
convenience methods for managing hooks.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path


class ClaudeConfigEditor:
    """Read and write Claude Code settings.json files."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def user_level(cls) -> ClaudeConfigEditor:
        """Editor for user-level settings (~/.claude/settings.json)."""
        return cls(Path.home() / ".claude" / "settings.json")

    @classmethod
    def project_level(cls, project_dir: Path | None = None) -> ClaudeConfigEditor:
        """Editor for project-level settings (.claude/settings.json).

        Args:
            project_dir: Project root. Defaults to cwd.
        """
        base = Path(project_dir) if project_dir else Path.cwd()
        return cls(base / ".claude" / "settings.json")

    def load(self) -> dict:
        """Load settings from file.

        Returns empty dict if file doesn't exist.
        Raises ValueError on invalid JSON or non-object content.
        """
        if not self.path.exists():
            return {}
        text = self.path.read_text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {self.path}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} contains non-object JSON")
        return data

    def save(self, settings: dict) -> None:
        """Write settings to file. Creates parent dirs as needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(settings, indent=2) + "\n")

    def _modify_settings(self, mutator_fn):
        """Load settings, deepcopy, apply mutator_fn, save. Returns mutator's result."""
        settings = self.load()
        settings = copy.deepcopy(settings)
        result = mutator_fn(settings)
        self.save(settings)
        return result

    def has_hook(self, event: str, command: str) -> bool:
        """Check if a command hook exists for the given event."""
        settings = self.load()
        for entry in settings.get("hooks", {}).get(event, []):
            for hook in entry.get("hooks", []):
                if hook.get("command") == command:
                    return True
        return False

    def add_hook(self, event: str, command: str, matcher: str = "") -> bool:
        """Add a command hook for an event.

        Returns True if the hook was added, False if it already exists.
        """
        if self.has_hook(event, command):
            return False

        def _add(settings):
            if "hooks" not in settings:
                settings["hooks"] = {}
            if event not in settings["hooks"]:
                settings["hooks"][event] = []
            settings["hooks"][event].append({
                "matcher": matcher,
                "hooks": [{"type": "command", "command": command}],
            })

        self._modify_settings(_add)
        return True

    def remove_hook(self, event: str, command: str) -> bool:
        """Remove a matcher group containing this command.

        Returns True if found and removed, False if not found.
        Cleans up empty event arrays and empty hooks dict.
        """
        settings = self.load()
        hooks_dict = settings.get("hooks", {})
        event_list = hooks_dict.get(event, [])

        # Find the matcher group index containing this command
        index_to_remove = None
        for i, entry in enumerate(event_list):
            for hook in entry.get("hooks", []):
                if hook.get("command") == command:
                    index_to_remove = i
                    break
            if index_to_remove is not None:
                break

        if index_to_remove is None:
            return False

        def _remove(settings):
            del settings["hooks"][event][index_to_remove]
            if not settings["hooks"][event]:
                del settings["hooks"][event]
            if not settings["hooks"]:
                del settings["hooks"]

        self._modify_settings(_remove)
        return True

    def list_hooks_matching(self, command_prefix: str) -> list[tuple[str, str]]:
        """Return [(event, command)] for all hooks whose command starts with prefix."""
        settings = self.load()
        results = []
        for event, entries in settings.get("hooks", {}).items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if cmd.startswith(command_prefix):
                        results.append((event, cmd))
        return results

    # ----- Hook detection -----

    @staticmethod
    def are_overcode_hooks_installed() -> bool:
        """Check if core overcode hooks are installed at user level.

        Requires at least UserPromptSubmit, Stop, and PostToolUse hooks
        to consider hooks mode viable.
        """
        editor = ClaudeConfigEditor.user_level()
        try:
            editor.load()
        except (ValueError, FileNotFoundError):
            return False
        core_events = ("UserPromptSubmit", "Stop", "PostToolUse")
        return all(editor.has_hook(event, "overcode hook-handler") for event in core_events)

    # ----- Permission management -----

    def add_permission(self, tool_pattern: str) -> bool:
        """Add to permissions.allow. Returns True if newly added."""
        settings = self.load()
        allow_list = settings.get("permissions", {}).get("allow", [])
        if tool_pattern in allow_list:
            return False

        def _add(settings):
            if "permissions" not in settings:
                settings["permissions"] = {}
            if "allow" not in settings["permissions"]:
                settings["permissions"]["allow"] = []
            settings["permissions"]["allow"].append(tool_pattern)

        self._modify_settings(_add)
        return True

    def remove_permission(self, tool_pattern: str) -> bool:
        """Remove from permissions.allow. Returns True if found."""
        settings = self.load()
        allow_list = settings.get("permissions", {}).get("allow", [])
        if tool_pattern not in allow_list:
            return False

        def _remove(settings):
            settings["permissions"]["allow"].remove(tool_pattern)
            if not settings["permissions"]["allow"]:
                del settings["permissions"]["allow"]
            if not settings["permissions"]:
                del settings["permissions"]

        self._modify_settings(_remove)
        return True

    def list_permissions_matching(self, prefix: str) -> list[str]:
        """Return entries from permissions.allow that start with prefix."""
        settings = self.load()
        return [
            p for p in settings.get("permissions", {}).get("allow", [])
            if p.startswith(prefix)
        ]
