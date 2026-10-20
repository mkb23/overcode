"""Where a new agent's billing code comes from, and collapsing codes into the default.

``billing.new_agent_code`` (name | folder | default) and
``billing.default_code`` in config.yaml; ``codes.collapse`` / ``unmerge``;
``overcode billing collapse`` / ``unmerge``. Synthetic records only.
"""

import json
import os
import subprocess
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.codes import CodeError, CodeRegistry
from overcode.config import (DEFAULT_BILLING_CODE, config_path, get_billing_config,
                             get_billing_defaults, save_billing_defaults)
from overcode.git_context import repo_name
from overcode.launcher import AgentLauncher
from overcode.ledger import collapsible
from overcode.session_manager import SessionManager
from overcode.tmux_manager import TmuxManager
from tests.tmux_doubles import MockTmux

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("OVERCODE_") and key not in ("OVERCODE_DIR", "OVERCODE_TMUX_SOCKET"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OVERCODE_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr("overcode.config.CONFIG_PATH", tmp_path / "config.yaml")
    with patch("overcode.launcher.require_tmux"), \
         patch("overcode.launcher.require_agent_cli"), \
         patch("overcode.launcher.time.sleep"):
        yield


@pytest.fixture
def sm():
    return SessionManager(skip_git_detection=True)


@pytest.fixture
def launcher(sm):
    return AgentLauncher("agents", TmuxManager("agents", tmux=MockTmux()), sm)


@pytest.fixture
def codes(tmp_path):
    return CodeRegistry(tmp_path / "ledger")


def _config(text: str) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _row(code, cost, t, sid=None, kind="final"):
    return {"t": t, "seq": 1, "sid": sid or f"s-{code[-6:]}-{t}", "name": "x", "code": code,
            "billing_code": "x", "seg": 0, "cost": cost, "tokens": 1, "tok_in": 0,
            "tok_out": 0, "tok_cache": 0, "base_cost": 0.0, "base_tokens": 0, "kind": kind}


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


# ── config ───────────────────────────────────────────────────────────


class TestConfig:
    def test_defaults(self):
        assert get_billing_defaults() == {"new_agent_code": "name",
                                          "default_code": DEFAULT_BILLING_CODE, "warnings": []}

    def test_values_and_no_unknown_key_warning(self):
        _config("billing:\n  new_agent_code: folder\n  default_code: ops\n  codes: [a]\n")
        assert get_billing_defaults()["new_agent_code"] == "folder"
        assert get_billing_defaults()["default_code"] == "ops"
        assert get_billing_config() == {"codes": ["a"], "warnings": []}

    @pytest.mark.parametrize("text", [
        "billing:\n  new_agent_code: repo\n  default_code: ''\n",
        "billing:\n  new_agent_code: [x]\n  default_code: {a: 1}\n",
        "billing: nonsense\n",
    ])
    def test_bad_values_warn_and_fall_back(self, text):
        _config(text)
        settings = get_billing_defaults()
        assert settings["new_agent_code"] == "name"
        assert settings["default_code"] == DEFAULT_BILLING_CODE
        if "nonsense" not in text:
            assert len(settings["warnings"]) == 2

    def test_save_keeps_the_shortlist_and_comments(self):
        _config("# mine\nbilling:\n  codes: [a, b]\nother: 1\n")
        save_billing_defaults("default", " ops ")
        text = config_path().read_text()
        assert "# mine" in text and "other: 1" in text
        assert get_billing_config()["codes"] == ["a", "b"]
        assert get_billing_defaults()["new_agent_code"] == "default"
        assert get_billing_defaults()["default_code"] == "ops"
        with pytest.raises(ValueError):
            save_billing_defaults("repo", "x")


# ── folder names ────────────────────────────────────────────────────


class TestRepoName:
    def test_repo_root_worktree_and_plain_folder(self, tmp_path):
        repo = tmp_path / "myproj"
        (repo / "src" / "deep").mkdir(parents=True)
        _git("init", "-q", cwd=repo)
        _git("commit", "-q", "--allow-empty", "-m", "x", cwd=repo)
        _git("worktree", "add", "-q", str(tmp_path / "wt" / "feature"), cwd=repo)
        assert repo_name(str(repo / "src" / "deep")) == "myproj"
        assert repo_name(str(tmp_path / "wt" / "feature")) == "myproj"
        plain = tmp_path / "loose"
        plain.mkdir()
        assert repo_name(str(plain)) == "loose"


# ── launch ──────────────────────────────────────────────────────────


class TestLaunchModes:
    def test_name_mode_is_the_default_and_makes_a_default_code(self, launcher, sm):
        agent = sm.get_session(launcher.launch(name="shirka").id)
        entry = sm.ledger.codes.get(agent.billing_code_id)
        assert entry["name"] == "shirka" and entry["source"] == "default"
        assert agent.billing_code == "shirka"

    def test_folder_mode_bills_to_the_repo_and_never_follows(self, launcher, sm, tmp_path):
        _config("billing:\n  new_agent_code: folder\n")
        repo = tmp_path / "proj"
        (repo / "sub").mkdir(parents=True)
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        a = sm.get_session(launcher.launch(name="one", start_directory=str(repo / "sub")).id)
        b = sm.get_session(launcher.launch(name="two", start_directory=str(repo)).id)
        assert a.billing_code == "proj" and a.billing_code_id == b.billing_code_id
        codes = sm.ledger.codes
        assert codes.get(a.billing_code_id)["source"] == "human"
        assert not codes.follow_agent_name(a.billing_code_id, "jwt-fix")

    def test_default_mode_creates_the_default_code_as_human(self, launcher, sm):
        _config("billing:\n  new_agent_code: default\n  default_code: ops\n")
        a = sm.get_session(launcher.launch(name="one").id)
        b = sm.get_session(launcher.launch(name="two").id)
        assert a.billing_code == b.billing_code == "ops"
        assert a.billing_code_id == b.billing_code_id
        assert sm.ledger.codes.get(a.billing_code_id)["source"] == "human"

    def test_the_flag_still_wins_and_children_inherit(self, launcher, sm):
        _config("billing:\n  new_agent_code: default\n")
        parent = sm.get_session(launcher.launch(name="boss", billing_code="client-x").id)
        assert parent.billing_code == "client-x"
        with patch.dict(os.environ, {"OVERCODE_SESSION_NAME": "boss"}):
            child = sm.get_session(launcher.launch(name="kid").id)
        assert child.billing_code_id == parent.billing_code_id

    def test_a_bad_mode_warns_and_uses_the_name(self, launcher, sm, capsys):
        _config("billing:\n  new_agent_code: repo\n")
        agent = sm.get_session(launcher.launch(name="shirka").id)
        assert agent.billing_code == "shirka"
        assert "new_agent_code" in capsys.readouterr().err


# ── registry ────────────────────────────────────────────────────────


class TestCollapseRegistry:
    def test_collapse_merges_into_the_default_created_as_human(self, codes):
        a, b = codes.resolve("scratch"), codes.resolve("try-1")
        target, folded = codes.collapse(["scratch", b], "general")
        assert codes.get(target)["name"] == "general" and codes.get(target)["source"] == "human"
        assert [cid for cid, _ in folded] == [a, b]
        assert codes.find("scratch") == target and codes.canonical(b) == target
        assert set(codes.get(target)["previous_names"]) == {"scratch", "try-1"}

    def test_the_default_code_cannot_be_collapsed(self, codes):
        a = codes.resolve("scratch")
        codes.resolve("general")
        with pytest.raises(CodeError):
            codes.collapse(["scratch", "general"], "general")
        assert codes.canonical(a) == a  # nothing merged

    def test_unmerge_restores_the_code_and_its_names(self, codes):
        api = codes.resolve("api")
        v2 = codes.resolve("api-v2")
        codes.rename("api-v2", "api-two")
        codes.merge("api-two", "api")
        assert codes.find("api-v2") == api
        code_id, name, into = codes.unmerge("api-two")
        assert (code_id, name, into) == (v2, "api-two", "api")
        assert codes.find("api-two") == v2 and codes.find("api-v2") == v2
        assert codes.get(api)["previous_names"] == []
        assert "merged_into" not in codes.get(v2)
        with pytest.raises(CodeError):
            codes.unmerge("api-two")  # not merged any more

    def test_unmerge_keeps_a_name_the_target_had_before(self, codes):
        api = codes.resolve("api")
        codes.rename("api", "api-main")
        codes.rename(codes.resolve("tmp"), "api")  # a new code called api
        codes.merge("api", "api-main")   # api-main already had "api" as a previous name
        codes.unmerge("api")
        assert codes.get(api)["previous_names"] == ["api"]

    def test_unmerge_onto_a_taken_name_gets_a_suffix(self, codes):
        a = codes.resolve("scratch")
        codes.collapse(["scratch"], "general")
        codes.rename(codes.resolve("other"), "scratch")  # a new code took the name since
        _, name, _ = codes.unmerge(a)
        assert name == "scratch~2" and codes.get(a)["name"] == "scratch~2"


class TestCollapsible:
    NOW = datetime(2026, 10, 20, 12, 0)

    def test_filters(self):
        old = self.NOW - timedelta(days=40)
        assert collapsible(0.5, 0, old, self.NOW, under=1.0, idle_days=30)
        assert not collapsible(1.0, 0, old, self.NOW, under=1.0)
        assert not collapsible(0.5, 1, old, self.NOW, idle_days=30)  # running: never idle
        assert not collapsible(0.5, 0, self.NOW - timedelta(days=3), self.NOW, idle_days=30)
        assert collapsible(0.0, 0, None, self.NOW, idle_days=30)    # never spent


# ── CLI ─────────────────────────────────────────────────────────────


class TestCollapseCli:
    def _fleet(self, sm):
        codes = sm.ledger.codes
        ids = {n: codes.resolve(n) for n in ("big", "tiny-old", "tiny-new", "general")}
        now = datetime.now()
        old = (now - timedelta(days=60)).isoformat(timespec="seconds")
        new = (now - timedelta(days=1)).isoformat(timespec="seconds")
        sm.ledger.append([_row(ids["big"], 50.0, old), _row(ids["tiny-old"], 0.2, old),
                          _row(ids["tiny-new"], 0.3, new)])
        return ids

    def test_dry_run_changes_nothing(self, sm):
        ids = self._fleet(sm)
        result = runner.invoke(app, ["billing", "collapse", "--under", "1", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "tiny-old" in result.output and "tiny-new" in result.output
        assert "big" not in result.output
        assert sm.ledger.codes.canonical(ids["tiny-old"]) == ids["tiny-old"]

    def test_filters_combine_and_ask_first(self, sm):
        ids = self._fleet(sm)
        result = runner.invoke(app, ["billing", "collapse", "--under", "1", "--idle", "30d"],
                               input="n\n")
        assert result.exit_code == 1
        assert sm.ledger.codes.canonical(ids["tiny-old"]) == ids["tiny-old"]
        result = runner.invoke(app, ["billing", "collapse", "--under", "1", "--idle", "30", "-y"])
        assert result.exit_code == 0, result.output
        codes = sm.ledger.codes
        assert codes.canonical(ids["tiny-old"]) == ids["general"]
        assert codes.canonical(ids["tiny-new"]) == ids["tiny-new"]
        totals = sm.ledger.named(sm.ledger.totals())
        assert totals["general"]["cost"] == pytest.approx(0.2)

    def test_named_codes_moves_live_agents_and_unmerge_leaves_them(self, sm):
        ids = self._fleet(sm)
        live = sm.create_session(name="w", tmux_session="agents", tmux_window="w-1", command=[],
                                 billing_code="big")
        result = runner.invoke(app, ["billing", "collapse", "big"])
        assert result.exit_code == 0, result.output
        assert sm.get_session(live.id).billing_code_id == ids["general"]
        assert sm.get_session(live.id).billing_code == "general"
        assert sm.ledger.named(sm.ledger.totals())["general"]["cost"] == pytest.approx(50.0)
        out = runner.invoke(app, ["billing", "codes", "--merged"]).output
        assert "merged into general" in out
        result = runner.invoke(app, ["billing", "unmerge", "big"])
        assert result.exit_code == 0, result.output
        assert "stay" in result.output
        totals = sm.ledger.named(sm.ledger.totals())
        assert totals["big"]["cost"] == pytest.approx(50.0)
        assert sm.get_session(live.id).billing_code_id == ids["general"]

    def test_refusals(self, sm):
        self._fleet(sm)
        assert runner.invoke(app, ["billing", "collapse"]).exit_code == 1
        assert runner.invoke(app, ["billing", "collapse", "general"]).exit_code == 1
        assert runner.invoke(app, ["billing", "collapse", "nope"]).exit_code == 1
        assert runner.invoke(app, ["billing", "collapse", "big", "--idle", "x"]).exit_code == 1
        assert runner.invoke(app, ["billing", "unmerge", "big"]).exit_code == 1

    def test_the_configured_default_code(self, sm):
        _config("billing:\n  default_code: ops\n")
        ids = self._fleet(sm)
        assert runner.invoke(app, ["billing", "collapse", "tiny-old"]).exit_code == 0
        ops = sm.ledger.codes.find("ops")
        assert sm.ledger.codes.canonical(ids["tiny-old"]) == ops
        data = json.loads(runner.invoke(app, ["billing", "codes", "--json"]).output)
        assert any(c["name"] == "ops" and c["source"] == "human" for c in data)
