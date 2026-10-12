"""The libtmux internals implementations.py wraps are still there.

``_BoundedTmuxCmd`` bounds every tmux command by replacing the
``tmux_cmd`` name in ``libtmux.server`` and ``libtmux.neo``: those are
private details, and libtmux is pinned (pyproject) for that reason. A
libtmux that moves its command construction, or changes ``tmux_cmd``'s
call shape, would leave the wrapper silently unused (every tmux call
unbounded again) or broken. These tests make such a bump fail in CI.
"""

import importlib
import inspect
import pkgutil
import types

import libtmux
import libtmux.common
import libtmux.neo
import libtmux.server
import pytest

from overcode.implementations import _BoundedTmuxCmd

pytestmark = pytest.mark.unit

# libtmux functions that build a tmux_cmd without reaching the server
# (``tmux -V``), so they may keep libtmux's own, unbounded, command.
UNWRAPPED_OK = {
    ("libtmux.common", "get_version"),  # up to 0.5x
    ("libtmux.common", "_query_version"),  # 0.6x
}

# Keyword arguments libtmux passes to tmux_cmd (0.55+ passes tmux_bin=)
KNOWN_KEYWORDS = {"tmux_bin"}


def _functions(module):
    """(qualified name, code) of every function and method ``module`` defines."""
    for name, value in vars(module).items():
        if isinstance(value, types.FunctionType) and value.__module__ == module.__name__:
            yield name, value.__code__
        elif isinstance(value, type) and value.__module__ == module.__name__:
            for attr, member in vars(value).items():
                if isinstance(member, (staticmethod, classmethod)):
                    member = member.__func__
                elif isinstance(member, property):
                    member = member.fget
                if isinstance(member, types.FunctionType):
                    yield f"{name}.{attr}", member.__code__


def _names_tmux_cmd(code) -> bool:
    if "tmux_cmd" in code.co_names:
        return True
    return any(isinstance(c, types.CodeType) and _names_tmux_cmd(c) for c in code.co_consts)


def test_both_wrapped_names_exist_and_are_the_wrapper():
    assert libtmux.server.tmux_cmd is _BoundedTmuxCmd
    assert libtmux.neo.tmux_cmd is _BoundedTmuxCmd
    assert issubclass(_BoundedTmuxCmd, libtmux.common.tmux_cmd)


def test_every_libtmux_command_builder_resolves_to_the_wrapper():
    """A function that builds ``tmux_cmd`` finds the wrapper in its module.

    A new libtmux module (or a function that imports ``tmux_cmd`` from
    ``libtmux.common`` locally) would build unbounded commands.
    """
    builders = []
    for info in pkgutil.walk_packages(libtmux.__path__, "libtmux."):
        try:
            module = importlib.import_module(info.name)
        except Exception:  # optional extras (pytest plugin deps, ...)
            continue
        for name, code in _functions(module):
            if _names_tmux_cmd(code):
                builders.append((info.name, name, getattr(module, "tmux_cmd", None)))
    assert builders, "libtmux no longer builds commands through tmux_cmd"
    unwrapped = [
        f"{module}.{name}" for module, name, cmd in builders
        if cmd is not _BoundedTmuxCmd and (module, name) not in UNWRAPPED_OK
    ]
    assert unwrapped == []
    # the two paths every RealTmux read takes
    assert ("libtmux.server", "Server.cmd") in {(m, n) for m, n, _ in builders}
    assert ("libtmux.neo", "fetch_objs") in {(m, n) for m, n, _ in builders}


def test_libtmux_tmux_cmd_takes_args_and_known_keywords_only():
    params = list(inspect.signature(libtmux.common.tmux_cmd.__init__).parameters.values())[1:]
    assert any(p.kind is p.VAR_POSITIONAL for p in params)
    keywords = {p.name for p in params if p.kind is not p.VAR_POSITIONAL}
    assert keywords <= KNOWN_KEYWORDS, f"tmux_cmd grew arguments: {keywords - KNOWN_KEYWORDS}"


def test_server_commands_reach_the_wrapper_in_the_expected_shape(monkeypatch):
    """Server.cmd and the object listings construct ``_BoundedTmuxCmd``
    with string arguments and only keywords the wrapper knows about."""
    calls = []
    original = _BoundedTmuxCmd.__init__

    def recording_init(self, *args, **kwargs):
        calls.append((args, kwargs))
        original(self, *args, **kwargs)

    monkeypatch.setattr(_BoundedTmuxCmd, "__init__", recording_init)
    server = libtmux.Server(socket_name="oc-unit-libtmux-pin-nope")
    server.cmd("list-sessions")
    server.sessions  # neo.fetch_objs; no server is running, so empty

    assert len(calls) >= 2
    for args, kwargs in calls:
        assert all(isinstance(a, (str, int)) for a in args)
        assert set(kwargs) <= KNOWN_KEYWORDS
