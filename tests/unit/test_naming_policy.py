"""The naming policy checker (#542, auto-naming.md §2a, §5): pure, no model."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from overcode.naming_policy import (
    RULE_ADVICE,
    RULE_BASE_PATTERN,
    RULE_FORBID,
    RULE_MAX_LENGTH,
    RULE_NO_REPO_PREFIX,
    RULE_PATTERN,
    RULE_RECENT_ARCHIVED,
    RULE_REQUIRE,
    RULE_RESERVED,
    RULE_TAKEN,
    NamingContext,
    NamingRules,
    check,
    choose,
    llm_check,
    load_advice,
    normalise,
    project_advice_path,
)

pytestmark = pytest.mark.unit


def _agent(name, id=None, previous=(), repo=None, remote=False):
    return SimpleNamespace(id=id or f"id-{name}", name=name, previous_names=list(previous),
                           repo_name=repo, is_remote=remote)


def _ctx(rules=None, sessions=(), **kw):
    return NamingContext.from_fleet(rules or NamingRules(), sessions, **kw)


def _rules(verdict):
    return [f.rule for f in verdict.failures]


class TestDefaultsArePermissive:

    @pytest.mark.parametrize("name", [
        "wiki1", "wiki2", "wiki3", "child-1", "test", "my_Agent", "tmp", "x",
        "a-very-long-name-that-says-a-lot-about-the-work", "overcode-main-fix",
    ])
    def test_reasonable_names_pass_with_no_rules(self, name):
        ctx = _ctx(sessions=[_agent("wiki1x"), _agent("wikis")], repo="overcode-main")
        assert check(name, ctx).ok

    def test_a_numbered_series_is_never_flagged(self):
        """wiki1, wiki2, wiki3 side by side: deliberate, not near-duplicates."""
        ctx = _ctx(sessions=[_agent("wiki1"), _agent("wiki2")])
        assert check("wiki3", ctx).ok
        assert check("api-fx", _ctx(sessions=[_agent("api-fix")])).ok

    def test_recent_archived_names_are_not_checked_by_default(self):
        archived = [{"id": "old", "name": "gone", "end_time": datetime.now().isoformat()}]
        assert check("gone", _ctx(archived=archived)).ok


class TestHardRules:

    @pytest.mark.parametrize("name", ["", "has space", "x" * 65, "ünï", "a/b"])
    def test_base_pattern(self, name):
        v = check(name, _ctx())
        assert RULE_BASE_PATTERN in _rules(v) and v.hard

    def test_reserved(self):
        v = check("daemon_claude", _ctx())
        assert _rules(v) == [RULE_RESERVED] and v.hard

    def test_a_live_name_is_taken(self):
        v = check("alpha", _ctx(sessions=[_agent("alpha")]))
        assert _rules(v) == [RULE_TAKEN] and v.hard
        assert "agent 'alpha'" in v.failures[0].message

    def test_a_live_alias_is_taken(self):
        v = check("old-name", _ctx(sessions=[_agent("new-name", previous=["old-name"])]))
        assert _rules(v) == [RULE_TAKEN]
        assert "old name of 'new-name'" in v.failures[0].message

    def test_an_agents_own_names_are_not_taken_for_itself(self):
        ctx = _ctx(sessions=[_agent("cur", id="me", previous=["was"])], self_id="me")
        assert check("was", ctx).ok

    def test_sister_agents_are_left_out(self):
        assert check("remote-one", _ctx(sessions=[_agent("remote-one", remote=True)])).ok


class TestOptionalRules:

    def test_pattern(self):
        v = check("Mixed_Case", _ctx(NamingRules(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")))
        assert _rules(v) == [RULE_PATTERN] and not v.hard
        assert v.suggestion == "mixed-case"

    def test_max_length_cuts_at_a_word(self):
        v = check("jwt-refresh-token-tests", _ctx(NamingRules(max_length=18)))
        assert _rules(v) == [RULE_MAX_LENGTH]
        assert v.suggestion == "jwt-refresh-token"

    def test_forbid_matches_whole_words_only(self):
        rules = NamingRules(forbid=("child", "test"))
        assert _rules(check("child-1", _ctx(rules))) == [RULE_FORBID]
        assert check("children-ui", _ctx(rules)).ok
        assert check("latest-fix", _ctx(rules)).ok
        assert check("auth-child", _ctx(rules)).suggestion == "auth"

    def test_require(self):
        rules = NamingRules(require=r"-[0-9]+$")
        v = check("fix-jwt", _ctx(rules))
        assert _rules(v) == [RULE_REQUIRE]
        assert v.suggestion is None  # an issue number can't be made up
        assert check("fix-jwt-542", _ctx(rules)).ok

    def test_no_repo_prefix_only_while_the_repo_column_shows(self):
        rules = NamingRules(no_repo_prefix=True)
        one_repo = _ctx(rules, sessions=[_agent("a", repo="overcode")], repo="overcode")
        assert check("overcode-fix", one_repo).ok  # the column is hidden: one repo
        two_repos = _ctx(rules, sessions=[_agent("a", repo="other")], repo="overcode")
        v = check("overcode-fix", two_repos)
        assert _rules(v) == [RULE_NO_REPO_PREFIX]
        assert v.suggestion == "fix"
        assert check("overcoder", two_repos).ok

    def test_recent_archived(self):
        now = datetime(2026, 10, 20, 12, 0)
        archived = [
            {"id": "a", "name": "fresh", "previous_names": ["fresh-old"],
             "end_time": (now - timedelta(hours=2)).isoformat()},
            {"id": "b", "name": "stale", "end_time": (now - timedelta(hours=30)).isoformat()},
        ]
        ctx = _ctx(NamingRules(recent_archived_hours=24), archived=archived, now=now)
        assert _rules(check("fresh", ctx)) == [RULE_RECENT_ARCHIVED]
        assert _rules(check("fresh-old", ctx)) == [RULE_RECENT_ARCHIVED]
        assert check("stale", ctx).ok

    def test_force_name_gets_past_soft_rules_only(self):
        rules = NamingRules(forbid=("tmp",))
        assert not check("tmp", _ctx(rules)).hard
        assert check("tmp", _ctx(rules, sessions=[_agent("tmp")])).hard


class TestSuggestions:

    def test_a_taken_numbered_name_gets_the_next_number(self):
        ctx = _ctx(sessions=[_agent("wiki1"), _agent("wiki2")])
        assert check("wiki1", ctx).suggestion == "wiki3"

    def test_a_taken_name_gets_a_dash_number(self):
        ctx = _ctx(sessions=[_agent("auth"), _agent("auth-2")])
        assert check("auth", ctx).suggestion == "auth-3"

    def test_the_suggestion_stays_within_max_length(self):
        rules = NamingRules(max_length=8)
        ctx = _ctx(rules, sessions=[_agent("abcdefgh")])
        s = check("abcdefgh", ctx).suggestion
        assert s is not None and len(s) <= 8 and check(s, ctx).ok

    def test_message_names_the_rule_the_advice_and_the_override(self):
        v = check("child-1", _ctx(NamingRules(forbid=("child",))))
        text = v.message("name the work")
        assert "forbidden word 'child'" in text
        assert "--force-name" in text
        assert "name the work" in text
        hard = check("alpha", _ctx(sessions=[_agent("alpha")])).message()
        assert "--force-name" not in hard

    def test_to_dict(self):
        d = check("alpha", _ctx(sessions=[_agent("alpha")])).to_dict()
        assert d["ok"] is False and d["hard"] is True
        assert d["failures"][0]["rule"] == RULE_TAKEN
        assert d["suggestion"] == "alpha-2"


class TestChoose:

    def test_normalises_and_takes_the_first_survivor(self):
        rules = NamingRules(max_length=10)
        ctx = _ctx(rules, sessions=[_agent("jwt-fix")])
        assert choose(["JWT Fix", "Auth Refresh Flow", "jwt_retry"], ctx) == "jwt-retry"

    def test_none_survive_disambiguates_the_first(self):
        ctx = _ctx(sessions=[_agent("jwt-fix")])
        assert choose(["jwt-fix"], ctx) == "jwt-fix-2"

    def test_a_pick_always_passes_check(self):
        rules = NamingRules(max_length=12, forbid=("agent",), pattern=r"^[a-z0-9-]+$")
        ctx = _ctx(rules, sessions=[_agent("db-migrate"), _agent("db-migrate-2")])
        pick = choose(["DB migrate", "agent", "the-db-migration-work"], ctx)
        assert pick is not None and check(pick, ctx).ok

    def test_nothing_usable(self):
        assert choose(["", "!!!", None], _ctx()) is None

    def test_normalise(self):
        assert normalise("  JWT__Refresh  Tests! ") == "jwt-refresh-tests"


class TestAdvice:

    def test_config_then_project(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".overcode").mkdir()
        (repo / ".overcode" / "naming.md").write_text("team: prefix with the area\n")
        sub = repo / "src" / "pkg"
        sub.mkdir(parents=True)
        assert project_advice_path(str(sub)) == repo / ".overcode" / "naming.md"
        assert load_advice("kebab-case", str(sub)) == "kebab-case\n\nteam: prefix with the area"

    def test_stops_at_the_repo_root(self, tmp_path):
        (tmp_path / ".overcode").mkdir()
        (tmp_path / ".overcode" / "naming.md").write_text("outer")
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        assert project_advice_path(str(repo)) is None
        assert load_advice("", str(repo)) == ""


class _Client:
    def __init__(self, reply=None, error=None, available=True):
        self.reply, self.error, self.available = reply, error, available
        self.calls = []

    def complete(self, prompt, max_tokens, timeout):
        self.calls.append((prompt, timeout))
        if self.error:
            raise self.error
        return self.reply


class TestLlmCheck:

    def test_a_failing_reply(self):
        client = _Client('{"ok": false, "why": "names the tool", "suggestion": "Auth Fix"}')
        failure, pick = llm_check("claude-1", "name the work", client=client)
        assert failure.rule == RULE_ADVICE and "names the tool" in failure.message
        assert pick == "auth-fix"
        assert "claude-1" in client.calls[0][0] and "name the work" in client.calls[0][0]

    @pytest.mark.parametrize("client", [
        _Client('{"ok": true}'),
        _Client("no json here"),
        _Client(None),
        _Client(error=TimeoutError()),
        _Client(available=False),
    ])
    def test_fails_open(self, client):
        assert llm_check("x", "advice", client=client) is None

    def test_no_advice_no_call(self):
        client = _Client('{"ok": false}')
        assert llm_check("x", "  ", client=client) is None
        assert client.calls == []
