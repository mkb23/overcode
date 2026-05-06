"""
`overcode parallelism` — recommend a sensible max-children cap (#365).

Computes a safe parallelism budget from system resources and current agent
load. Useful as a guard before spawning more child agents from a parent.
"""

from typing import Annotated

import typer
from rich import print as rprint
from rich.table import Table

from ._shared import app, SessionOption


# Heuristics — kept conservative because Claude children can spike well
# above their idle footprint, and the user probably wants headroom for
# the foreground app + supervision processes.
_RESERVED_CORES = 1
_PER_CHILD_RAM_GB = 1.5
_PER_CHILD_CPU_FRACTION = 0.5  # assume each child saturates ~half a core sustained


def _system_cores() -> int:
    import os
    return os.cpu_count() or 1


def _system_ram_gb() -> float:
    try:
        import psutil  # type: ignore
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        # Fallback for systems without psutil — read sysconf if available.
        try:
            import os
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return (pages * page_size) / (1024 ** 3)
        except (AttributeError, ValueError, OSError):
            return 0.0


def _recommend_cap(cores: int, ram_gb: float) -> int:
    by_cpu = max(1, int((cores - _RESERVED_CORES) / _PER_CHILD_CPU_FRACTION))
    by_ram = max(1, int(ram_gb / _PER_CHILD_RAM_GB)) if ram_gb > 0 else by_cpu
    return min(by_cpu, by_ram)


@app.command()
def parallelism(
    session: SessionOption = "agents",
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit recommendation as JSON for agents to consume"),
    ] = False,
):
    """Recommend a max-children cap based on system resources and current load (#365).

    Looks at CPU cores, total RAM, and the in-progress agents' resource
    use, then prints a recommended ceiling and whether the current count
    is safe. Use it as a quick sanity-check before launching more
    children from a parent agent.
    """
    from ..launcher import ClaudeLauncher

    launcher = ClaudeLauncher(session)
    sessions = [
        s for s in launcher.list_sessions(detect_terminated=False)
        if s.status not in ("terminated", "done")
    ]

    cores = _system_cores()
    ram_gb = _system_ram_gb()
    recommended = _recommend_cap(cores, ram_gb)

    current_count = len(sessions)
    total_cpu_pct = sum((getattr(s, "cpu_percent", 0.0) or 0.0) for s in sessions)
    total_ram_bytes = sum((getattr(s, "rss_bytes", 0) or 0) for s in sessions)
    total_ram_gb = total_ram_bytes / (1024 ** 3)

    headroom = max(0, recommended - current_count)
    over = current_count > recommended

    if json_out:
        import json as _json
        rprint(_json.dumps({
            "cores": cores,
            "ram_gb": round(ram_gb, 2),
            "recommended_max_children": recommended,
            "current_count": current_count,
            "headroom": headroom,
            "over_limit": over,
            "current_total_cpu_pct": round(total_cpu_pct, 1),
            "current_total_ram_gb": round(total_ram_gb, 2),
        }))
        return

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_row("[bold]System[/bold]", f"{cores} cores · {ram_gb:.1f} GB RAM")
    table.add_row("[bold]Per-child budget[/bold]",
                  f"≤{_PER_CHILD_CPU_FRACTION:.1f} core · ~{_PER_CHILD_RAM_GB:.1f} GB RAM")
    table.add_row("[bold]Recommended max[/bold]", f"[green]{recommended}[/green] children")
    table.add_row("[bold]Currently active[/bold]",
                  f"{current_count} agents · {total_cpu_pct:.0f}% CPU · {total_ram_gb:.1f} GB RAM")
    if over:
        table.add_row("[bold]Verdict[/bold]",
                      f"[red]Over by {current_count - recommended}[/red] — consider letting some finish before spawning more.")
    elif headroom == 0:
        table.add_row("[bold]Verdict[/bold]", "[yellow]At cap[/yellow] — no headroom for new children.")
    else:
        table.add_row("[bold]Verdict[/bold]",
                      f"[green]OK[/green] — room for [bold]{headroom}[/bold] more children.")
    rprint(table)
