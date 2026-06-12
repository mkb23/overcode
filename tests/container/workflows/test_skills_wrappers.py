"""Skills, hooks, perms, wrappers install/status/uninstall (matrix row 12).

These write under the test-private HOME (~/.claude, ~/.overcode), so they
exercise the real install paths without touching anything shared.
"""

import pytest

pytestmark = pytest.mark.timeout(120)


def test_skills_install_status_uninstall(oc):
    install = oc.run("skills", "install", session_arg=False)
    assert install.returncode == 0, install.stdout + install.stderr

    skills_dir = oc.home / ".claude" / "skills"
    assert skills_dir.exists() and any(skills_dir.iterdir())

    status = oc.run("skills", "status", session_arg=False)
    assert status.returncode == 0

    uninstall = oc.run("skills", "uninstall", session_arg=False)
    assert uninstall.returncode == 0


def test_hooks_status_reports(oc):
    result = oc.run("hooks", "status", session_arg=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_perms_status_reports(oc):
    result = oc.run("perms", "status", session_arg=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_wrappers_list_and_install(oc):
    listing = oc.run("wrappers", "list", session_arg=False)
    assert listing.returncode == 0, listing.stdout + listing.stderr

    install = oc.run("wrappers", "install", session_arg=False)
    assert install.returncode == 0, install.stdout + install.stderr

    wrappers_dir = oc.overcode_dir / "wrappers"
    assert wrappers_dir.exists() and any(wrappers_dir.iterdir())


def test_custom_wrapper_wraps_launch(oc, oc_wait, tmp_path):
    """A wrapper script actually wraps the claude invocation (row 12)."""
    marker = tmp_path / "wrapper-ran.txt"
    wrapper = oc.overcode_dir / "wrappers" / "tattle"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(f"#!/bin/sh\necho wrapped > {marker}\nexec \"$@\"\n")
    wrapper.chmod(0o755)

    # Launch WITHOUT MOCK_SCENARIO: the launcher's mock branch would bypass
    # the wrapper (launcher.py:258-261). CLAUDE_COMMAND still points at the
    # mock, which the wrapper exec's directly via its shebang.
    oc.ok("launch", "-n", "wrapped", "--wrapper", "tattle")
    oc_wait(lambda: oc.agent("wrapped"), desc="wrapped agent in registry")
    oc_wait(lambda: marker.exists(), desc="wrapper script executed")
