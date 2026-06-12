"""Tags, annotations, priority value (coverage matrix row 15)."""

import pytest

pytestmark = pytest.mark.timeout(120)


@pytest.fixture
def agent(oc, oc_wait):
    oc.launch("meta", scenario="startup_idle")
    oc_wait(lambda: oc.agent("meta"), desc="agent in registry")
    return "meta"


def test_tag_untag_roundtrip(oc, agent):
    oc.ok("tag", agent, "urgent", "frontend")
    assert set(oc.agent(agent)["tags"]) >= {"urgent", "frontend"}

    result = oc.ok("tags", agent)
    assert "urgent" in result.stdout

    oc.ok("untag", agent, "urgent")
    tags = oc.agent(agent)["tags"]
    assert "urgent" not in tags
    assert "frontend" in tags


def test_annotate_roundtrip(oc, agent):
    oc.ok("annotate", agent, "reviewing", "the", "auth", "flow")
    assert "auth" in str(oc.agent(agent))

    # Clearing: annotate with no text
    oc.ok("annotate", agent)
    assert "auth" not in str(oc.agent(agent))


def test_set_value_changes_priority(oc, agent):
    oc.ok("set-value", agent, "2000")
    agent_data = oc.agent(agent)
    assert any(v == 2000 for v in agent_data.values()), agent_data
