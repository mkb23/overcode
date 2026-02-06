"""
Overcode - A supervisor for managing multiple Claude Code instances.
"""

from pathlib import Path

_toml = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
if _toml.is_file():
    import tomllib
    with open(_toml, "rb") as _f:
        __version__ = tomllib.load(_f)["project"]["version"]
else:
    from importlib.metadata import version as _version
    __version__ = _version("overcode")
