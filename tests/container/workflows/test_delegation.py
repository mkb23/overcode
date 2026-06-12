"""Parent/child delegation, settings inheritance (#433), hierarchy (#244).

Coverage matrix row 4.
"""

import pytest

pytestmark = pytest.mark.timeout(180)


def test_child_links_to_parent(oc, oc_wait):
    oc.launch("parent", scenario="startup_idle")
    oc_wait(lambda: oc.agent("parent"), desc="parent in registry")

    oc.launch("child", "--parent", "parent", scenario="startup_idle")
    child = oc_wait(lambda: oc.agent("child"), desc="child in registry")

    parent = oc.agent("parent")
    assert child["parent_session_id"] == parent["id"]


def test_child_inherits_parent_settings(oc, oc_wait):
    """#433: provider/model/permission mode flow from parent unless overridden."""
    oc.launch("par-set", "--model", "haiku", "--provider", "web",
              scenario="startup_idle")
    oc_wait(lambda: oc.agent("par-set"), desc="parent in registry")

    oc.launch("kid-inherit", "--parent", "par-set", scenario="startup_idle")
    kid = oc_wait(lambda: oc.agent("kid-inherit"), desc="child in registry")
    assert kid["model"] == "haiku"
    assert kid["provider"] == "web"

    # Explicit override beats inheritance
    oc.launch("kid-override", "--parent", "par-set", "--model", "sonnet",
              scenario="startup_idle")
    kid2 = oc_wait(lambda: oc.agent("kid-override"), desc="override child in registry")
    assert kid2["model"] == "sonnet"


def test_cascade_kill_takes_children(oc, oc_wait, sandbox):
    oc.launch("ancestor", scenario="startup_idle")
    oc_wait(lambda: oc.agent("ancestor"), desc="ancestor in registry")
    oc.launch("descendant", "--parent", "ancestor", scenario="startup_idle")
    oc_wait(lambda: oc.agent("descendant"), desc="descendant in registry")

    oc.ok("kill", "ancestor")

    oc_wait(
        lambda: not any(
            w.startswith(("ancestor", "descendant"))
            for w in sandbox.list_windows(oc.session)
        ),
        desc="both windows gone after cascade kill",
    )


def test_no_cascade_orphans_child(oc, oc_wait, sandbox):
    oc.launch("solo-parent", scenario="startup_idle")
    oc_wait(lambda: oc.agent("solo-parent"), desc="parent in registry")
    oc.launch("survivor", "--parent", "solo-parent", scenario="startup_idle")
    oc_wait(lambda: oc.agent("survivor"), desc="child in registry")

    oc.ok("kill", "solo-parent", "--no-cascade")

    oc_wait(
        lambda: not any(
            w.startswith("solo-parent") for w in sandbox.list_windows(oc.session)
        ),
        desc="parent window gone",
    )
    assert any(
        w.startswith("survivor") for w in sandbox.list_windows(oc.session)
    ), "child should survive --no-cascade kill"
