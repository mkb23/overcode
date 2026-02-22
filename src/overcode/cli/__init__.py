"""
CLI interface for Overcode using Typer.

This package was split from a single cli.py module. All public names
are re-exported here for backward compatibility.
"""

# Import shared state (apps, options, utilities) — must come first
from ._shared import app, main_callback, _parse_duration, SessionOption  # noqa: F401

# Re-export for backward compat (tests patch overcode.cli.ClaudeLauncher)
from ..launcher import ClaudeLauncher  # noqa: F401

# Import submodules to register their commands with the Typer apps
from . import agent  # noqa: F401
from . import budget  # noqa: F401
from . import hooks  # noqa: F401
from . import skills  # noqa: F401
from . import perms  # noqa: F401
from . import monitoring  # noqa: F401
from . import daemon  # noqa: F401
from . import sister  # noqa: F401
from . import config  # noqa: F401


def main():
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
