"""Console-script entry point for the ``overcode`` command.

The typer CLI in :mod:`overcode.cli` imports typer, rich, libtmux and the
launcher before it can dispatch anything — roughly 70 ms of CPU per
process. That is fine for a human typing commands, but the same binary is
what every agent's hooks run (``overcode hook-handler``) on *every*
PreToolUse / PostToolUse / Stop / ... event. Across a fleet of busy agents
that adds up to whole cores burned on Python imports, competing with the
tmux server and the terminal for CPU — visible as typing lag.

So the hook path is dispatched here, before the CLI package is imported.
Everything else falls through to the full typer app unchanged.
"""

import sys

# Subcommands that must not pay for the typer CLI import. Only ``hook-handler``
# today; each entry maps to a zero-argument callable resolved lazily.
_FAST_PATHS = {
    "hook-handler": ("overcode.hook_handler", "handle_hook_event"),
}


def main() -> None:
    """Dispatch ``overcode <cmd>``; fast-path hook events, else run the CLI."""
    argv = sys.argv[1:]
    if len(argv) == 1 and argv[0] in _FAST_PATHS:
        module_name, func_name = _FAST_PATHS[argv[0]]
        import importlib

        getattr(importlib.import_module(module_name), func_name)()
        return

    from .cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
