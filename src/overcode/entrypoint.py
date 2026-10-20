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


def main() -> None:
    """Dispatch ``overcode <cmd>``; fast-path hook events, else run the CLI.

    Only ``hook-handler``, ``hook-handler --backend <name>`` and
    ``hook-handler --prompt-context`` (the opencode plugins, on every
    prompt) take the fast path; any other shape (``--help``) goes to the
    typer CLI.
    """
    from .paths import mark_entry_point

    mark_entry_point()
    argv = sys.argv[1:]
    if argv and argv[0] == "hook-handler":
        if len(argv) == 1 or (len(argv) == 3 and argv[1] == "--backend"):
            from .hook_handler import handle_hook_event

            handle_hook_event(argv[2] if len(argv) == 3 else None)
            return
        if argv[1:] == ["--prompt-context"]:
            from .hook_handler import handle_prompt_context

            handle_prompt_context()
            return
    # The calibration statusline runs after every assistant message, like a hook
    if argv == ["statusline"]:
        from .statusline_tap import main as statusline_main

        statusline_main()
        return

    from .cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
