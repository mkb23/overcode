"""
Data export functionality for Overcode.

Exports session data to Parquet format for analysis in Jupyter notebooks.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from .session_manager import SessionManager
from .status_history import read_agent_status_history
from .presence_logger import read_presence_history


def export_to_parquet(
    output_path: str,
    include_archived: bool = True,
    include_timeline: bool = True,
    include_presence: bool = True,
) -> Dict[str, Any]:
    """Export overcode data to Parquet format.

    Creates a multi-table parquet file suitable for pandas analysis.

    Args:
        output_path: Path to output parquet file
        include_archived: Include archived sessions
        include_timeline: Include agent status timeline
        include_presence: Include user presence data

    Returns:
        Dict with counts of exported data

    Raises:
        ImportError: If pyarrow is not installed
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        raise ImportError(
            "pyarrow is required for parquet export. "
            "Install it with: pip install pyarrow"
        )

    sessions = SessionManager()
    result = {
        "sessions_count": 0,
        "archived_count": 0,
        "timeline_rows": 0,
        "presence_rows": 0,
    }

    # Collect session data
    session_records = []

    # Active sessions
    for s in sessions.list_sessions():
        record = _session_to_record(s, is_archived=False)
        session_records.append(record)

    result["sessions_count"] = len(session_records)

    # Archived sessions
    if include_archived:
        archived = sessions.list_archived_sessions()
        for s in archived:
            record = _session_to_record(s, is_archived=True)
            record["end_time"] = getattr(s, "_end_time", None)
            session_records.append(record)
        result["archived_count"] = len(archived)

    # Build sessions table
    sessions_table = _build_sessions_table(session_records)

    # Build timeline table
    timeline_table = None
    if include_timeline:
        timeline_records = _build_timeline_records()
        if timeline_records:
            timeline_table = _build_timeline_table(timeline_records)
            result["timeline_rows"] = len(timeline_records)

    # Build presence table
    presence_table = None
    if include_presence:
        presence_records = _build_presence_records()
        if presence_records:
            presence_table = _build_presence_table(presence_records)
            result["presence_rows"] = len(presence_records)

    # Write to parquet
    # Use a directory-based approach for multiple tables
    output = Path(output_path)
    if output.suffix != ".parquet":
        output = output.with_suffix(".parquet")

    # For simplicity, write sessions as the main table
    # with timeline and presence as separate files if requested
    pq.write_table(sessions_table, output)

    # Write additional tables as separate files
    if timeline_table is not None:
        timeline_path = output.with_stem(output.stem + "_timeline")
        pq.write_table(timeline_table, timeline_path)

    if presence_table is not None:
        presence_path = output.with_stem(output.stem + "_presence")
        pq.write_table(presence_table, presence_path)

    return result


def _session_to_record(session, is_archived: bool) -> Dict[str, Any]:
    """Convert a Session to a flat dictionary record."""
    stats = session.stats
    return {
        "id": session.id,
        "name": session.name,
        "tmux_session": session.tmux_session,
        "tmux_window": session.tmux_window,
        "start_directory": session.start_directory,
        "start_time": session.start_time,
        "end_time": None,  # Set for archived
        "repo_name": session.repo_name,
        "branch": session.branch,
        "status": session.status,
        "is_archived": is_archived,
        "permissiveness_mode": session.permissiveness_mode,
        "standing_instructions": session.standing_instructions,
        "standing_instructions_preset": session.standing_instructions_preset,
        # Stats
        "interaction_count": stats.interaction_count,
        "estimated_cost_usd": stats.estimated_cost_usd,
        "total_tokens": stats.total_tokens,
        "input_tokens": stats.input_tokens,
        "output_tokens": stats.output_tokens,
        "cache_creation_tokens": stats.cache_creation_tokens,
        "cache_read_tokens": stats.cache_read_tokens,
        "steers_count": stats.steers_count,
        "last_activity": stats.last_activity,
        "current_task": stats.current_task,
        "current_state": stats.current_state,
        "state_since": stats.state_since,
        "green_time_seconds": stats.green_time_seconds,
        "non_green_time_seconds": stats.non_green_time_seconds,
        "last_stats_update": stats.last_stats_update,
    }


def _build_sessions_table(records):
    """Build a PyArrow table from session records."""
    import pyarrow as pa

    if not records:
        # Return empty table with schema
        schema = pa.schema([
            ("id", pa.string()),
            ("name", pa.string()),
            ("start_time", pa.string()),
            ("end_time", pa.string()),
            ("is_archived", pa.bool_()),
            ("interaction_count", pa.int64()),
            ("total_tokens", pa.int64()),
            ("estimated_cost_usd", pa.float64()),
            ("green_time_seconds", pa.float64()),
            ("non_green_time_seconds", pa.float64()),
        ])
        # Create empty arrays for each column
        empty_arrays = {field.name: pa.array([], type=field.type) for field in schema}
        return pa.table(empty_arrays, schema=schema)

    # Build arrays from records
    arrays = {}
    for key in records[0].keys():
        values = [r.get(key) for r in records]
        arrays[key] = values

    return pa.table(arrays)


def _build_timeline_records():
    """Build timeline records from agent status history."""
    records = []

    # Read last 24 hours of timeline data
    # Returns List[Tuple[datetime, agent, status, activity]]
    history = read_agent_status_history(hours=24.0)

    for ts, agent_name, status, activity in history:
        records.append({
            "timestamp": ts.isoformat() if isinstance(ts, datetime) else str(ts),
            "agent": agent_name,
            "status": status,
        })

    return records


def _build_timeline_table(records):
    """Build a PyArrow table from timeline records."""
    import pyarrow as pa

    if not records:
        schema = pa.schema([
            ("timestamp", pa.string()),
            ("agent", pa.string()),
            ("status", pa.string()),
        ])
        empty_arrays = {field.name: pa.array([], type=field.type) for field in schema}
        return pa.table(empty_arrays, schema=schema)

    arrays = {}
    for key in records[0].keys():
        values = [r.get(key) for r in records]
        arrays[key] = values

    return pa.table(arrays)


def _build_presence_records():
    """Build presence records from presence log."""
    records = []

    # Read last 24 hours of presence data
    history = read_presence_history(hours=24.0)

    for ts, state in history:
        records.append({
            "timestamp": ts.isoformat() if isinstance(ts, datetime) else str(ts),
            "state": state,
            "state_name": {1: "locked", 2: "inactive", 3: "active"}.get(state, "unknown"),
        })

    return records


def _build_presence_table(records):
    """Build a PyArrow table from presence records."""
    import pyarrow as pa

    if not records:
        schema = pa.schema([
            ("timestamp", pa.string()),
            ("state", pa.int64()),
            ("state_name", pa.string()),
        ])
        empty_arrays = {field.name: pa.array([], type=field.type) for field in schema}
        return pa.table(empty_arrays, schema=schema)

    arrays = {}
    for key in records[0].keys():
        values = [r.get(key) for r in records]
        arrays[key] = values

    return pa.table(arrays)
