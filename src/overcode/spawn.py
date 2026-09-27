"""
Cheap process starts for the long-running processes (#486).

The TUI and the monitor daemon run tmux (and git) many times a second.
By default Python starts each child with fork+exec, which on macOS costs
the parent ~1.7 ms of CPU per call for a process the TUI's size (144 MB,
a dozen threads) — about half of the TUI's CPU in a py-spy profile.
posix_spawn does the same job for ~0.2 ms, but CPython only uses it when
the call passes close_fds=False, names the executable by path, and sets
no cwd, new session or preexec_fn (subprocess.Popen._execute_child).

install() makes the first two the process-wide defaults, so every caller
— our own subprocess.run calls and libtmux's alike — gets posix_spawn
without being rewritten. close_fds=False is safe: since PEP 446 every fd
Python opens is non-inheritable, so children still get only 0-2 plus
whatever they are explicitly handed. A call that passes close_fds, an
executable or an env of its own keeps its own choice.

Only the TUI and the daemon call install(); tests and one-shot CLI
commands keep stock subprocess behaviour.
"""

import os
import shutil
import subprocess
from typing import Dict, Optional

_installed = False
_paths: Dict[str, str] = {}


def resolve(name: str) -> Optional[str]:
    """``name``'s full path on PATH, remembered once found; None if absent.

    A miss isn't remembered, so a tool installed later is still found.
    """
    path = _paths.get(name)
    if path is None:
        path = shutil.which(name)
        if path is not None:
            _paths[name] = path
    return path


def _spawnable(args, kwargs):
    """``args`` with a bare program name replaced by its path, when safe."""
    if kwargs.get("shell") or kwargs.get("executable") is not None or kwargs.get("env") is not None:
        return args
    if isinstance(args, (list, tuple)) and args and isinstance(args[0], str):
        if os.sep not in args[0]:
            path = resolve(args[0])
            if path is not None:
                return [path, *args[1:]]
    return args


def install() -> None:
    """Make posix_spawn the default for this process's children. Idempotent."""
    global _installed
    if _installed:
        return
    _installed = True
    original_init = subprocess.Popen.__init__

    def __init__(self, args, *pos, **kwargs):
        if not pos:  # close_fds etc. passed positionally: leave the call alone
            if not kwargs.get("pass_fds"):  # pass_fds needs close_fds
                kwargs.setdefault("close_fds", False)
            args = _spawnable(args, kwargs)
        original_init(self, args, *pos, **kwargs)

    subprocess.Popen.__init__ = __init__
