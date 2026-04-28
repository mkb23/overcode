"""Tests for JumpModal filter logic (#420)."""

from overcode.tui_widgets.jump_modal import JumpCandidate, filter_candidates


def _cands():
    return [
        JumpCandidate(session_id="1", name="shirka2",       repo="shirka2",       branch="main"),
        JumpCandidate(session_id="2", name="overcode-main", repo="overcode-main", branch="main"),
        JumpCandidate(session_id="3", name="tapestry",      repo="tapestry",      branch="feature/x"),
        JumpCandidate(session_id="4", name="creative",      repo="creative",      branch="main"),
    ]


class TestFilterCandidates:
    def test_empty_query_returns_all(self):
        result = filter_candidates(_cands(), "")
        assert [c.session_id for c in result] == ["1", "2", "3", "4"]

    def test_substring_case_insensitive(self):
        result = filter_candidates(_cands(), "SHIRKA")
        assert [c.session_id for c in result] == ["1"]

    def test_partial_match(self):
        result = filter_candidates(_cands(), "over")
        assert [c.session_id for c in result] == ["2"]

    def test_no_match(self):
        assert filter_candidates(_cands(), "zzznope") == []

    def test_branch_match_when_name_doesnt(self):
        result = filter_candidates(_cands(), "feature")
        assert [c.session_id for c in result] == ["3"]

    def test_name_hits_rank_above_branch_hits(self):
        cands = _cands() + [
            JumpCandidate(session_id="5", name="widget",    repo="widget",    branch="main-attempt"),
            JumpCandidate(session_id="6", name="main-work", repo="main-work", branch="main"),
        ]
        result = filter_candidates(cands, "main")
        # name-match "main-work" should come before any of the branch-only matches
        ids = [c.session_id for c in result]
        assert ids.index("6") < ids.index("5")

    def test_preserves_order_within_group(self):
        cands = _cands()
        result = filter_candidates(cands, "")
        assert [c.session_id for c in result] == ["1", "2", "3", "4"]
