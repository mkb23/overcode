"""Real Claude Code smoke set (coverage matrix row 17).

Outcome-based assertions only: files created, status transitions observed,
token counters non-zero. Haiku + tight prompts keep each run cheap; the
conftest cost fuse aborts the tier if estimated spend exceeds the cap.
"""

import pytest

from tests.container.real_llm.conftest import launch_real

pytestmark = pytest.mark.timeout(600)


def test_real_agent_completes_file_task(roc, roc_wait, tmp_path):
    workdir = tmp_path / "task"
    workdir.mkdir()

    launch_real(
        roc, "real-worker",
        "Create a file named done.txt containing exactly the word DONE. "
        "Do nothing else, then stop.",
        workdir,
    )
    roc_wait(lambda: roc.agent("real-worker"), desc="real agent in registry")

    done = workdir / "done.txt"
    roc_wait(lambda: done.exists() and "DONE" in done.read_text(),
            timeout=300, interval=2, desc="real claude created done.txt")


def test_real_agent_status_and_tokens_tracked(roc, roc_wait, tmp_path):
    workdir = tmp_path / "task"
    workdir.mkdir()
    roc.start_monitor_daemon(interval=2)

    launch_real(
        roc, "real-tracked",
        "Create a file named ping.txt containing pong. Then stop.",
        workdir,
    )

    # The daemon must see it running at some point, then settle to waiting
    roc_wait(
        lambda: roc.agent_status("real-tracked")
        in ("running", "waiting_user", "waiting_approval"),
        timeout=120, interval=2,
        desc="monitor daemon detects real agent",
    )
    roc_wait(lambda: (workdir / "ping.txt").exists(),
            timeout=300, interval=2, desc="task completed")
    roc_wait(
        lambda: roc.agent_status("real-tracked") in ("waiting_user", "terminated", "done"),
        timeout=180, interval=2,
        desc="agent settles after completing",
    )

    # Token/cost sync from real Claude history files
    roc_wait(
        lambda: roc.agent_daemon_state("real-tracked").get("input_tokens", 0) > 0
        or roc.agent_daemon_state("real-tracked").get("output_tokens", 0) > 0,
        timeout=120, interval=2,
        desc="non-zero token counters",
    )


def test_real_send_drives_followup_work(roc, roc_wait, tmp_path):
    workdir = tmp_path / "task"
    workdir.mkdir()

    launch_real(
        roc, "real-driven",
        "Create a file named first.txt containing 1. Then wait for instructions.",
        workdir,
    )
    roc_wait(lambda: (workdir / "first.txt").exists(),
            timeout=300, interval=2, desc="initial task done")

    roc.ok("send", "real-driven",
           "Now create a file named second.txt containing 2. Then stop.")
    roc_wait(lambda: (workdir / "second.txt").exists(),
            timeout=300, interval=2, desc="follow-up task done")
