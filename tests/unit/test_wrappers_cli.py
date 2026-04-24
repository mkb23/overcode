"""Tests for the wrappers CLI commands (list, install, reset)."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from overcode.cli import app
from overcode.wrapper import BUNDLED_WRAPPERS


runner = CliRunner()


@pytest.fixture
def wrappers_dir(tmp_path, monkeypatch):
    """Point OVERCODE_DIR at a tmp dir so wrappers land in tmp/wrappers/."""
    monkeypatch.setenv("OVERCODE_DIR", str(tmp_path))
    return tmp_path / "wrappers"


class TestWrappersList:

    def test_empty_shows_available(self, wrappers_dir):
        result = runner.invoke(app, ["wrappers", "list"])
        assert result.exit_code == 0
        # Nothing installed yet, but bundled names should still appear under
        # "Available" — users need to know what names auto-install.
        assert "Available" in result.output
        for filename in BUNDLED_WRAPPERS:
            stem = Path(filename).stem
            assert stem in result.output

    def test_lists_installed_as_bundled(self, wrappers_dir):
        # Install first so entries show up under Installed
        runner.invoke(app, ["wrappers", "install"])
        result = runner.invoke(app, ["wrappers", "list"])
        assert result.exit_code == 0
        assert "Installed" in result.output
        assert "(bundled)" in result.output

    def test_detects_modified(self, wrappers_dir):
        runner.invoke(app, ["wrappers", "install"])
        # Mutate one
        victim = wrappers_dir / next(iter(BUNDLED_WRAPPERS))
        victim.write_text("#!/bin/bash\n# tampered")

        result = runner.invoke(app, ["wrappers", "list"])
        assert result.exit_code == 0
        assert "modified" in result.output

    def test_custom_tag_for_non_bundled(self, wrappers_dir):
        wrappers_dir.mkdir(parents=True)
        custom = wrappers_dir / "my-custom"
        custom.write_text("#!/bin/bash\nexec \"$@\"")
        custom.chmod(0o755)

        result = runner.invoke(app, ["wrappers", "list"])
        assert result.exit_code == 0
        assert "my-custom" in result.output
        assert "(custom)" in result.output


class TestWrappersInstall:

    def test_installs_all_bundled(self, wrappers_dir):
        result = runner.invoke(app, ["wrappers", "install"])
        assert result.exit_code == 0
        assert "Installed" in result.output
        for filename in BUNDLED_WRAPPERS:
            f = wrappers_dir / filename
            assert f.exists()
            # Wrappers must be executable to be usable at launch.
            assert f.stat().st_mode & 0o111

    def test_idempotent_second_run(self, wrappers_dir):
        runner.invoke(app, ["wrappers", "install"])
        result = runner.invoke(app, ["wrappers", "install"])
        assert result.exit_code == 0
        assert "Unchanged" in result.output

    def test_updates_modified(self, wrappers_dir):
        runner.invoke(app, ["wrappers", "install"])
        filename = next(iter(BUNDLED_WRAPPERS))
        (wrappers_dir / filename).write_text("#!/bin/bash\n# was modified")

        result = runner.invoke(app, ["wrappers", "install"])
        assert result.exit_code == 0
        assert "Updated" in result.output
        # Content should now match the bundled version again.
        assert (wrappers_dir / filename).read_text() == BUNDLED_WRAPPERS[filename]


class TestWrappersReset:

    def test_reset_single_by_stem(self, wrappers_dir):
        runner.invoke(app, ["wrappers", "install"])
        filename = next(iter(BUNDLED_WRAPPERS))
        stem = Path(filename).stem
        (wrappers_dir / filename).write_text("#!/bin/bash\n# user edit")

        result = runner.invoke(app, ["wrappers", "reset", stem])
        assert result.exit_code == 0
        assert "Restored" in result.output
        assert (wrappers_dir / filename).read_text() == BUNDLED_WRAPPERS[filename]

    def test_reset_unknown_exits_nonzero(self, wrappers_dir):
        result = runner.invoke(app, ["wrappers", "reset", "not-a-real-wrapper"])
        assert result.exit_code == 1
        assert "not a bundled wrapper" in result.output
        # Error message should list the valid names to guide the user.
        for filename in BUNDLED_WRAPPERS:
            assert Path(filename).stem in result.output

    def test_reset_all_when_no_name(self, wrappers_dir):
        runner.invoke(app, ["wrappers", "install"])
        # Tamper with every bundled file
        for filename in BUNDLED_WRAPPERS:
            (wrappers_dir / filename).write_text("#!/bin/bash\n# tampered")

        result = runner.invoke(app, ["wrappers", "reset"])
        assert result.exit_code == 0
        assert "All bundled wrappers reset" in result.output
        for filename, content in BUNDLED_WRAPPERS.items():
            assert (wrappers_dir / filename).read_text() == content
