"""History, archived sessions, Parquet export (coverage matrix row 13).

`history` shows ARCHIVED sessions (no --session option); `export` takes the
output path positionally. `usage` needs a real Claude OAuth token, so tier 1
only asserts it degrades gracefully.
"""

import pytest

pytestmark = pytest.mark.timeout(180)


@pytest.fixture
def archived(oc, oc_wait, sandbox):
    """An agent that lived, died, and was archived via cleanup."""
    oc.launch("scribe", scenario="task_running")
    oc.start_monitor_daemon(interval=1)
    csv = oc.session_dir / "agent_status_history.csv"
    oc_wait(lambda: csv.exists() and "scribe" in csv.read_text(),
            timeout=60, desc="history CSV has rows")

    for w in sandbox.list_windows(oc.session):
        if w.startswith("scribe"):
            sandbox.cmd("kill-window", "-t", f"{oc.session}:{w}")
    oc_wait(
        lambda: not any(w.startswith("scribe") for w in sandbox.list_windows(oc.session)),
        desc="window killed",
    )

    def archived_now():
        oc.run("cleanup", timeout=30)
        return oc.agent("scribe") is None

    oc_wait(archived_now, timeout=60, desc="agent archived by cleanup")
    return oc


def test_history_lists_archived_session(archived):
    result = archived.run("history", session_arg=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scribe" in result.stdout


def test_usage_degrades_gracefully_without_oauth(oc):
    result = oc.run("usage", session_arg=False)
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined


def test_export_parquet(archived, tmp_path):
    out = tmp_path / "export.parquet"
    result = archived.run("export", str(out), session_arg=False, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.exists() and out.stat().st_size > 0

    import pyarrow.parquet as pq
    table = pq.read_table(out)
    assert table.num_rows > 0
