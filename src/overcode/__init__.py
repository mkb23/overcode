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


def get_dev_version_suffix() -> str:
    """Get ` (git-describe)` if running from an editable git install, else ''."""
    try:
        import subprocess
        pkg_dir = Path(__file__).resolve().parent
        result = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            capture_output=True, text=True, cwd=pkg_dir, timeout=2,
        )
        if result.returncode == 0:
            return f" ({result.stdout.strip()})"
    except Exception:
        pass
    return ""


def get_full_version() -> str:
    """Full version string used at launch: `<version>[ (git-describe)]`.

    Recorded on each Session so we can see which overcode build spawned
    each agent — useful for diagnosing feature-regression questions like
    "was this agent launched before the --settings hook injection landed?"
    """
    return f"{__version__}{get_dev_version_suffix()}"
