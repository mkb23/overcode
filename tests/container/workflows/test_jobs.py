"""Background bash jobs (coverage matrix row 11).

`overcode bash` has no --session option, attaches by default (--no-follow),
and suffixes job names (writer -> writer-ab12) — tests parse the real name
from the launch output.
"""

import re

import pytest

pytestmark = pytest.mark.timeout(120)


def _launch_job(oc, command: str, name: str) -> str:
    result = oc.ok("bash", command, "-n", name, "--no-follow", session_arg=False)
    match = re.search(r"Job '([^']+)' launched", result.stdout)
    assert match, f"could not parse job name from: {result.stdout}"
    return match.group(1)


def test_bash_job_lifecycle(oc, oc_wait, tmp_path):
    marker = tmp_path / "job-output.txt"
    job = _launch_job(oc, f"echo done > {marker} && sleep 60", "writer")

    oc_wait(lambda: marker.exists() and "done" in marker.read_text(),
            desc="job wrote its marker file")

    listing = oc.ok("jobs", "list", session_arg=False)
    assert job in listing.stdout

    oc.ok("jobs", "kill", job, session_arg=False)
    oc.ok("jobs", "clear", session_arg=False)


def test_jobs_tail_shows_output(oc, oc_wait):
    job = _launch_job(oc, "echo tail-me-please && sleep 60", "tailer")
    # --lines exits immediately; default mode streams until the job ends
    oc_wait(
        lambda: "tail-me-please"
        in oc.run("jobs", "tail", job, "--lines", "50", session_arg=False).stdout,
        desc="jobs tail shows job output",
    )
    oc.ok("jobs", "kill", job, session_arg=False)
