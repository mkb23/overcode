"""
Agent commands: launch, list, attach, kill, follow, report, cleanup,
set-value, set-budget, annotate, send, show.
"""

from typing import Annotated, Optional, List

import typer
from rich import print as rprint

from ..exceptions import LaunchError
from ..launcher import AgentLauncher
from ._shared import app, SessionOption, _parse_duration, find_agent


def _parse_oversight_policy(on_stuck: Optional[str], oversight_timeout: Optional[str]) -> tuple[str, float]:
    """Parse oversight policy args into (policy, timeout_seconds)."""
    policy = "wait"
    timeout_seconds = 0.0
    if oversight_timeout:
        policy = "timeout"
        try:
            timeout_seconds = _parse_duration(oversight_timeout)
        except ValueError:
            rprint(f"[red]Error: Invalid duration '{oversight_timeout}'[/red]")
            raise typer.Exit(code=1)
    elif on_stuck:
        if on_stuck == "wait":
            policy = "wait"
        elif on_stuck == "fail":
            policy = "fail"
        elif on_stuck.startswith("timeout:"):
            policy = "timeout"
            try:
                timeout_seconds = _parse_duration(on_stuck[len("timeout:"):])
            except ValueError:
                rprint(f"[red]Error: Invalid duration in '--on-stuck {on_stuck}'[/red]")
                raise typer.Exit(code=1)
        else:
            rprint(f"[red]Error: Invalid --on-stuck value '{on_stuck}'. Use: wait, fail, timeout:DURATION[/red]")
            raise typer.Exit(code=1)
    return policy, timeout_seconds


def _store_oversight_policy(result, policy: str, timeout_seconds: float,
                            archive_after_seconds: Optional[float] = None) -> None:
    """Store the oversight policy and done-archive delay on the session if non-default."""
    from ..child_presets import DEFAULT_ARCHIVE_AFTER_SECONDS, format_duration
    fields = {}
    if policy != "wait" or timeout_seconds > 0:
        fields.update(oversight_policy=policy, oversight_timeout_seconds=timeout_seconds)
    if archive_after_seconds is not None and archive_after_seconds != DEFAULT_ARCHIVE_AFTER_SECONDS:
        fields["archive_after_seconds"] = archive_after_seconds
    if not fields:
        return
    from ..session_manager import SessionManager
    SessionManager().update_session(result.id, **fields)
    if "oversight_policy" in fields:
        rprint(f"  Oversight: {policy}" + (f" ({timeout_seconds:.0f}s)" if timeout_seconds > 0 else ""))
    if "archive_after_seconds" in fields:
        rprint(f"  Archive after done: {format_duration(archive_after_seconds)}")


def _launches_child(parent: Optional[str], no_parent: bool) -> bool:
    """Whether this launch makes a child (the launcher's rule: --parent, or run inside an agent)."""
    from ..agent_identity import in_agent
    return parent is not None or (not no_parent and in_agent())


def _resolve_child_settings(preset: Optional[str], is_child: bool, on_stuck: Optional[str],
                            oversight_timeout: Optional[str], archive_after: Optional[str],
                            budget: Optional[float]):
    """A launch's preset and its settings, explicit flags first (child_presets).

    Returns (preset or None, oversight policy, timeout seconds, archive
    delay seconds or None, budget). Exits on an invalid flag, including a
    ``--preset`` that names no preset; a bad config entry only warns.
    """
    chosen = None
    if preset is not None or is_child:
        from ..child_presets import UnknownPresetError, resolve_preset
        try:
            chosen, warnings = resolve_preset(preset, is_child=is_child)
        except UnknownPresetError as e:
            for warning in e.warnings:
                rprint(f"[yellow]Warning:[/yellow] {warning}")
            rprint(f"[red]Error: Unknown preset '{e.name}'. Use: {', '.join(e.known)}[/red]")
            raise typer.Exit(code=1)
        for warning in warnings:
            rprint(f"[yellow]Warning:[/yellow] {warning}")

    if on_stuck or oversight_timeout:
        policy, timeout_seconds = _parse_oversight_policy(on_stuck, oversight_timeout)
    elif chosen is not None:
        policy, timeout_seconds = chosen.oversight_policy, chosen.oversight_timeout_seconds
    else:
        policy, timeout_seconds = "wait", 0.0

    archive_seconds = None
    if archive_after is not None:
        from ..child_presets import parse_archive_after
        try:
            archive_seconds = parse_archive_after(archive_after)
        except ValueError:
            rprint(f"[red]Error: Invalid --archive-after '{archive_after}'. Use a duration "
                   f"(e.g. 10m, 24h) or never[/red]")
            raise typer.Exit(code=1)
    elif chosen is not None:
        archive_seconds = chosen.archive_after_seconds

    if budget is None and chosen is not None and chosen.budget_usd:
        budget = chosen.budget_usd
    return chosen, policy, timeout_seconds, archive_seconds, budget


def _json_stdout(command):
    """With ``--json``, ``command``'s human-readable lines go to stderr and
    stdout carries only what ``_echo_json`` prints."""
    import contextlib
    import functools
    import sys

    @functools.wraps(command)
    def run(*args, **kwargs):
        if not kwargs.get("as_json"):
            return command(*args, **kwargs)
        _json_out.append(sys.stdout)
        try:
            with contextlib.redirect_stdout(sys.stderr):
                return command(*args, **kwargs)
        finally:
            _json_out.pop()
    return run


_json_out: list = []


def _echo_json(text: str) -> None:
    import sys
    print(text, file=_json_out[-1] if _json_out else sys.stdout, flush=True)


def _lock_if_renamed(sessions, session_id: str, new_name: str) -> None:
    """Mark the name a human's (locked) once the record carries ``new_name``."""
    from ..name_source import HUMAN
    current = sessions.get_session(session_id)
    if current is not None and current.name == new_name:
        sessions.update_session(session_id, name_source=HUMAN)


def _list_json(session_data, daemon_state=None) -> str:
    """``overcode list --json``: each agent's identity, name history, status
    and spend (its total and its trip, ``trip.py``)."""
    import json
    from .trip import trip_report
    return json.dumps([
        {"id": sess.id, "name": sess.name, "name_source": sess.name_source,
         "name_forced": sess.name_forced, "previous_names": list(sess.previous_names),
         "status": status, "activity": activity,
         "parent_session_id": sess.parent_session_id, "repo": sess.repo_name,
         "branch": sess.branch, "backend": sess.backend, "model": sess.model,
         "is_remote": bool(getattr(sess, "is_remote", False)),
         "spend": trip_report(sess, daemon_state)}
        for sess, status, activity, *_ in session_data
    ], indent=2)


def _naming_enabled() -> bool:
    from ..config import get_naming_config
    return get_naming_config()["enabled"]


def _launch_json(result) -> str:
    """A launched (or forked) agent as ``--json`` prints it."""
    import json
    return json.dumps({"ok": True, "id": result.id, "name": result.name,
                       "name_source": result.name_source, "name_forced": result.name_forced,
                       "parent_session_id": result.parent_session_id})


def _check_skill_staleness() -> None:
    """Warn if installed skills are modified (#290)."""
    from ..bundled_skills import any_skills_stale
    if any_skills_stale():
        rprint("[yellow]Warning:[/yellow] Installed skills are modified. Run [bold]overcode skills install[/bold] to update.")


def _gather_session_stats(sess, pane_content_raw: str) -> dict:
    """Gather claude_stats, git_diff, bg_bash_count, live_sub_count for a session."""
    from ..stats_reader import stats_reader_for_session
    from ..backends import session_backend_name
    from ..status_patterns import (
        extract_background_bash_count,
        extract_live_subagent_count,
        extract_in_session_mode,
        get_patterns,
    )
    from ..git_stats import (
        get_git_diff_stats,
        get_git_untracked_count,
        effective_git_directory,
    )

    patterns = get_patterns(session_backend_name(sess))
    bg_bash_count = extract_background_bash_count(pane_content_raw, patterns) if pane_content_raw else 0
    live_sub_count = extract_live_subagent_count(pane_content_raw, patterns) if pane_content_raw else 0
    in_session_mode = extract_in_session_mode(pane_content_raw, patterns) if pane_content_raw else ""

    claude_stats = None
    try:
        claude_stats = stats_reader_for_session(sess).get_stats(sess)
        if claude_stats:
            live_sub_count = max(live_sub_count, claude_stats.live_subagent_count)
    except Exception:
        pass

    git_diff = None
    git_untracked = None
    try:
        _gdir = effective_git_directory(sess)
        if _gdir:
            git_diff = get_git_diff_stats(_gdir)
            git_untracked = get_git_untracked_count(_gdir)
    except Exception:
        pass

    return {
        "claude_stats": claude_stats,
        "git_diff": git_diff,
        "git_untracked": git_untracked,
        "bg_bash_count": bg_bash_count,
        "live_sub_count": live_sub_count,
        "in_session_mode": in_session_mode,
    }


def _subtree_costs(sessions, daemon_state, use_daemon: bool) -> dict:
    """Subtree cost by session id: the daemon's, and a sister's forwarded one."""
    subtree_costs = {}
    if use_daemon:
        for ds in daemon_state.sessions:
            if ds.subtree_cost_usd > 0:
                subtree_costs[ds.session_id] = ds.subtree_cost_usd
    for s in sessions:
        rds = getattr(s, 'remote_daemon_state', None)
        if rds and rds.get('subtree_cost_usd', 0) > 0:
            subtree_costs[s.id] = rds['subtree_cost_usd']
    return subtree_costs


def _reconcile_without_daemon(launcher, tmux_session: str) -> None:
    """Settle lifecycle once when no monitor daemon is running to have done it.

    The daemon is the one writer of terminated / done / waiting_oversight
    (and the #432 refund on done); with it running, a CLI listing only
    reads. Without it nothing would ever mark a gone window terminated, so
    ``list`` and ``cleanup`` apply the same rule once
    (``AgentLauncher.reconcile_lifecycle``) and say what they found.
    """
    from ..monitor_daemon import is_monitor_daemon_running

    if is_monitor_daemon_running(tmux_session):
        return
    result = launcher.reconcile_lifecycle()
    for name, refunded in result.refunds:
        print(f"  Refunded ${refunded:.4f} from '{name}' to its parent")
    if result.terminated:
        print(f"Detected {len(result.terminated)} terminated session(s): "
              f"{', '.join(result.terminated)}")


def _get_sessions_to_cleanup(launcher, done: bool) -> tuple[int, int]:
    """Returns (terminated_count, done_count) after performing cleanup."""
    terminated_count = launcher.cleanup_terminated_sessions()

    done_count = 0
    if done:
        all_sessions = launcher.sessions.list_sessions()
        done_sessions = [s for s in all_sessions if s.status == "done"]
        for sess in done_sessions:
            launcher._kill_single_session(sess)
            done_count += 1

    return terminated_count, done_count


@app.command()
@_json_stdout
def launch(
    name: Annotated[str, typer.Option("--name", "-n", help="Name for the agent")],
    directory: Annotated[
        Optional[str], typer.Option("--directory", "-d", help="Working directory")
    ] = None,
    prompt: Annotated[
        Optional[str], typer.Option("--prompt", "-p", help="Initial prompt to send")
    ] = None,
    skip_permissions: Annotated[
        bool,
        typer.Option(
            "--skip-permissions",
            help="Auto-deny permission prompts (--permission-mode dontAsk)",
        ),
    ] = False,
    bypass_permissions: Annotated[
        bool,
        typer.Option(
            "--bypass-permissions",
            help="Bypass all permission checks (--dangerously-skip-permissions)",
        ),
    ] = False,
    normal_permissions: Annotated[
        bool,
        typer.Option(
            "--normal-permissions",
            help="Run in normal permission mode even under a bypass or permissive parent "
                 "(still inherits everything else)",
        ),
    ] = False,
    parent: Annotated[
        Optional[str],
        typer.Option("--parent", help="Parent agent name for hierarchy (#244)"),
    ] = None,
    no_parent: Annotated[
        bool,
        typer.Option(
            "--no-parent",
            help="Launch top-level, not as the caller's child (from inside an agent, overagent only)",
        ),
    ] = False,
    skills: Annotated[
        Optional[str],
        typer.Option(
            "--skills",
            help="Skill profile to switch on (see `overcode skills profile list`); "
                 "'none' for none. Default: parent's, then the folder's pinned profile",
        ),
    ] = None,
    follow: Annotated[
        bool,
        typer.Option("--follow", "-f", help="Stream child output and block until done (#244)"),
    ] = False,
    on_stuck: Annotated[
        Optional[str],
        typer.Option("--on-stuck", help="Policy when child stops: wait (default), fail, timeout:DURATION"),
    ] = None,
    oversight_timeout: Annotated[
        Optional[str],
        typer.Option("--oversight-timeout", help="Shorthand for --on-stuck timeout:DURATION (e.g. 5m, 1h)"),
    ] = None,
    preset: Annotated[
        Optional[str],
        typer.Option(
            "--preset",
            help="Children preset: supervised (default), balanced, autonomous, or one from "
                 "config.yaml. Sets oversight, archive delay and a default budget; flags override",
        ),
    ] = None,
    archive_after: Annotated[
        Optional[str],
        typer.Option(
            "--archive-after",
            help="Close a done child's window this long after it finishes (e.g. 10m, 24h; "
                 "default 1h), or never",
        ),
    ] = None,
    allowed_tools: Annotated[
        Optional[str],
        typer.Option("--allowed-tools", help="Comma-separated tools to allow (e.g. 'Bash,Read,Write,Edit')"),
    ] = None,
    model: Annotated[
        Optional[str],
        typer.Option("--model", "-m", help="Model (e.g. sonnet, opus; openai/gpt-4o-mini for opencode; gpt-5.6-sol for codex; grok-4.6 for grok; gpt-5-mini or anthropic/claude-opus-4.6 for hermes)"),
    ] = None,
    backend_args: Annotated[
        Optional[List[str]],
        typer.Option(
            "--backend-arg",
            help="Extra agent-CLI flag (repeatable, e.g. '--verbose')",
        ),
    ] = None,
    claude_args: Annotated[
        Optional[List[str]],
        typer.Option(
            "--claude-arg",
            hidden=True,
            help="Deprecated alias for --backend-arg.",
        ),
    ] = None,
    budget: Annotated[
        Optional[float],
        typer.Option("--budget", "-b", help="Cost budget in USD (deducted from parent if parent has budget)"),
    ] = None,
    agent: Annotated[
        Optional[str],
        typer.Option("--agent", "-a", help="Agent persona to run as (Claude Code: .claude/agents/)"),
    ] = None,
    teams: Annotated[
        bool,
        typer.Option("--teams", help="Enable Claude Code agent teams (CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS)"),
    ] = False,
    provider: Annotated[
        Optional[str],
        typer.Option("--provider", "-P", help="API provider: 'web' (Claude.ai OAuth) or 'bedrock' (AWS Bedrock)"),
    ] = None,
    backend: Annotated[
        Optional[str],
        # -b is already --budget, so the backend picker takes -B.
        typer.Option("--backend", "-B", help="Agent CLI backend: claude-code (default), opencode, opencode2, codex, grok, hermes, or shell (a plain $SHELL terminal; -p is typed as a command)"),
    ] = None,
    wrapper: Annotated[
        Optional[str],
        typer.Option("--wrapper", "-w", help="Wrapper script (path or name from ~/.overcode/wrappers/)"),
    ] = None,
    no_inherit: Annotated[
        bool,
        typer.Option(
            "--no-inherit",
            help="Don't inherit provider/model/wrapper/permissions from the parent agent (#433)",
        ),
    ] = False,
    billing_code: Annotated[
        Optional[str],
        typer.Option(
            "--billing-code",
            help="Bill this agent's spend to this code (default: its name; a child always takes its parent's)",
        ),
    ] = None,
    sister: Annotated[
        Optional[str],
        typer.Option("--sister", "-S", help="Launch on a remote sister machine (by name from config)"),
    ] = None,
    force_name: Annotated[
        bool,
        typer.Option(
            "--force-name",
            help="From inside an agent: launch with a name the naming policy refuses "
                 "(not one that is taken or invalid)",
        ),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the result (or a refused name) as JSON")
    ] = False,
    session: SessionOption = "agents",
):
    """Launch a new agent.

    From inside an agent, the name is checked against the naming policy
    (`overcode name policy`); a refused name exits 3 with the rule it
    broke and a suggestion.
    """
    import os

    # --claude-arg is the pre-Phase-6 spelling of --backend-arg; both fold
    # into one list so a mixed invocation still works.
    if claude_args:
        backend_args = list(backend_args or []) + list(claude_args)

    if normal_permissions and (bypass_permissions or skip_permissions):
        from ..launch_settings import NORMAL_PERMISSIONS_CONFLICT
        rprint(f"[red]Error: {NORMAL_PERMISSIONS_CONFLICT}[/red]")
        raise typer.Exit(code=1)

    # Remote launch via sister
    if sister:
        from ..config import get_sister_by_name
        from ..sister_controller import SisterController

        sister_config = get_sister_by_name(sister)
        if not sister_config:
            from ..config import get_sisters_config
            available = [s["name"] for s in get_sisters_config()]
            if available:
                rprint(f"[red]Error: Sister '{sister}' not found. Available: {', '.join(available)}[/red]")
            else:
                rprint("[red]Error: No sisters configured. Add sisters to ~/.overcode/config.yaml[/red]")
            raise typer.Exit(code=1)

        remote_dir = directory if directory else "."
        if not directory:
            rprint("[yellow]Warning:[/yellow] No --directory given; remote agent will use '.'")
        if billing_code:
            # The sister launch API takes no billing code (the TUI warns the same)
            rprint(f"[yellow]Warning:[/yellow] Billing codes are not yet supported for remote "
                   f"launches; '{name}' is billed to its name "
                   f"(overcode rebill on {sister} moves it)")

        # Map permission flags to permissions string
        if bypass_permissions:
            permissions = "bypass"
        elif skip_permissions:
            permissions = "skip"
        else:
            permissions = "normal"

        controller = SisterController()
        result = controller.launch_agent(
            sister_url=sister_config["url"],
            api_key=sister_config.get("api_key", ""),
            directory=remote_dir,
            name=name,
            prompt=prompt,
            permissions=permissions,
        )

        if result.ok:
            rprint(f"\n[green]✓[/green] Remote agent '[bold]{name}[/bold]' launched on [bold]{sister}[/bold]")
            if prompt:
                rprint("  Initial prompt sent")
        else:
            rprint(f"[red]Error: Remote launch failed: {result.error}[/red]")
            raise typer.Exit(code=1)
        return

    # Children preset, oversight policy and archive delay (flags override the preset)
    chosen_preset, oversight_policy, oversight_timeout_seconds, archive_after_seconds, budget = (
        _resolve_child_settings(preset, _launches_child(parent, no_parent), on_stuck,
                                oversight_timeout, archive_after, budget))

    # Provider/wrapper resolution (CLI flag > parent settings > config
    # default) happens in the launcher (#433); only validate the explicit
    # flag here for a clean error message.
    if provider is not None and provider not in ("web", "bedrock"):
        rprint(f"[red]Error: Invalid provider '{provider}'. Use: web, bedrock[/red]")
        raise typer.Exit(code=1)

    # Backend resolution (flag > parent > config default) happens in the
    # launcher; validate the explicit flag here for a clean error message.
    if backend is not None:
        from ..backends import list_backends
        if backend not in list_backends():
            known = ", ".join(list_backends())
            rprint(f"[red]Error: Unknown backend '{backend}'. Use: {known}[/red]")
            raise typer.Exit(code=1)

    # Default to current directory if not specified
    working_dir = directory if directory else os.getcwd()

    from .naming import check_launch_name
    name_source, name_forced = check_launch_name(name, working_dir, force_name, as_json)

    launcher = AgentLauncher(session)

    try:
        result = launcher.launch(
            name=name,
            start_directory=working_dir,
            initial_prompt=prompt,
            skip_permissions=skip_permissions,
            dangerously_skip_permissions=bypass_permissions,
            normal_permissions=normal_permissions,
            parent_name=parent,
            allowed_tools=allowed_tools,
            extra_cli_args=backend_args,
            agent_teams=teams,
            budget_usd=budget,
            agent_persona=agent,
            model=model,
            provider=provider,
            backend=backend,
            wrapper=wrapper,
            inherit_parent_settings=not no_inherit,
            no_parent=no_parent,
            skill_profile=skills,
            billing_code=billing_code,
            name_source=name_source,
            name_forced=name_forced,
        )
    except LaunchError as e:
        print(e)
        raise typer.Exit(code=1)

    if result and as_json:
        _echo_json(_launch_json(result))
    if result:
        rprint(f"\n[green]✓[/green] Agent '[bold]{name}[/bold]' launched")
        if result.parent_session_id:
            parent_record = launcher.sessions.get_session(result.parent_session_id)
            rprint(f"  Parent: {parent_record.name if parent_record else parent or '?'}")
        if result.skill_profile:
            rprint(f"  Skills: {result.skill_profile}")
        if prompt:
            rprint("  Initial prompt sent")
        if allowed_tools:
            rprint(f"  Allowed tools: {allowed_tools}")
        if backend_args:
            rprint(f"  Extra CLI args: {' '.join(backend_args)}")
        if agent:
            rprint(f"  Agent: {agent}")
        if teams:
            rprint("  Agent teams: enabled")
        if result.wrapper:
            rprint(f"  Wrapper: {result.wrapper}")
        if result.provider != "web":
            rprint(f"  Provider: {result.provider}")
        from ..backends import DEFAULT_BACKEND, session_backend_name
        if session_backend_name(result) != DEFAULT_BACKEND:
            rprint(f"  Backend: {session_backend_name(result)}")
        if result.model and not model:
            rprint(f"  Model: {result.model} (inherited)")
        if normal_permissions:
            rprint("  Permissions: normal")
        if budget is not None and budget > 0:
            rprint(f"  Budget: ${budget:.2f}")
        if chosen_preset is not None:
            from ..child_presets import describe
            rprint(f"  Preset: {chosen_preset.name} ({describe(chosen_preset)})")

        _store_oversight_policy(result, oversight_policy, oversight_timeout_seconds,
                                archive_after_seconds)
        _check_skill_staleness()

        if follow:
            from ..follow_mode import follow_agent
            exit_code = follow_agent(name, session)
            raise typer.Exit(code=exit_code)

        rprint("\nTo view: [bold]overcode attach[/bold]")


@app.command()
@_json_stdout
def fork(
    source: Annotated[str, typer.Argument(help="Name of agent to fork from")],
    name: Annotated[
        Optional[str], typer.Option("--name", "-n", help="Name for the forked agent (default: <source>-fork)")
    ] = None,
    prompt: Annotated[
        Optional[str], typer.Option("--prompt", "-p", help="Initial prompt to send to the fork")
    ] = None,
    normal_permissions: Annotated[
        bool,
        typer.Option(
            "--normal-permissions",
            help="Run the fork in normal permission mode instead of the source's",
        ),
    ] = False,
    force_name: Annotated[
        bool,
        typer.Option(
            "--force-name",
            help="From inside an agent: fork with a name the naming policy refuses",
        ),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the result (or a refused name) as JSON")
    ] = False,
    session: SessionOption = "agents",
):
    """Fork an agent, creating a child with the source's conversation context.

    Uses the backend's fork gesture (Claude Code: --resume --fork-session;
    opencode: --session --fork) to branch the conversation history into a new
    independent agent. The forked agent inherits the source's directory,
    permissions (unless --normal-permissions), agent persona, and CLI flags.

    Examples:
        overcode fork my-agent                          # Fork as my-agent-fork
        overcode fork my-agent -n side-analysis         # Fork with custom name
        overcode fork my-agent -p "analyze test failures"  # Fork with initial prompt
        overcode fork my-agent --normal-permissions     # Fork a bypass agent, prompts on
    """
    from ..session_manager import SessionManager
    from ..backends import BackendCapability, get_backend, supports

    sm = SessionManager()
    source_session = find_agent(sm, source)
    if not source_session:
        rprint(f"[red]Error: Agent '{source}' not found[/red]")
        raise typer.Exit(code=1)

    backend = get_backend(getattr(source_session, "backend", None))
    if not supports(backend, BackendCapability.FORK):
        rprint(f"[red]Error: backend '{backend.name}' does not support fork[/red]")
        raise typer.Exit(code=1)

    if not source_session.active_agent_session_id:
        rprint(f"[red]Error: Agent '{source}' has no active agent session ID yet[/red]")
        rprint("[dim]The monitor daemon detects session IDs periodically — try again shortly[/dim]")
        raise typer.Exit(code=1)

    if source_session.status in ("terminated", "done"):
        rprint(f"[red]Error: Cannot fork a {source_session.status} agent[/red]")
        raise typer.Exit(code=1)

    # Default fork name
    fork_name = name if name else f"{source}-fork"
    if name:
        from .naming import check_launch_name
        name_source, name_forced = check_launch_name(
            name, source_session.start_directory, force_name, as_json, sessions=sm)
    else:
        from ..name_source import DEFAULT
        name_source, name_forced = DEFAULT, False

    launcher = AgentLauncher(session)
    try:
        result = launcher.launch_fork(
            name=fork_name,
            source_session=source_session,
            initial_prompt=prompt,
            normal_permissions=normal_permissions,
            name_source=name_source,
            name_forced=name_forced,
        )
    except LaunchError as e:
        print(e)
        rprint(f"[red]Error: Failed to fork '{source}'[/red]")
        raise typer.Exit(code=1)

    if as_json:
        _echo_json(_launch_json(result))
    rprint(f"\n[green]✓[/green] Forked '[bold]{source}[/bold]' → '[bold]{fork_name}[/bold]'")
    rprint(f"  Directory: {result.start_directory}")
    if result.agent_persona:
        rprint(f"  Agent: {result.agent_persona}")
    if prompt:
        rprint("  Initial prompt sent")
    rprint(f"\nTo view: [bold]overcode attach {fork_name}[/bold]")


@app.command("list")
def list_agents(
    name: Annotated[
        Optional[str], typer.Argument(help="Agent name to filter (show agent + descendants)")
    ] = None,
    show_done: Annotated[
        bool, typer.Option("--show-done", help="Include 'done' child agents (#244)")
    ] = False,
    cost: Annotated[
        bool, typer.Option("--cost", help="Show $ cost instead of token counts")
    ] = False,
    joules: Annotated[
        bool, typer.Option("--joules", help="Show energy in joules instead of token counts")
    ] = False,
    sisters: Annotated[
        bool, typer.Option("--sisters", help="Include sister (remote) agents")
    ] = False,
    low: Annotated[
        bool, typer.Option("--low", help="Low detail (identity only)")
    ] = False,
    med: Annotated[
        bool, typer.Option("--med", help="Medium detail")
    ] = False,
    full: Annotated[
        bool, typer.Option("--full", help="Full detail (default)")
    ] = False,
    sort: Annotated[
        str, typer.Option("--sort", "-s", help="Sort mode: alphabetical, by_status, by_value, by_tree")
    ] = "by_tree",
    as_json: Annotated[
        bool, typer.Option("--json", help="One JSON object per agent: identity, name source, status")
    ] = False,
    self_: Annotated[
        bool, typer.Option("--self", help="From inside an agent: it and its descendants, by id "
                                           "(survives renames)")
    ] = False,
    session: SessionOption = "agents",
):
    """List running agents with status.

    Shows a compact TUI-style summary line per agent with status, time-in-state,
    timing breakdown, token/cost usage, context window %, and git diff stats.

    With no arguments, shows all agents with depth-based indentation.
    With a name, shows that agent + all its descendants.
    """
    from ..stats_reader import stats_reader_for_session
    from ..git_stats import effective_git_directory, get_git_diff_stats, get_git_untracked_count
    from ..tui_helpers import get_status_symbol
    from ..monitor_daemon_state import get_monitor_daemon_state
    from ..summary_columns import (
        FleetRow, build_cli_context, compute_column_widths, compute_fleet_flags,
        pad_and_join_cells, render_header_cells, render_summary_cells,
    )
    from ..tui_logic import compute_tree_metadata, sort_sessions
    from ..name_source import name_marker, title_pending_mark
    from rich.console import Console

    # Resolve detail level (mutually exclusive flags, default full)
    if low:
        detail = "low"
    elif med:
        detail = "med"
    else:
        detail = "full"

    launcher = AgentLauncher(session)
    _reconcile_without_daemon(launcher, session)
    sessions = launcher.list_sessions()

    # Merge sister sessions if --sisters flag
    has_sisters = False
    local_hostname = ""
    if sisters:
        from ..sister_poller import SisterPoller
        poller = SisterPoller()
        has_sisters = poller.has_sisters
        local_hostname = poller.local_hostname
        if has_sisters:
            remote_sessions = poller.poll_all()
            sessions = sessions + remote_sessions

    if not sessions:
        if as_json:
            typer.echo("[]")
            return
        rprint("[dim]No running agents[/dim]")
        return

    # Filter to specific agent + descendants if name given (#244); --self
    # finds the calling agent by id, which a rename does not change
    if self_ or name:
        if self_:
            from ..agent_identity import env_agent_id
            own_id = env_agent_id()
            root = launcher.sessions.get_session(own_id) if own_id else None
            if not root:
                rprint("[red]Error: --self works only inside an overcode agent[/red]")
                raise typer.Exit(code=1)
        else:
            root = find_agent(launcher.sessions, name)
        if not root:
            rprint(f"[red]Error: Agent '{name}' not found[/red]")
            raise typer.Exit(code=1)
        descendants = launcher.sessions.get_descendants(root.id)
        allowed_ids = {root.id} | {d.id for d in descendants}
        sessions = [s for s in sessions if s.id in allowed_ids]

    # Filter out "done" agents unless --show-done (#244)
    if not show_done:
        sessions = [s for s in sessions if s.status != "done"]

    if not sessions:
        rprint("[dim]No running agents[/dim]")
        return

    # Sort using the same logic as the TUI
    sessions = sort_sessions(sessions, sort)
    tree_meta = compute_tree_metadata(sessions)

    # Column alignment widths
    max_name_len = max((len(s.name) for s in sessions), default=10)
    name_width = min(max(max_name_len, 10), 20)
    max_repo_width = max((len(s.repo_name or "n/a") for s in sessions), default=5)
    max_branch_width = max((len(s.branch or "n/a") for s in sessions), default=5)
    all_names_match_repos = all(
        s.name == s.repo_name for s in sessions if s.repo_name
    )

    # Prefer daemon state for status/activity (single source of truth)
    daemon_state = get_monitor_daemon_state(session)
    use_daemon = daemon_state is not None and not daemon_state.is_stale()

    subtree_costs = _subtree_costs(sessions, daemon_state, use_daemon)

    # Only create detector as fallback when daemon isn't running
    detector = None
    if not use_daemon:
        from ..status_detector_factory import StatusDetectorDispatcher
        detector = StatusDetectorDispatcher(session)

    console = Console()
    terminated_count = 0

    # First pass: collect status/activity/stats per session
    session_data = []
    for sess in sessions:
        if getattr(sess, 'is_remote', False):
            status = sess.stats.current_state or "running"
            activity = sess.stats.current_task or ""
        elif sess.status == "terminated":
            status = "terminated"
            activity = "(tmux window no longer exists)"
            terminated_count += 1
        elif sess.status == "done":
            status = "done"
            activity = "(completed)"
        elif use_daemon:
            ds = daemon_state.get_session(sess.id)
            if ds:
                status = ds.current_status
                activity = ds.current_activity
            else:
                if detector is None:
                    from ..status_detector_factory import StatusDetectorDispatcher
                    detector = StatusDetectorDispatcher(session)
                status, activity, _ = detector.detect_status(sess)
        else:
            status, activity, _ = detector.detect_status(sess)

        if sess.is_asleep:
            status = "asleep"

        claude_stats = None
        try:
            claude_stats = stats_reader_for_session(sess).get_stats(sess)
        except Exception:
            pass
        if claude_stats is None and getattr(sess, 'is_remote', False):
            from ..history_reader import synthesize_remote_stats
            claude_stats = synthesize_remote_stats(sess)

        git_diff = None
        git_untracked = None
        if getattr(sess, 'is_remote', False):
            git_diff = getattr(sess, 'remote_git_diff', None)
            git_untracked = getattr(sess, 'remote_git_untracked', None)
        else:
            try:
                _gdir = effective_git_directory(sess)
                if _gdir:
                    git_diff = get_git_diff_stats(_gdir)
                    git_untracked = get_git_untracked_count(_gdir)
            except Exception:
                pass

        session_data.append((sess, status, activity, claude_stats, git_diff, git_untracked))

    if as_json:
        typer.echo(_list_json(session_data, daemon_state if use_daemon else None))
        return

    # Pull structured 2-column status detail off the detector when available
    # (#TBD). Detector may be None when daemon state alone supplies status.
    def _get_status_detail(sess):
        if detector is None:
            return None
        getter = getattr(detector, "get_status_detail", None)
        if getter is None:
            return None
        try:
            return getter(sess.name)
        except Exception:
            return None

    # Each child's result, from its session (the daemon publishes the
    # same, from the same fields), and each parent's children by state
    from ..child_lifecycle import child_rollup, task_result
    from ..tui_helpers import result_text
    results = {sess.id: task_result(sess, status) for sess, status, *_ in session_data}
    children: dict = {}
    for sess, status, *_ in session_data:
        if getattr(sess, 'parent_session_id', None):
            children.setdefault(sess.parent_session_id, []).append(
                (status, results[sess.id][0]))

    # Each agent's delegated work (the 👥 column): what the daemon
    # published, else what this command's own detection read
    def _swarm_rollup(sess):
        if use_daemon:
            ds = daemon_state.get_session(sess.id)
            if ds is not None:
                return ds.swarm_rollup if isinstance(ds.swarm_rollup, dict) else None
        getter = getattr(detector, "get_swarm_rollup", None)
        try:
            rollup = getter(sess.name) if getter is not None else None
        except Exception:
            return None
        return rollup if isinstance(rollup, dict) else None

    swarms = {sess.id: _swarm_rollup(sess) for sess, *_ in session_data}
    # The SubAgents fallback without a rollup: subagents whose transcripts moved lately
    live_subs = {sess.id: getattr(claude_stats, "live_subagent_count", 0) or 0
                 for sess, _status, _activity, claude_stats, *_ in session_data}

    # Each agent's subscription quota estimate (the Q% column): the daemon's
    def _quota(sess):
        if not use_daemon:
            return None
        ds = daemon_state.get_session(sess.id)
        quota = getattr(ds, "quota", None) if ds is not None else None
        return quota if isinstance(quota, dict) else None

    quotas = {sess.id: _quota(sess) for sess, *_ in session_data}

    # Each agent's trip counter (trip.py): the daemon's, else from its record
    def _trip(sess):
        from ..trip import trip_view
        ds = daemon_state.get_session(sess.id) if use_daemon else None
        if ds is not None:
            return ds.trip, ds.subtree_trip_cost_usd
        return trip_view(sess, sess.stats), None

    trips = {sess.id: _trip(sess) for sess, *_ in session_data}

    # Which fleet-gated columns show, by the same rule as the TUI
    fleet = compute_fleet_flags(
        FleetRow(
            session=sess, status=status, pr_number=getattr(sess, 'pr_number', None),
            subtree_cost_usd=subtree_costs.get(sess.id, 0.0),
            has_status_detail=_get_status_detail(sess) is not None,
            has_swarm=swarms[sess.id] is not None,
            has_live_subagents=live_subs[sess.id] > 0,
            has_quota=quotas[sess.id] is not None,
        )
        for sess, status, *_ in session_data
    )

    # Heartbeat status legacy bridge (#TBD task 6)
    from ..hook_status_detector import augment_with_legacy_heartbeat

    import time
    from ..clock import to_epoch
    from ..config import get_idle_config
    from ..idle_stages import idle_since, staged_detail
    drowsy_after = get_idle_config()["drowsy_after"]

    def _augmented_detail(sess, status, idle_at=None):
        """The detail as the TUI shows it: 📥 turns 😴 after idle.drowsy_after."""
        detail = augment_with_legacy_heartbeat(_get_status_detail(sess), status)
        if idle_at is None:
            idle_at = idle_since(status, detail, to_epoch(sess.stats.state_since))
        return staged_detail(detail, idle_at, time.time(), drowsy_after)

    # Second pass: build contexts and collect cells for auto-alignment
    from ..summary_columns import COLUMNS_BY_ID
    row_contexts = []
    activities = []
    for sess, status, activity, claude_stats, git_diff, git_untracked in session_data:
        meta = tree_meta.get(sess.id)
        child_count = meta.child_count if meta else 0

        # Get per-session daemon fields
        oversight_deadline = None
        in_session_mode = ""
        idle_at = None
        title_lag = None
        if use_daemon:
            ds = daemon_state.get_session(sess.id)
            if ds:
                oversight_deadline = ds.oversight_deadline
                in_session_mode = ds.in_session_mode
                idle_at = ds.idle_since
                title_lag = ds.cli_title_lag

        _, status_color = get_status_symbol(status)
        ctx = build_cli_context(
            session=sess, stats=sess.stats,
            claude_stats=claude_stats, git_diff_stats=git_diff,
            status=status, bg_bash_count=0, live_sub_count=live_subs[sess.id],
            git_untracked_count=git_untracked,
            in_session_mode=in_session_mode,
            fleet=fleet, child_count=child_count,
            status_detail=_augmented_detail(sess, status, idle_at),
            oversight_deadline=oversight_deadline,
            pr_number=getattr(sess, 'pr_number', None),
            summary_detail=detail,
            has_sisters=has_sisters,
            local_hostname=local_hostname,
            max_name_width=name_width,
            max_repo_width=max_repo_width,
            max_branch_width=max_branch_width,
            all_names_match_repos=all_names_match_repos,
            subtree_cost_usd=subtree_costs.get(sess.id, 0.0),
            child_rollup=child_rollup(children.get(sess.id, ())),
            swarm_rollup=swarms[sess.id],
            quota=quotas[sess.id],
            cli_title_lag=title_lag,
            trip=trips[sess.id][0],
            subtree_trip_cost_usd=trips[sess.id][1],
        )
        ctx.status_color = f"bold {status_color}"
        ctx.show_cost = "joules" if joules else ("cost" if cost else "tokens")
        ctx.is_list_mode = True

        # Handle tree indentation (#244) using compute_tree_metadata
        depth = meta.depth if meta else 0
        indent = "  " * depth
        marker = name_marker(sess.name_source)
        pending = title_pending_mark(title_lag)
        available = name_width - len(indent) - len(marker) - len(pending)
        ctx.display_name = (indent + marker + sess.name[:available] + pending).ljust(name_width)

        row_contexts.append(ctx)
        # A child's result follows its activity (the TUI's ``l`` result mode)
        result = result_text(*results[sess.id])
        activities.append(f"{activity}  {result[0]}" if result else activity)

    # Columns every row has the same value in say nothing: hide them, as
    # the TUI does, and note the shared values under the table
    from ..summary_columns import uniform_columns
    shared = uniform_columns(row_contexts)

    def col_filter(col):
        return detail in col.detail_levels and col.id not in shared

    all_cells = [render_summary_cells(ctx, column_filter=col_filter) for ctx in row_contexts]

    # Auto-align columns across all rows, then append activity
    widths = compute_column_widths(all_cells)
    header = render_header_cells(column_filter=col_filter, column_widths=widths)
    header.truncate(console.width, pad=False)
    console.print(header, no_wrap=True)
    aligned_lines = [pad_and_join_cells(row, widths) for row in all_cells]
    for line, activity in zip(aligned_lines, activities):
        line.append(" │ ", style="dim")
        line.append(activity)
        line.truncate(console.width, pad=False)
        console.print(line, no_wrap=True)
    notes = [f"{COLUMNS_BY_ID[cid].header or cid} {value}" for cid, value in shared.items()
             if value and detail in COLUMNS_BY_ID[cid].detail_levels]
    if notes:
        console.print("all: " + " · ".join(notes), style="dim", no_wrap=True)

    # Warn if the terminal is too narrow to render the data columns — the
    # right side is silently truncated otherwise, hiding metrics (#459).
    data_width = sum(widths)
    if data_width > console.width:
        next_level = "med" if detail == "full" else "low"
        suggest = f" Use --{next_level} for fewer columns." if detail != "low" else ""
        rprint(
            f"\n[yellow]⚠ Terminal is {console.width} cols; "
            f"{data_width} needed for all columns — output truncated."
            f"{suggest} Widen the terminal to see the rest.[/yellow]"
        )

    if terminated_count > 0:
        rprint(f"\n[dim]{terminated_count} terminated session(s). Run 'overcode cleanup' to remove.[/dim]")

    # Hint about untracked tmux windows (#344)
    if use_daemon and daemon_state.untracked_window_count > 0:
        n = daemon_state.untracked_window_count
        rprint(f"\n[dim]{n} untracked tmux window(s). Run 'overcode cleanup --untracked' to remove.[/dim]")


@app.command()
def attach(
    name: Annotated[Optional[str], typer.Argument(help="Agent name to focus on")] = None,
    bare: Annotated[
        bool,
        typer.Option("--bare", help="Minimal attach: no status bar, no prefix, mouse passthrough"),
    ] = False,
    session: SessionOption = "agents",
):
    """Attach to the tmux session to view agents.

    Optionally specify an agent name to jump directly to that agent's window.
    Use --bare for embedding in VSCode or other terminals (hides tmux chrome).
    """
    launcher = AgentLauncher(session)
    if bare:
        if not name:
            rprint("[red]Error:[/red] --bare requires an agent name")
            raise typer.Exit(1)
        rprint(f"[dim]Attaching to '{name}' (bare mode, close terminal to detach)...[/dim]")
    elif name:
        rprint(f"[dim]Attaching to '{name}'...[/dim]")
        rprint("[dim](Ctrl-b d to detach, Ctrl-b <number> to switch agents)[/dim]")
    else:
        rprint("[dim]Attaching to overcode...[/dim]")
        rprint("[dim](Ctrl-b d to detach, Ctrl-b <number> to switch agents)[/dim]")
    launcher.attach(name=name, bare=bare)


@app.command()
def kill(
    name: Annotated[str, typer.Argument(help="Name of agent to kill")],
    no_cascade: Annotated[
        bool,
        typer.Option("--no-cascade", help="Don't kill child agents (orphan them instead)"),
    ] = False,
    session: SessionOption = "agents",
):
    """Kill a running agent.

    By default, also kills all descendant (child) agents.
    Use --no-cascade to only kill the named agent and orphan its children.
    """
    launcher = AgentLauncher(session)
    # Not found, or its window would not die: a script's `kill x && ...`
    # must not carry on as if it had (kill_session printed why)
    if not launcher.kill_session(name, cascade=not no_cascade):
        raise typer.Exit(code=1)


@app.command()
def restart(
    name: Annotated[str, typer.Argument(help="Name of agent to restart")],
    fresh: Annotated[
        bool,
        typer.Option(
            "--fresh",
            help="Start a brand-new agent session instead of resuming the prior conversation.",
        ),
    ] = False,
    model: Annotated[
        Optional[str],
        typer.Option("--model", "-m", help="Relaunch on this model (kept for later restarts)"),
    ] = None,
    session: SessionOption = "agents",
):
    """Restart a running agent with the same configuration.

    Gracefully exits the agent CLI, then relaunches with the same full launch
    environment as a fresh `overcode launch` — telemetry injection,
    permissions, wrapper script, env prefix, model, persona, allowed tools,
    and extra CLI args.

    By default, resumes the agent's prior session so conversation
    history is preserved. Pass --fresh to start a brand-new session (useful
    when the old session is stuck or MCP configs have changed).

    If the agent's tmux window is gone (killed, or after a reboot), it is
    revived in a new window instead. `overcode revive --all` does this for
    every dead agent.

    The model is the one the agent last ran on (a /model switch is picked
    up from its transcript); --model picks another.
    """
    launcher = AgentLauncher(session)
    sess = find_agent(launcher.sessions, name)
    if not sess:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)
    # Found fleet-wide: its window is in its own tmux session, not --session's
    launcher = launcher.for_agent(sess)

    if model:
        launcher.sessions.update_session(sess.id, model=model)
        sess = launcher.sessions.get_session(sess.id)

    # Window gone (killed, reboot): bring it back in a new window, as the
    # TUI's R does (#481).
    from ..protocols import TmuxTimeoutError
    try:
        window_exists = launcher.tmux.window_exists(sess.tmux_window)
    except TmuxTimeoutError:
        rprint(f"[red]Failed to restart agent: {name}: tmux is not responding; "
               f"try again in a moment[/red]")
        raise typer.Exit(code=1)
    if not window_exists:
        from ..lifecycle import resume_mode

        mode, reason = resume_mode(sess, fresh)
        rprint(f"[dim]'{name}' has no tmux window; reviving...[/dim]")
        if launcher.revive(sess, fresh=fresh):
            why = f", {reason}" if reason and not fresh else ""
            rprint(f"[green]Revived agent: {name} ({'resumed' if mode == 'resume' else 'fresh'}{why})[/green]")
            return
        rprint(f"[red]Failed to revive agent: {name}[/red]")
        raise typer.Exit(code=1)

    rprint(f"[dim]Stopping '{name}'...[/dim]")
    if launcher.restart(sess, fresh=fresh):
        mode = "fresh" if fresh else "resumed"
        rprint(f"[green]Restarted agent: {name} ({mode})[/green]")
    else:
        rprint(f"[red]Failed to restart agent: {name}[/red]")
        raise typer.Exit(code=1)


@app.command()
def follow(
    name: Annotated[str, typer.Argument(help="Name of agent to follow")],
    session: SessionOption = "agents",
):
    """Follow an already-running agent, streaming its output (#244).

    Blocks until the agent reaches Stop. Press Ctrl-C to stop following
    (the agent keeps running in tmux).

    Examples:
        overcode follow my-child-agent
    """
    from ..follow_mode import follow_agent as _follow_agent

    exit_code = _follow_agent(name, session)
    raise typer.Exit(code=exit_code)


@app.command()
def rename(
    name: Annotated[str, typer.Argument(help="Current name of agent")],
    new_name: Annotated[
        Optional[str], typer.Argument(help="New name for the agent (not with --auto / --lock)")
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Pre-0.6 agents only (they restart): rename even mid-turn. "
                 "Ignored otherwise",
        ),
    ] = False,
    auto: Annotated[
        bool,
        typer.Option("--auto", help="Let overcode name this agent (keeps the name for now)"),
    ] = False,
    lock: Annotated[
        bool,
        typer.Option("--lock", help="Keep this name: overcode never renames it"),
    ] = False,
    session: SessionOption = "agents",
):
    """Rename an agent, keeping its conversation and history.

    The agent keeps running, even mid-turn: its window and record take the
    new name, and it is told its new name with its next prompt. The old
    name keeps working as an alias — `send`, `follow`, `kill` and the rest
    reach the agent and say it was renamed — so a parent or script still
    using it is not broken.

    An agent launched before 0.6 is briefly stopped and resumed under the
    new name instead (same window, same conversation). While it is busy
    that would cancel its turn, so it is refused unless --force.

    A rename locks the name: overcode's auto-naming never changes a name
    a person chose. --auto hands it back to overcode, and --lock locks
    the current name without changing it.

    Examples:
        overcode rename bugfix-1 auth-refactor
        overcode rename bugfix-1 --auto
    """
    from ..exceptions import AgentBusyError, InvalidSessionNameError
    from ..launcher import AgentLauncher
    from ..name_source import AUTO, HUMAN
    from ..protocols import TmuxTimeoutError

    if auto and lock:
        rprint("[red]Error: --auto and --lock are opposites; pass one[/red]")
        raise typer.Exit(code=1)
    if (auto or lock) == (new_name is not None):
        rprint("[red]Error: give a new name, or --auto / --lock (not both)[/red]")
        raise typer.Exit(code=1)

    launcher = AgentLauncher(session)
    sess = find_agent(launcher.sessions, name)
    if not sess:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)
    name = sess.name

    if auto or lock:
        launcher.sessions.update_session(sess.id, name_source=AUTO if auto else HUMAN)
        if auto:
            rprint(f"[green]'{name}' is named by overcode now[/green]"
                   f"{'' if _naming_enabled() else ' [dim](naming.enabled is off)[/dim]'}")
        else:
            rprint(f"[green]'{name}' keeps its name[/green]")
        return

    try:
        renamed = launcher.rename(sess, new_name, force=force, source="human")
    except InvalidSessionNameError:
        rprint(
            f"[red]Error: '{new_name}' is not a valid agent name "
            f"(letters, digits, - and _ only, max 64 chars)[/red]"
        )
        raise typer.Exit(code=1)
    except AgentBusyError as exc:
        state = (
            "could not be checked" if exc.status == "unknown" else f"is busy ({exc.status})"
        )
        rprint(
            f"[yellow]Agent '{name}' {state}.[/yellow] Renaming restarts it, "
            "which would cancel a turn in progress. Try again when it is idle, "
            "or use --force."
        )
        raise typer.Exit(code=1)
    except ValueError as exc:
        rprint(f"[red]Error: {exc}[/red]")
        raise typer.Exit(code=1)
    except TmuxTimeoutError:
        rprint(f"[red]Error: cannot rename '{name}': tmux is not responding; "
               f"try again in a moment[/red]")
        raise typer.Exit(code=1)

    # A person chose this name: the namer leaves it alone from now on
    _lock_if_renamed(launcher.sessions, sess.id, new_name)
    if not renamed:
        current = launcher.sessions.get_session(sess.id)
        if current is not None and current.name == new_name:
            rprint(
                f"[yellow]Renamed '{name}' → '{new_name}', but the relaunch could "
                f"not be sent.[/yellow] Run: overcode restart {new_name}"
            )
        else:
            rprint(
                f"[red]Error: tmux refused to rename the window; '{name}' keeps "
                "its name.[/red]"
            )
        raise typer.Exit(code=1)

    rprint(f"[green]Renamed agent: {name} → {new_name} (conversation preserved)[/green]")


@app.command()
def report(
    status: Annotated[
        str,
        typer.Option("--status", "-s", help="Report status: success or failure"),
    ],
    reason: Annotated[
        Optional[str],
        typer.Option("--reason", "-r", help="Reason for the report"),
    ] = None,
):
    """Report completion status from a child agent.

    Called by child agents to signal success or failure to the parent.
    Reads OVERCODE_SESSION_NAME and OVERCODE_TMUX_SESSION from env vars
    (automatically set for all child agents).

    Examples:
        overcode report --status success
        overcode report --status failure --reason "Tests failed"
    """
    import os
    import json
    from datetime import datetime

    if status not in ("success", "failure"):
        rprint(f"[red]Error: --status must be 'success' or 'failure', got '{status}'[/red]")
        raise typer.Exit(code=1)

    agent_name = os.environ.get("OVERCODE_SESSION_NAME")
    tmux_session = os.environ.get("OVERCODE_TMUX_SESSION")

    if not agent_name or not tmux_session:
        rprint("[red]Error: OVERCODE_SESSION_NAME and OVERCODE_TMUX_SESSION env vars required[/red]")
        rprint("[dim]This command should be run from within a child agent launched by overcode[/dim]")
        raise typer.Exit(code=1)

    # Write the report in the agent's own directory (agents/<id>/, item 35)
    from ..hook_handler import agent_file_for_write
    report_file = agent_file_for_write(
        tmux_session, agent_name, "report", os.environ.get("OVERCODE_SESSION_ID") or None)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_data = {
        "status": status,
        "reason": reason or "",
        "timestamp": datetime.now().isoformat(),
    }
    report_file.write_text(json.dumps(report_data, indent=2))

    # Also update session fields for persistence: the agent by its id (its
    # launch name is stale after a rename), by name only for a pre-0.6 agent
    from ..agent_identity import current_agent
    from ..session_manager import SessionManager
    sm = SessionManager()
    session = current_agent(sm)
    if session:
        sm.update_session(
            session.id,
            report_status=status,
            report_reason=reason or "",
        )

    rprint(f"[green]✓[/green] Report filed: {status}" + (f" ({reason})" if reason else ""))


@app.command()
def cleanup(
    done: Annotated[
        bool, typer.Option("--done", help="Also archive 'done' child agents (#244)")
    ] = False,
    untracked: Annotated[
        bool, typer.Option("--untracked", help="Kill tmux windows not tracked by any agent (#344)")
    ] = False,
    session: SessionOption = "agents",
):
    """Remove terminated sessions from tracking.

    Terminated sessions are those whose tmux window no longer exists
    (e.g., after a machine reboot). Use 'overcode list' to see them.

    Use --done to also archive done child agents (kill tmux window, move to archive).
    Use --untracked to kill tmux windows that exist but aren't tracked by any agent.
    """
    launcher = AgentLauncher(session)
    _reconcile_without_daemon(launcher, session)
    count, done_count = _get_sessions_to_cleanup(launcher, done)

    # Kill untracked tmux windows (#344)
    if untracked:
        killed = launcher.kill_untracked_windows()
        for w_name in killed:
            print(f"Killing untracked window {w_name}")
        if killed:
            print(f"Killed {len(killed)} untracked window(s)")

    total = count + done_count
    if total > 0:
        parts = []
        if count > 0:
            parts.append(f"{count} terminated")
        if done_count > 0:
            parts.append(f"{done_count} done")
        rprint(f"[green]✓ Cleaned up {' + '.join(parts)} session(s)[/green]")
    elif not untracked:
        rprint("[dim]No sessions to clean up[/dim]")


@app.command(name="set-value")
def set_value(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    value: Annotated[int, typer.Argument(help="Priority value (default 1000, higher = more important)")],
    session: SessionOption = "agents",
):
    """Set agent priority value for sorting (#61).

    Higher values indicate higher priority. Default is 1000.

    Examples:
        overcode set-value my-agent 2000    # High priority
        overcode set-value my-agent 500     # Low priority
        overcode set-value my-agent 1000    # Reset to default
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = find_agent(manager, name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    manager.set_agent_value(agent.id, value)
    rprint(f"[green]✓ Set {name} value to {value}[/green]")


@app.command(name="set-budget", hidden=True)
def set_budget(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    budget: Annotated[float, typer.Argument(help="Budget in USD (0 to clear)")],
    session: SessionOption = "agents",
):
    """[Deprecated] Use 'overcode budget set' instead.

    Set cost budget for an agent (#173).
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = find_agent(manager, name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    if budget < 0:
        rprint("[red]Error: Budget cannot be negative[/red]")
        raise typer.Exit(code=1)

    manager.set_cost_budget(agent.id, budget)
    if budget > 0:
        rprint(f"[green]✓ Set {name} budget to ${budget:.2f}[/green]")
    else:
        rprint(f"[green]✓ Cleared budget for {name}[/green]")


@app.command()
def annotate(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    text: Annotated[
        Optional[List[str]], typer.Argument(help="Annotation text (omit to clear)")
    ] = None,
    session: SessionOption = "agents",
):
    """Set or clear a human annotation on an agent (#223).

    Allows programmatic annotation of agents so scripts and other tools
    can communicate status to the overcode TUI.

    Examples:
        overcode annotate my-agent "Working on auth module"
        overcode annotate my-agent Building the API layer
        overcode annotate my-agent                           # Clear annotation
    """
    from ..session_manager import SessionManager

    manager = SessionManager()
    agent = find_agent(manager, name)
    if not agent:
        rprint(f"[red]Error: Agent '{name}' not found[/red]")
        raise typer.Exit(code=1)

    annotation = " ".join(text) if text else ""
    manager.set_human_annotation(agent.id, annotation)
    if annotation:
        rprint(f"[green]✓ Annotation set for {name}:[/green] {annotation}")
    else:
        rprint(f"[green]✓ Annotation cleared for {name}[/green]")


@app.command()
def send(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    text: Annotated[
        Optional[List[str]],
        typer.Argument(help="Text to send (or a gesture: approve, reject, enter, escape)"),
    ] = None,
    no_enter: Annotated[
        bool, typer.Option("--no-enter", help="Don't press Enter after text")
    ] = False,
    session: SessionOption = "agents",
):
    """
    Send input to an agent.

    Gestures: approve, reject -- resolved via the agent's backend, so the
    right keys go to a Claude Code prompt or an opencode one.

    Special keys (raw): enter, escape, tab, up, down, left, right

    Examples:
        overcode send my-agent "yes"           # Send "yes" + Enter
        overcode send my-agent approve         # Approve a permission prompt
        overcode send my-agent reject          # Reject a permission prompt
        overcode send my-agent enter           # Just press Enter (raw)
        overcode send my-agent --no-enter "y"  # Send "y" without Enter
    """
    from ..session_manager import SessionManager
    sm = SessionManager()

    # Auto-wake sleeping agent (#168)
    agent_session = find_agent(sm, name)
    if agent_session:
        name = agent_session.name  # an old name resolved; say the current one
    if agent_session and agent_session.is_asleep:
        sm.update_session(agent_session.id, is_asleep=False, asleep_auto=False)
        rprint(f"[dim]Woke agent '{name}' to send command[/dim]")

    launcher = AgentLauncher(session)

    # Join all text parts if multiple were given
    text_str = " ".join(text) if text else ""
    enter = not no_enter

    if launcher.send_to_session(name, text_str, enter=enter):
        if text_str.lower() in ("enter", "escape", "esc", "approve", "reject"):
            rprint(f"[green]✓[/green] Sent {text_str.upper()} to '[bold]{name}[/bold]'")
        elif enter:
            display = text_str[:50] + "..." if len(text_str) > 50 else text_str
            rprint(f"[green]✓[/green] Sent to '[bold]{name}[/bold]': {display}")
        else:
            display = text_str[:50] + "..." if len(text_str) > 50 else text_str
            rprint(f"[green]✓[/green] Sent (no enter) to '[bold]{name}[/bold]': {display}")
    else:
        rprint(f"[red]✗[/red] Failed to send to '[bold]{name}[/bold]'")
        raise typer.Exit(1)


@app.command()
def show(
    name: Annotated[str, typer.Argument(help="Name of agent")],
    lines: Annotated[
        int, typer.Option("--lines", "-n", help="Number of lines to show")
    ] = 50,
    no_stats: Annotated[
        bool, typer.Option("--no-stats", help="Skip stats, show only pane output")
    ] = False,
    stats_only: Annotated[
        bool, typer.Option("--stats-only", "-s", help="Show only stats, no pane output")
    ] = False,
    color: Annotated[
        bool, typer.Option("--color", "-c", help="Preserve ANSI colors in pane output")
    ] = False,
    session: SessionOption = "agents",
):
    """Show agent details and recent output."""
    from ..status_patterns import strip_ansi
    from ..summary_columns import build_cli_context, render_cli_stats
    from ..monitor_daemon_state import get_monitor_daemon_state

    launcher = AgentLauncher(session)

    # Get the Session object
    sess = find_agent(launcher.sessions, name)
    if sess is None:
        # Archived: its record (and whether its own files are kept)
        archived = launcher.sessions.find_archived(name)
        if archived is not None:
            from .monitoring import print_archived_session
            print_archived_session(archived, files=True)
            return
        rprint(f"[red]✗[/red] Agent '[bold]{name}[/bold]' not found")
        raise typer.Exit(1)
    name = sess.name  # an old name resolved; show under the current one
    # Found fleet-wide: its daemon state and window are in its own tmux session
    session = sess.tmux_session or session

    # Read daemon state for status/activity (single source of truth)
    daemon_state = get_monitor_daemon_state(session)
    daemon_session = None
    if daemon_state and not daemon_state.is_stale():
        daemon_session = daemon_state.get_session(sess.id)

    # Get status/activity from daemon state, falling back to detection
    pane_content_raw = ""
    if sess.status == "terminated":
        status = "terminated"
        activity = "(tmux window no longer exists)"
    elif daemon_session:
        status = daemon_session.current_status
        activity = daemon_session.current_activity
    else:
        # Daemon not running — fall back to direct detection
        from ..status_detector_factory import (
            create_status_detector,
            resolve_session_detection_mode,
        )
        from ..status_patterns import get_patterns
        from ..backends import session_backend_name
        from ..settings import resolve_detection_mode
        detector = create_status_detector(
            session,
            strategy=resolve_session_detection_mode(
                sess, resolve_detection_mode(session)
            ),
            patterns=get_patterns(session_backend_name(sess)),
            backend_name=session_backend_name(sess),
        )
        status, activity, pane_content_raw = detector.detect_status(sess)

    if sess.is_asleep:
        status = "asleep"

    # Capture pane content separately if needed for display or stats parsing
    need_pane = (not stats_only and lines > 0) or not no_stats
    if need_pane and not pane_content_raw and sess.status != "terminated":
        from ..status_detector_factory import StatusDetectorDispatcher
        dispatcher = StatusDetectorDispatcher(session)
        pane_content_raw = dispatcher.get_pane_content(sess.tmux_window, num_lines=lines)

    if not no_stats:
        # Gather all stats
        stats_data = _gather_session_stats(sess, pane_content_raw)
        claude_stats = stats_data["claude_stats"]
        git_diff = stats_data["git_diff"]
        git_untracked = stats_data["git_untracked"]
        bg_bash_count = stats_data["bg_bash_count"]
        live_sub_count = stats_data["live_sub_count"]
        in_session_mode = stats_data["in_session_mode"]

        # AI summaries from daemon state
        ai_short = ""
        ai_context = ""
        if daemon_session:
            ai_short = daemon_session.activity_summary or ""
            ai_context = daemon_session.activity_summary_context or ""

        # Build context and render via column system
        ctx = build_cli_context(
            session=sess,
            stats=sess.stats,
            claude_stats=claude_stats,
            git_diff_stats=git_diff,
            status=status,
            bg_bash_count=bg_bash_count,
            live_sub_count=live_sub_count,
            git_untracked_count=git_untracked,
            in_session_mode=in_session_mode,
        )

        print(f"=== {name} ===")
        label_width = max(len(label) for label, _ in render_cli_stats(ctx)) + 1
        for label, value in render_cli_stats(ctx):
            print(f"{label + ':':<{label_width + 1}} {value}")

        # AI summaries (not a column — comes from daemon state)
        if ai_short:
            print(f"{'AI:':<{label_width + 1}} {ai_short}")
        if ai_context:
            print(f"{'Context:':<{label_width + 1}} {ai_context}")

        # Activity from status detector (not a column — transient)
        if activity:
            print(f"{'Activity:':<{label_width + 1}} {activity[:100]}")

        # Agent CLI flag passthrough (#290)
        if sess.allowed_tools:
            print(f"{'Tools:':<{label_width + 1}} {sess.allowed_tools}")
        if sess.extra_cli_args:
            print(f"{'CLI args:':<{label_width + 1}} {' '.join(sess.extra_cli_args)}")
        if sess.agent_persona:
            print(f"{'Agent:':<{label_width + 1}} {sess.agent_persona}")
        if sess.agent_teams:
            print(f"{'Teams:':<{label_width + 1}} enabled")
        if sess.wrapper:
            print(f"{'Wrapper:':<{label_width + 1}} {sess.wrapper}")
        from ..backends import get_backend, session_backend_name
        backend_name = session_backend_name(sess)
        try:
            backend_label = get_backend(backend_name).display_name
        except Exception:
            backend_label = backend_name
        print(f"{'Backend:':<{label_width + 1}} {backend_label} ({backend_name})")
        if sess.launcher_version:
            print(f"{'Launcher:':<{label_width + 1}} {sess.launcher_version}")

        print()

    # Pane output section (skip if --stats-only or --lines 0)
    if not stats_only and lines > 0:
        if pane_content_raw:
            if color:
                from rich.console import Console
                from rich.text import Text
                console = Console()
                raw_lines = pane_content_raw.rstrip().split('\n')
                display_lines = raw_lines[-lines:]
                console.print(f"=== {name} (last {lines} lines) ===")
                for line in display_lines:
                    console.print(Text.from_ansi(line))
                console.print(f"=== end {name} ===")
            else:
                clean_content = strip_ansi(pane_content_raw)
                content_lines = clean_content.rstrip().split('\n')
                display_lines = content_lines[-lines:]
                print(f"=== {name} (last {lines} lines) ===")
                print('\n'.join(display_lines))
                print(f"=== end {name} ===")
        else:
            # Fallback for terminated sessions
            output = launcher.get_session_output(name, lines=lines)
            if output is not None:
                print(f"=== {name} (last {lines} lines) ===")
                print(output)
                print(f"=== end {name} ===")
            else:
                rprint("[dim]No pane output available[/dim]")
