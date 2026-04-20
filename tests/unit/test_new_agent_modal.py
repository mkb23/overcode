"""
Unit tests for NewAgentModal form logic.

Tests the field model, cycling, name derivation, host switching,
wrapper field, and launch message construction without needing a
running Textual app.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.tui_widgets.new_agent_modal import NewAgentModal, FormField, _unique_name


class TestUniqueNameDerivation:
    def test_no_collision(self):
        assert _unique_name("project", set()) == "project"

    def test_collision_increments(self):
        assert _unique_name("project", {"project"}) == "project2"

    def test_double_collision(self):
        assert _unique_name("project", {"project", "project2"}) == "project3"

    def test_many_collisions(self):
        existing = {f"agent{i}" for i in range(2, 10)} | {"agent"}
        assert _unique_name("agent", existing) == "agent10"


class TestFormField:
    def test_text_field(self):
        f = FormField("dir", "Directory", "text", value="/tmp")
        assert f.type == "text"
        assert f.value == "/tmp"
        assert f.auto is False

    def test_toggle_field(self):
        f = FormField("perms", "Perms", "toggle", value="normal", options=["normal", "bypass"])
        assert f.options == ["normal", "bypass"]

    def test_auto_flag(self):
        f = FormField("name", "Name", "text", value="project", auto=True)
        assert f.auto is True


class TestNewAgentModalState:
    """Test modal state management without a Textual app."""

    def _make_modal(self) -> NewAgentModal:
        return NewAgentModal(id="test-modal")

    def _make_modal_with_fields(self, **kwargs) -> NewAgentModal:
        """Create a modal and populate fields as show() would."""
        modal = self._make_modal()
        directory = kwargs.get("directory", "/Users/mike/Code/myproject")
        defaults = kwargs.get("defaults", {"bypass_permissions": False, "agent_teams": True, "provider": "web", "wrapper": ""})
        agents = kwargs.get("agents", ["coder", "reviewer"])
        existing = kwargs.get("existing_names", {"other-agent"})
        local_hostname = kwargs.get("local_hostname", "macbook")
        sister_names = kwargs.get("sister_names", [])

        base_name = Path(directory).name
        default_name = _unique_name(base_name, existing)
        agent_options = ["(none)"] + agents
        host_options = [local_hostname] + sister_names
        wrapper_default = defaults.get("wrapper", "") or ""

        modal._existing_names = existing
        modal._local_hostname = local_hostname
        modal.fields = [
            FormField("host", "Host", "toggle", value=host_options[0], options=host_options),
            FormField("directory", "Directory", "text", value=directory),
            FormField("name", "Name", "text", value=default_name, auto=True),
            FormField("agent", "Agent", "select", value="(none)", options=agent_options),
            FormField("perms", "Perms", "toggle",
                      value="bypass" if defaults.get("bypass_permissions") else "normal",
                      options=["normal", "bypass"]),
            FormField("teams", "Teams", "toggle",
                      value="on" if defaults.get("agent_teams") else "off",
                      options=["off", "on"]),
            FormField("provider", "Provider", "toggle",
                      value=defaults.get("provider", "web"),
                      options=["web", "bedrock"]),
            FormField("wrapper", "Wrapper", "text", value=wrapper_default),
            FormField("claude_args", "Claude args", "text", value=""),
        ]
        modal.selected_index = 2
        return modal

    def test_fields_populated(self):
        modal = self._make_modal_with_fields()
        assert len(modal.fields) == 9
        assert modal._field("host").value == "macbook"
        assert modal._field("directory").value == "/Users/mike/Code/myproject"
        assert modal._field("name").value == "myproject"
        assert modal._field("name").auto is True
        assert modal._field("agent").options == ["(none)", "coder", "reviewer"]
        assert modal._field("perms").value == "normal"
        assert modal._field("teams").value == "on"
        assert modal._field("wrapper").value == ""

    def test_host_field_includes_sisters(self):
        modal = self._make_modal_with_fields(sister_names=["desktop", "server"])
        host = modal._field("host")
        assert host.options == ["macbook", "desktop", "server"]
        assert host.value == "macbook"

    def test_is_remote_local(self):
        modal = self._make_modal_with_fields(sister_names=["desktop"])
        assert modal._is_remote is False

    def test_is_remote_after_cycle(self):
        modal = self._make_modal_with_fields(sister_names=["desktop"])
        host = modal._field("host")
        modal._cycle(host)
        assert host.value == "desktop"
        assert modal._is_remote is True

    def test_cycle_toggle(self):
        modal = self._make_modal()
        f = FormField("perms", "Perms", "toggle", value="normal", options=["normal", "bypass"])
        modal.fields = [f]
        modal.selected_index = 0
        modal._cycle(f)
        assert f.value == "bypass"
        modal._cycle(f)
        assert f.value == "normal"

    def test_cycle_select(self):
        modal = self._make_modal()
        f = FormField("agent", "Agent", "select", value="(none)", options=["(none)", "coder", "reviewer"])
        modal.fields = [f]
        modal.selected_index = 0
        modal._cycle(f)
        assert f.value == "coder"
        modal._cycle(f)
        assert f.value == "reviewer"
        modal._cycle(f)
        assert f.value == "(none)"

    def test_rederive_name_when_auto(self):
        modal = self._make_modal_with_fields()
        modal._field("directory").value = "/Users/mike/Code/other-project"
        modal._rederive_name()
        assert modal._field("name").value == "other-project"

    def test_no_rederive_after_manual_edit(self):
        modal = self._make_modal_with_fields()
        modal._field("name").value = "custom-name"
        modal._field("name").auto = False
        modal._field("directory").value = "/Users/mike/Code/other-project"
        modal._rederive_name()
        assert modal._field("name").value == "custom-name"

    def test_rederive_avoids_collision(self):
        modal = self._make_modal_with_fields(existing_names={"newdir"})
        modal._field("directory").value = "/foo/newdir"
        modal._rederive_name()
        assert modal._field("name").value == "newdir2"

    def test_host_change_updates_directory(self):
        modal = self._make_modal_with_fields(
            directory="/Users/mike/Code/myproject",
            sister_names=["desktop"],
        )
        host = modal._field("host")
        # Switch to remote
        modal._cycle(host)
        assert modal._field("directory").value == "."
        # Switch back to local
        modal._cycle(host)
        assert modal._field("directory").value != "."

    def test_wrapper_field_with_default(self):
        modal = self._make_modal_with_fields(
            defaults={"bypass_permissions": False, "agent_teams": False, "provider": "web", "wrapper": "devcontainer"},
        )
        assert modal._field("wrapper").value == "devcontainer"

    def test_wrapper_field_empty_by_default(self):
        modal = self._make_modal_with_fields()
        assert modal._field("wrapper").value == ""

    def test_confirm_edit_clears_auto(self):
        modal = self._make_modal_with_fields()
        modal.selected_index = 2  # name field
        modal._editing = True
        modal._edit_snapshot = "myproject"
        modal._cur.value = "custom"
        modal._confirm_edit(advance=False)
        assert modal._field("name").auto is False

    def test_confirm_edit_preserves_auto_if_unchanged(self):
        modal = self._make_modal_with_fields()
        modal.selected_index = 2  # name field
        modal._editing = True
        modal._edit_snapshot = "myproject"
        modal._confirm_edit(advance=False)
        assert modal._field("name").auto is True

    def test_render_produces_text(self):
        modal = self._make_modal_with_fields()
        modal._editing = False
        from rich.text import Text
        result = modal.render()
        assert isinstance(result, Text)
        assert "New Agent" in result.plain
        assert "Host" in result.plain
        assert "Directory" in result.plain

    def test_render_edit_mode(self):
        modal = self._make_modal_with_fields()
        modal.selected_index = 2
        modal._editing = True
        modal._cursor = 3
        from rich.text import Text
        result = modal.render()
        assert isinstance(result, Text)
        assert "type to edit" in result.plain
