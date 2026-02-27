"""
Unit tests for CommandBar widget logic.

Tests the helper methods, state management, message classes, and parsing
functions in isolation without requiring a running Textual application.
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helpers to build a bare CommandBar without running the Textual app
# ---------------------------------------------------------------------------

def _make_bare_command_bar(**extra_attrs):
    """Create a CommandBar bypassing __init__ for unit-testing methods.

    Sets only the minimum attributes needed to call individual methods.
    The ``expanded`` reactive has a watcher that calls query_one(), so we
    write directly to the instance __dict__ to bypass the reactive descriptor.
    Textual's ``app`` is a property (on MessagePump) that uses a ContextVar,
    so we create a test subclass that overrides it with a simple attribute.
    """
    from overcode.tui_widgets.command_bar import CommandBar

    # Create a test subclass that overrides the ``app`` property
    class _TestableCommandBar(CommandBar):
        _mock_app = MagicMock()

        @property
        def app(self):
            return self._mock_app

    widget = _TestableCommandBar.__new__(_TestableCommandBar)
    # Textual internals required for reactive attribute access
    widget._id = "command-bar"
    widget._is_mounted = False
    widget._running = False
    # Default CommandBar attributes (non-reactive)
    widget.target_session = None
    widget.target_session_id = None
    widget.mode = "send"
    widget.new_agent_dir = None
    widget.new_agent_name = None
    widget.heartbeat_freq = None
    # Bypass the reactive descriptor for ``expanded`` — its watcher calls
    # query_one() which requires a fully mounted widget tree.
    widget.__dict__["expanded"] = False
    # Give each instance its own mock app
    widget._mock_app = MagicMock()
    # Apply caller overrides.
    for k, v in extra_attrs.items():
        if k == "expanded":
            widget.__dict__["expanded"] = v
        elif k == "app":
            widget._mock_app = v
        else:
            setattr(widget, k, v)
    return widget


# ===========================================================================
# Message classes
# ===========================================================================


class TestCommandBarMessages:
    """Tests for the Textual message classes on CommandBar."""

    def test_send_requested_stores_attributes(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.SendRequested("agent-1", "do the thing")
        assert msg.session_name == "agent-1"
        assert msg.text == "do the thing"

    def test_standing_order_requested_stores_attributes(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.StandingOrderRequested("agent-2", "always test")
        assert msg.session_name == "agent-2"
        assert msg.text == "always test"

    def test_new_agent_requested_stores_attributes(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.NewAgentRequested("my-agent", directory="/tmp/dir", bypass_permissions=True)
        assert msg.agent_name == "my-agent"
        assert msg.directory == "/tmp/dir"
        assert msg.bypass_permissions is True

    def test_new_agent_requested_defaults(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.NewAgentRequested("my-agent")
        assert msg.agent_name == "my-agent"
        assert msg.directory is None
        assert msg.bypass_permissions is False

    def test_value_updated_stores_attributes(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.ValueUpdated("agent-3", 2000)
        assert msg.session_name == "agent-3"
        assert msg.value == 2000

    def test_annotation_updated_stores_attributes(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.AnnotationUpdated("agent-4", "needs review")
        assert msg.session_name == "agent-4"
        assert msg.annotation == "needs review"

    def test_heartbeat_updated_stores_attributes(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.HeartbeatUpdated("agent-5", enabled=True, frequency=600, instruction="check in")
        assert msg.session_name == "agent-5"
        assert msg.enabled is True
        assert msg.frequency == 600
        assert msg.instruction == "check in"

    def test_budget_updated_stores_attributes(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.BudgetUpdated("agent-6", 5.50)
        assert msg.session_name == "agent-6"
        assert msg.budget_usd == 5.50

    def test_clear_requested_exists(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.ClearRequested()
        assert msg is not None


# ===========================================================================
# _parse_duration
# ===========================================================================


class TestParseDuration:
    """Tests for CommandBar._parse_duration."""

    def test_empty_string_returns_none(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("") is None

    def test_whitespace_returns_none(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("   ") is None

    def test_plain_integer_seconds(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("300") == 300

    def test_seconds_suffix(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("60s") == 60

    def test_minutes_suffix(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("5m") == 300

    def test_hours_suffix(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("1h") == 3600

    def test_case_insensitive(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("5M") == 300
        assert widget._parse_duration("1H") == 3600
        assert widget._parse_duration("60S") == 60

    def test_with_leading_trailing_whitespace(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("  5m  ") == 300

    def test_invalid_returns_none(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("abc") is None
        assert widget._parse_duration("m5") is None
        assert widget._parse_duration("5x") is None

    def test_zero_seconds(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("0") == 0

    def test_large_value(self):
        widget = _make_bare_command_bar()
        assert widget._parse_duration("24h") == 86400


# ===========================================================================
# _send_message
# ===========================================================================


class TestSendMessage:
    """Tests for CommandBar._send_message."""

    def test_sends_message_with_target(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._send_message("hello world")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.session_name == "agent-1"
        assert msg.text == "hello world"

    def test_strips_text(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._send_message("  hello  ")
        msg = widget.post_message.call_args[0][0]
        assert msg.text == "hello"

    def test_no_target_does_not_send(self):
        widget = _make_bare_command_bar(target_session=None)
        widget.post_message = MagicMock()
        widget._send_message("hello")
        widget.post_message.assert_not_called()

    def test_empty_text_does_not_send(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._send_message("")
        widget.post_message.assert_not_called()

    def test_whitespace_only_does_not_send(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._send_message("   ")
        widget.post_message.assert_not_called()


# ===========================================================================
# _set_standing_order
# ===========================================================================


class TestSetStandingOrder:
    """Tests for CommandBar._set_standing_order."""

    def test_sends_standing_order_with_target(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._set_standing_order("always run tests")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.session_name == "agent-1"
        assert msg.text == "always run tests"

    def test_strips_text(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._set_standing_order("  test  ")
        msg = widget.post_message.call_args[0][0]
        assert msg.text == "test"

    def test_empty_text_sends_empty_to_clear(self):
        """Empty string should still be sent (to clear standing orders)."""
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._set_standing_order("")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.text == ""

    def test_no_target_does_not_send(self):
        widget = _make_bare_command_bar(target_session=None)
        widget.post_message = MagicMock()
        widget._set_standing_order("test")
        widget.post_message.assert_not_called()


# ===========================================================================
# _set_value
# ===========================================================================


class TestSetValue:
    """Tests for CommandBar._set_value."""

    def test_valid_integer(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_value("2000")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.session_name == "agent-1"
        assert msg.value == 2000

    def test_empty_defaults_to_1000(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_value("")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.value == 1000

    def test_whitespace_defaults_to_1000(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_value("   ")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.value == 1000

    def test_negative_value_notifies_error(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_value("-1")
        widget.post_message.assert_not_called()
        widget.app.notify.assert_called_once()

    def test_too_large_value_notifies_error(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_value("10000")
        widget.post_message.assert_not_called()
        widget.app.notify.assert_called_once()

    def test_non_integer_notifies_error(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_value("abc")
        widget.post_message.assert_not_called()
        widget.app.notify.assert_called_once()

    def test_boundary_0_is_valid(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_value("0")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.value == 0

    def test_boundary_9999_is_valid(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_value("9999")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.value == 9999

    def test_no_target_does_not_send(self):
        widget = _make_bare_command_bar(target_session=None)
        widget.post_message = MagicMock()

        widget._set_value("1000")
        widget.post_message.assert_not_called()


# ===========================================================================
# _set_cost_budget
# ===========================================================================


class TestSetCostBudget:
    """Tests for CommandBar._set_cost_budget."""

    def test_valid_budget(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_cost_budget("5.00")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.session_name == "agent-1"
        assert msg.budget_usd == 5.0

    def test_dollar_sign_stripped(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_cost_budget("$5.00")
        msg = widget.post_message.call_args[0][0]
        assert msg.budget_usd == 5.0

    def test_empty_defaults_to_zero(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_cost_budget("")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.budget_usd == 0.0

    def test_negative_budget_notifies_error(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_cost_budget("-5.00")
        widget.post_message.assert_not_called()
        widget.app.notify.assert_called_once()

    def test_invalid_budget_notifies_error(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_cost_budget("abc")
        widget.post_message.assert_not_called()
        widget.app.notify.assert_called_once()

    def test_no_target_does_not_send(self):
        widget = _make_bare_command_bar(target_session=None)
        widget.post_message = MagicMock()

        widget._set_cost_budget("5.00")
        widget.post_message.assert_not_called()

    def test_integer_budget(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()

        widget._set_cost_budget("10")
        msg = widget.post_message.call_args[0][0]
        assert msg.budget_usd == 10.0


# ===========================================================================
# _set_annotation
# ===========================================================================


class TestSetAnnotation:
    """Tests for CommandBar._set_annotation."""

    def test_sends_annotation(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._set_annotation("needs review")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.session_name == "agent-1"
        assert msg.annotation == "needs review"

    def test_strips_whitespace(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._set_annotation("  needs review  ")
        msg = widget.post_message.call_args[0][0]
        assert msg.annotation == "needs review"

    def test_empty_sends_to_clear(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget.post_message = MagicMock()
        widget._set_annotation("")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.annotation == ""

    def test_no_target_does_not_send(self):
        widget = _make_bare_command_bar(target_session=None)
        widget.post_message = MagicMock()
        widget._set_annotation("test")
        widget.post_message.assert_not_called()


# ===========================================================================
# _handle_new_agent_name
# ===========================================================================


class TestHandleNewAgentName:
    """Tests for CommandBar._handle_new_agent_name."""

    def test_stores_name_and_transitions_to_perms(self):
        widget = _make_bare_command_bar()
        widget._update_target_label = MagicMock()
        widget._handle_new_agent_name("my-agent")
        assert widget.new_agent_name == "my-agent"
        assert widget.mode == "new_agent_perms"
        widget._update_target_label.assert_called_once()


# ===========================================================================
# _create_new_agent
# ===========================================================================


class TestCreateNewAgent:
    """Tests for CommandBar._create_new_agent."""

    def test_posts_message_and_resets_state(self):
        widget = _make_bare_command_bar(
            new_agent_dir="/tmp/test",
            new_agent_name="agent-1",
        )
        widget.post_message = MagicMock()
        widget._update_target_label = MagicMock()
        widget._create_new_agent("agent-1", bypass_permissions=False)

        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.agent_name == "agent-1"
        assert msg.directory == "/tmp/test"
        assert msg.bypass_permissions is False

        # State reset
        assert widget.new_agent_dir is None
        assert widget.new_agent_name is None
        assert widget.mode == "send"

    def test_bypass_permissions(self):
        widget = _make_bare_command_bar(new_agent_dir="/tmp")
        widget.post_message = MagicMock()
        widget._update_target_label = MagicMock()
        widget._create_new_agent("agent-1", bypass_permissions=True)
        msg = widget.post_message.call_args[0][0]
        assert msg.bypass_permissions is True


# ===========================================================================
# _handle_heartbeat_instruction
# ===========================================================================


class TestHandleHeartbeatInstruction:
    """Tests for CommandBar._handle_heartbeat_instruction."""

    def test_sends_heartbeat_with_instruction(self):
        widget = _make_bare_command_bar(target_session="agent-1", heartbeat_freq=300)
        widget.post_message = MagicMock()
        widget._handle_heartbeat_instruction("check status")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.session_name == "agent-1"
        assert msg.enabled is True
        assert msg.frequency == 300
        assert msg.instruction == "check status"
        # heartbeat_freq should be reset
        assert widget.heartbeat_freq is None

    def test_no_target_does_not_send(self):
        widget = _make_bare_command_bar(target_session=None, heartbeat_freq=300)
        widget.post_message = MagicMock()
        widget._handle_heartbeat_instruction("check status")
        widget.post_message.assert_not_called()

    def test_empty_instruction_uses_existing(self):
        """Empty input reuses existing heartbeat instruction from session."""
        mock_session = MagicMock()
        mock_session.id = "uuid-123"
        mock_session.heartbeat_instruction = "existing instruction"
        mock_app = MagicMock()
        mock_app.sessions = [mock_session]

        widget = _make_bare_command_bar(target_session="agent-1", target_session_id="uuid-123", heartbeat_freq=300)
        widget._mock_app = mock_app
        widget.post_message = MagicMock()
        widget._handle_heartbeat_instruction("")
        widget.post_message.assert_called_once()
        msg = widget.post_message.call_args[0][0]
        assert msg.instruction == "existing instruction"

    def test_empty_instruction_no_existing_errors(self):
        """Empty input with no existing instruction shows error."""
        mock_session = MagicMock()
        mock_session.id = "uuid-123"
        mock_session.heartbeat_instruction = None
        mock_app = MagicMock()
        mock_app.sessions = [mock_session]

        widget = _make_bare_command_bar(target_session="agent-1", target_session_id="uuid-123", heartbeat_freq=300)
        widget._mock_app = mock_app
        widget.post_message = MagicMock()
        widget._handle_heartbeat_instruction("")
        widget.post_message.assert_not_called()
        mock_app.notify.assert_called_once()

    def test_defaults_frequency_when_none(self):
        """Uses 300 default when heartbeat_freq is None."""
        widget = _make_bare_command_bar(target_session="agent-1", heartbeat_freq=None)
        widget.post_message = MagicMock()
        widget._handle_heartbeat_instruction("do stuff")
        msg = widget.post_message.call_args[0][0]
        assert msg.frequency == 300


# ===========================================================================
# set_target and set_mode
# ===========================================================================


class TestSetTargetAndMode:
    """Tests for set_target and set_mode methods."""

    def test_set_target_updates_session_and_resets_mode(self):
        widget = _make_bare_command_bar(mode="standing_orders")
        widget._update_target_label = MagicMock()
        widget.set_target("agent-1")
        assert widget.target_session == "agent-1"
        assert widget.mode == "send"
        widget._update_target_label.assert_called_once()

    def test_set_target_none(self):
        widget = _make_bare_command_bar(target_session="agent-1")
        widget._update_target_label = MagicMock()
        widget.set_target(None)
        assert widget.target_session is None
        assert widget.mode == "send"

    def test_set_mode_updates_mode(self):
        widget = _make_bare_command_bar()
        widget._update_target_label = MagicMock()
        widget.set_mode("standing_orders")
        assert widget.mode == "standing_orders"
        widget._update_target_label.assert_called_once()

    def test_set_mode_to_new_agent_dir(self):
        widget = _make_bare_command_bar()
        widget._update_target_label = MagicMock()
        widget.set_mode("new_agent_dir")
        assert widget.mode == "new_agent_dir"


# ===========================================================================
# action_toggle_expand
# ===========================================================================


class TestActionToggleExpand:
    """Tests for action_toggle_expand.

    The action_toggle_expand method sets self.expanded which triggers
    the reactive watcher (watch_expanded). We patch the watcher to
    avoid requiring a full widget tree.
    """

    def test_toggles_expanded(self):
        from overcode.tui_widgets.command_bar import CommandBar

        widget = _make_bare_command_bar(expanded=False)
        with patch.object(CommandBar, "watch_expanded"):
            widget.action_toggle_expand()
            assert widget.expanded is True
            widget.action_toggle_expand()
            assert widget.expanded is False


# ===========================================================================
# get_mode_label_and_placeholder (pure function)
# ===========================================================================


class TestGetModeLabelAndPlaceholder:
    """Tests for the pure get_mode_label_and_placeholder function."""

    def test_new_agent_dir_mode(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("new_agent_dir", None)
        assert label == "[New Agent: Directory] "
        assert "directory" in placeholder.lower()

    def test_new_agent_name_mode(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("new_agent_name", None)
        assert label == "[New Agent: Name] "
        assert "name" in placeholder.lower()

    def test_new_agent_perms_mode(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("new_agent_perms", None)
        assert label == "[New Agent: Permissions] "
        assert "bypass" in placeholder.lower()

    def test_standing_orders_with_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("standing_orders", "my-agent")
        assert label == "[my-agent Standing Orders] "
        assert "standing orders" in placeholder.lower()

    def test_standing_orders_no_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, _ = get_mode_label_and_placeholder("standing_orders", None)
        assert label == "[Standing Orders] "

    def test_value_with_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("value", "agent-1")
        assert label == "[agent-1 Value] "
        assert "value" in placeholder.lower()

    def test_cost_budget_with_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("cost_budget", "agent-1")
        assert label == "[agent-1 Budget] "
        assert "budget" in placeholder.lower()

    def test_annotation_with_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("annotation", "agent-1")
        assert label == "[agent-1 Annotation] "
        assert "annotation" in placeholder.lower()

    def test_heartbeat_freq_with_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("heartbeat_freq", "agent-1")
        assert label == "[agent-1 Heartbeat: Frequency] "
        assert "interval" in placeholder.lower()

    def test_heartbeat_instruction_with_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("heartbeat_instruction", "agent-1")
        assert label == "[agent-1 Heartbeat: Instruction] "
        assert "instruction" in placeholder.lower()

    def test_send_mode_with_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("send", "agent-1")
        assert label == "[agent-1] "
        assert "instruction" in placeholder.lower()

    def test_send_mode_no_session(self):
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        label, placeholder = get_mode_label_and_placeholder("send", None)
        assert label == "[no session] "

    def test_all_session_modes_include_session_name(self):
        """All session-dependent modes should include the session name."""
        from overcode.tui_widgets.command_bar import get_mode_label_and_placeholder
        session_modes = ["standing_orders", "value", "cost_budget", "annotation",
                         "heartbeat_freq", "heartbeat_instruction"]
        for mode in session_modes:
            label, _ = get_mode_label_and_placeholder(mode, "test-agent")
            assert "test-agent" in label, f"Mode {mode} missing session name"


# ===========================================================================
# Session ID routing (#316)
# ===========================================================================


class TestSessionIdRouting:
    """Tests for session_id support in CommandBar and Messages."""

    def test_set_target_stores_both_name_and_id(self):
        widget = _make_bare_command_bar()
        widget._update_target_label = MagicMock()
        widget.set_target("agent-1", session_id="uuid-123")
        assert widget.target_session == "agent-1"
        assert widget.target_session_id == "uuid-123"

    def test_set_target_id_defaults_to_none(self):
        widget = _make_bare_command_bar()
        widget._update_target_label = MagicMock()
        widget.set_target("agent-1")
        assert widget.target_session == "agent-1"
        assert widget.target_session_id is None

    def test_send_requested_carries_session_id(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.SendRequested("agent-1", "hello", session_id="uuid-123")
        assert msg.session_id == "uuid-123"

    def test_send_requested_session_id_defaults_empty(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.SendRequested("agent-1", "hello")
        assert msg.session_id == ""

    def test_standing_order_carries_session_id(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.StandingOrderRequested("agent-1", "test", session_id="uuid-123")
        assert msg.session_id == "uuid-123"

    def test_value_updated_carries_session_id(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.ValueUpdated("agent-1", 2000, session_id="uuid-123")
        assert msg.session_id == "uuid-123"

    def test_annotation_updated_carries_session_id(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.AnnotationUpdated("agent-1", "note", session_id="uuid-123")
        assert msg.session_id == "uuid-123"

    def test_heartbeat_updated_carries_session_id(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.HeartbeatUpdated("agent-1", True, 300, "ping", session_id="uuid-123")
        assert msg.session_id == "uuid-123"

    def test_budget_updated_carries_session_id(self):
        from overcode.tui_widgets.command_bar import CommandBar
        msg = CommandBar.BudgetUpdated("agent-1", 5.0, session_id="uuid-123")
        assert msg.session_id == "uuid-123"

    def test_send_message_threads_session_id(self):
        """_send_message should include target_session_id in the posted message."""
        widget = _make_bare_command_bar(target_session="agent-1", target_session_id="uuid-abc")
        widget.post_message = MagicMock()
        widget._send_message("hello")
        msg = widget.post_message.call_args[0][0]
        assert msg.session_id == "uuid-abc"

    def test_set_standing_order_threads_session_id(self):
        widget = _make_bare_command_bar(target_session="agent-1", target_session_id="uuid-abc")
        widget.post_message = MagicMock()
        widget._set_standing_order("always test")
        msg = widget.post_message.call_args[0][0]
        assert msg.session_id == "uuid-abc"

    def test_set_value_threads_session_id(self):
        widget = _make_bare_command_bar(target_session="agent-1", target_session_id="uuid-abc")
        widget.post_message = MagicMock()
        widget._set_value("2000")
        msg = widget.post_message.call_args[0][0]
        assert msg.session_id == "uuid-abc"

    def test_set_annotation_threads_session_id(self):
        widget = _make_bare_command_bar(target_session="agent-1", target_session_id="uuid-abc")
        widget.post_message = MagicMock()
        widget._set_annotation("review")
        msg = widget.post_message.call_args[0][0]
        assert msg.session_id == "uuid-abc"

    def test_set_cost_budget_threads_session_id(self):
        widget = _make_bare_command_bar(target_session="agent-1", target_session_id="uuid-abc")
        widget.post_message = MagicMock()
        widget._set_cost_budget("5.00")
        msg = widget.post_message.call_args[0][0]
        assert msg.session_id == "uuid-abc"

    def test_heartbeat_instruction_threads_session_id(self):
        widget = _make_bare_command_bar(
            target_session="agent-1", target_session_id="uuid-abc", heartbeat_freq=300
        )
        widget.post_message = MagicMock()
        widget._handle_heartbeat_instruction("check in")
        msg = widget.post_message.call_args[0][0]
        assert msg.session_id == "uuid-abc"

    def test_heartbeat_disable_threads_session_id(self):
        widget = _make_bare_command_bar(target_session="agent-1", target_session_id="uuid-abc")
        widget.post_message = MagicMock()
        widget.action_clear_and_unfocus = MagicMock()
        widget._handle_heartbeat_freq("off")
        msg = widget.post_message.call_args[0][0]
        assert msg.session_id == "uuid-abc"

    def test_find_target_session_by_id(self):
        """_find_target_session should search app.sessions by ID."""
        mock_session = MagicMock()
        mock_session.id = "uuid-abc"
        mock_app = MagicMock()
        mock_app.sessions = [mock_session]

        widget = _make_bare_command_bar(target_session_id="uuid-abc")
        widget._mock_app = mock_app
        assert widget._find_target_session() is mock_session

    def test_find_target_session_returns_none_when_no_id(self):
        widget = _make_bare_command_bar(target_session_id=None)
        assert widget._find_target_session() is None

    def test_find_target_session_returns_none_when_not_found(self):
        mock_app = MagicMock()
        mock_app.sessions = []
        widget = _make_bare_command_bar(target_session_id="uuid-missing")
        widget._mock_app = mock_app
        assert widget._find_target_session() is None
