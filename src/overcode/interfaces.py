"""
Protocol definitions and implementations for external dependencies.

This module re-exports from the split modules for backward compatibility:
- protocols.py: Protocol (interface) definitions
- implementations.py: Real (production) implementations
- mocks.py: Mock implementations for testing

New code should import directly from the specific modules:
    from overcode.protocols import TmuxInterface
    from overcode.implementations import RealTmux
    from overcode.mocks import MockTmux
"""

# Re-export protocols
from .protocols import (
    TmuxInterface,
    FileSystemInterface,
    SubprocessInterface,
)

# Re-export real implementations
from .implementations import (
    RealTmux,
    RealFileSystem,
    RealSubprocess,
)

# Re-export mocks
from .mocks import (
    MockTmux,
    MockFileSystem,
    MockSubprocess,
)

__all__ = [
    # Protocols
    "TmuxInterface",
    "FileSystemInterface",
    "SubprocessInterface",
    # Real implementations
    "RealTmux",
    "RealFileSystem",
    "RealSubprocess",
    # Mocks
    "MockTmux",
    "MockFileSystem",
    "MockSubprocess",
]
