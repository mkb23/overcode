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
        settings = self.load()
        # Check existing
        for entry in settings.get("hooks", {}).get(event, []):
            for hook in entry.get("hooks", []):
                if hook.get("command") == command:
                    return False

        updated = copy.deepcopy(settings)
        if "hooks" not in updated:
            updated["hooks"] = {}
        if event not in updated["hooks"]:
            updated["hooks"][event] = []

        updated["hooks"][event].append({
            "matcher": matcher,
            "hooks": [{"type": "command", "command": command}],
        })

        self.save(updated)
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

        updated = copy.deepcopy(settings)
        del updated["hooks"][event][index_to_remove]

        # Clean up empty event array
        if not updated["hooks"][event]:
            del updated["hooks"][event]

        # Clean up empty hooks dict
        if not updated["hooks"]:
            del updated["hooks"]

        self.save(updated)
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
