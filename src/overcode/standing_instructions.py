"""
Standing instructions library for overcode agents.

Provides a library of pre-defined instruction presets that users can apply
to agents. Presets are stored in ~/.overcode/presets.json and can be
customized by the user.
"""

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, List

PRESETS_PATH = Path.home() / ".overcode" / "presets.json"


@dataclass
class InstructionPreset:
    """A pre-defined standing instruction preset."""
    name: str           # Short name: DEFAULT, CODING, etc.
    description: str    # One-line description for CLI help
    instructions: str   # Full instruction text for daemon claude


# Default presets - used to generate initial presets.json
DEFAULT_PRESETS: Dict[str, InstructionPreset] = {
    "DO_NOTHING": InstructionPreset(
        name="DO_NOTHING",
        description="Supervisor ignores this agent (default)",
        instructions=(
            "Do not interact with this agent at all. Do not approve or reject any prompts. "
            "Do not send any input. Leave the agent completely alone and let it wait for "
            "the human user. This agent is not under supervisor control."
        ),
    ),
    "STANDARD": InstructionPreset(
        name="STANDARD",
        description="General-purpose safe automation",
        instructions=(
            "Approve safe operations within the working directory: file reads/writes/edits, "
            "web fetches, git status/add/commit, running tests. Reject operations outside "
            "the project, rm -rf, or anything that could affect system stability. When "
            "uncertain, err on the side of caution."
        ),
    ),
    "PERMISSIVE": InstructionPreset(
        name="PERMISSIVE",
        description="Trusted agent, minimal friction",
        instructions=(
            "Approve most permission requests to keep work flowing. Trust the agent's "
            "judgment on file operations, web access, and shell commands within reason. "
            "Only reject clearly dangerous operations like rm -rf on large directories, "
            "operations on system files, or commands that could crash the system."
        ),
    ),
    "CAUTIOUS": InstructionPreset(
        name="CAUTIOUS",
        description="Sensitive project, careful oversight",
        instructions=(
            "Take a conservative approach. Approve read operations freely. For writes, "
            "check they're within the project and make sense for the task. Reject any "
            "git push, deployment commands, or operations that can't be easily undone. "
            "When in doubt, let the session wait for the human user."
        ),
    ),
    "RESEARCH": InstructionPreset(
        name="RESEARCH",
        description="Information gathering, exploration",
        instructions=(
            "Approve all read operations: file reads, web searches, web fetches, grep, "
            "glob, directory listings. Approve writing notes or summary files. Be "
            "cautious with code modifications - the goal is gathering information, not "
            "making changes. Reject shell commands that modify state."
        ),
    ),
    "CODING": InstructionPreset(
        name="CODING",
        description="Active development work",
        instructions=(
            "Approve file operations within the project: reads, writes, edits. Approve "
            "running tests, linters, and build commands. Approve git add and commit. "
            "Be cautious with git push - only if tests pass and work looks complete. "
            "Reject operations outside the project directory."
        ),
    ),
    "TESTING": InstructionPreset(
        name="TESTING",
        description="Running and fixing tests",
        instructions=(
            "Approve running test suites (pytest, jest, etc.) and viewing results. "
            "Approve file edits to fix failing tests. Approve re-running tests after "
            "fixes. Keep the agent focused on making tests pass. Reject unrelated "
            "changes or scope creep beyond test fixes."
        ),
    ),
    "REVIEW": InstructionPreset(
        name="REVIEW",
        description="Code review, analysis only",
        instructions=(
            "Approve only read operations: file reads, git log, git diff, grep searches. "
            "The agent should analyze and report, not modify. Reject all write operations, "
            "edits, and shell commands that change state. Let the agent provide analysis "
            "and recommendations only."
        ),
    ),
    "DEPLOY": InstructionPreset(
        name="DEPLOY",
        description="Deployment and release tasks",
        instructions=(
            "Approve deployment-related commands: git push, npm publish, docker build/push, "
            "deployment scripts. Verify tests pass before approving pushes. Approve version "
            "bumps and changelog updates. Be careful with production database commands - "
            "verify they're read-only or explicitly requested."
        ),
    ),
    "AUTONOMOUS": InstructionPreset(
        name="AUTONOMOUS",
        description="Fully autonomous operation",
        instructions=(
            "Approve all reasonable operations to maximize autonomous progress. Trust the "
            "agent to make good decisions. Only intervene for clearly dangerous operations "
            "(system file modifications, recursive deletes, credential exposure). The goal "
            "is minimal human interruption."
        ),
    ),
    "MINIMAL": InstructionPreset(
        name="MINIMAL",
        description="Just keep it from stalling",
        instructions=(
            "Only intervene when the agent is completely stuck on a permission prompt. "
            "Approve simple, safe operations. For anything complex or uncertain, let it "
            "wait for the human user. Don't provide guidance or redirect the agent - just "
            "handle permission gates."
        ),
    ),
}


def _ensure_presets_file() -> None:
    """Create presets.json with defaults if it doesn't exist."""
    if PRESETS_PATH.exists():
        return

    # Ensure directory exists
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Write default presets
    presets_data = {
        name: asdict(preset)
        for name, preset in DEFAULT_PRESETS.items()
    }
    with open(PRESETS_PATH, 'w') as f:
        json.dump(presets_data, f, indent=2)


def load_presets() -> Dict[str, InstructionPreset]:
    """Load presets from ~/.overcode/presets.json.

    Creates the file with defaults if it doesn't exist.

    Returns:
        Dict mapping preset names to InstructionPreset objects
    """
    _ensure_presets_file()

    try:
        with open(PRESETS_PATH, 'r') as f:
            data = json.load(f)

        presets = {}
        for name, preset_data in data.items():
            presets[name.upper()] = InstructionPreset(
                name=preset_data.get("name", name),
                description=preset_data.get("description", ""),
                instructions=preset_data.get("instructions", ""),
            )
        return presets

    except (json.JSONDecodeError, IOError):
        # Fall back to defaults if file is corrupted
        return DEFAULT_PRESETS.copy()


def save_presets(presets: Dict[str, InstructionPreset]) -> None:
    """Save presets to ~/.overcode/presets.json.

    Args:
        presets: Dict mapping preset names to InstructionPreset objects
    """
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)

    presets_data = {
        name: asdict(preset)
        for name, preset in presets.items()
    }
    with open(PRESETS_PATH, 'w') as f:
        json.dump(presets_data, f, indent=2)


def get_preset(name: str) -> Optional[InstructionPreset]:
    """Get a preset by name (case-insensitive).

    Args:
        name: Preset name to look up

    Returns:
        InstructionPreset if found, None otherwise
    """
    presets = load_presets()
    return presets.get(name.upper())


def get_preset_names() -> List[str]:
    """Get all preset names in order.

    Returns:
        List of preset names
    """
    presets = load_presets()
    # Return in a consistent order (DO_NOTHING first, then alphabetical)
    names = list(presets.keys())
    if "DO_NOTHING" in names:
        names.remove("DO_NOTHING")
        names = ["DO_NOTHING"] + sorted(names)
    else:
        names = sorted(names)
    return names


def resolve_instructions(input_text: str) -> tuple[str, Optional[str]]:
    """Resolve input to (full_instructions, preset_name_or_none).

    If input matches a preset name (case-insensitive), returns the preset's
    instructions and name. Otherwise returns the input as custom instructions.

    Args:
        input_text: User input - either a preset name or custom instructions

    Returns:
        Tuple of (full_instructions, preset_name_if_used)
    """
    preset = get_preset(input_text)
    if preset:
        return preset.instructions, preset.name
    return input_text, None


def add_preset(name: str, description: str, instructions: str) -> None:
    """Add or update a preset.

    Args:
        name: Preset name (will be uppercased)
        description: Short description
        instructions: Full instruction text
    """
    presets = load_presets()
    presets[name.upper()] = InstructionPreset(
        name=name.upper(),
        description=description,
        instructions=instructions,
    )
    save_presets(presets)


def remove_preset(name: str) -> bool:
    """Remove a preset.

    Args:
        name: Preset name to remove

    Returns:
        True if preset was removed, False if not found
    """
    presets = load_presets()
    name_upper = name.upper()
    if name_upper in presets:
        del presets[name_upper]
        save_presets(presets)
        return True
    return False


def reset_presets() -> None:
    """Reset presets to defaults."""
    save_presets(DEFAULT_PRESETS.copy())
