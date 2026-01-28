"""
Unit tests for SessionManager.

These tests use a temporary directory for state files, allowing us to
test all persistence operations without touching production data.
"""

import pytest
import json
import threading
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from overcode.session_manager import SessionManager, Session, SessionStats


class TestSessionManagerBasics:
    """Test basic CRUD operations"""

    def test_creates_state_directory(self, tmp_path):
        """State directory is created if it doesn't exist"""
        state_dir = tmp_path / "sessions"
        assert not state_dir.exists()

        manager = SessionManager(state_dir=state_dir, skip_git_detection=True)

        assert state_dir.exists()

    def test_create_session(self, tmp_path):
        """Can create a new session"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        session = manager.create_session(
            name="test-session",
            tmux_session="agents",
            tmux_window=1,
            command=["claude", "code"],
            start_directory="/tmp"
        )

        assert session.name == "test-session"
        assert session.tmux_session == "agents"
        assert session.tmux_window == 1
        assert session.command == ["claude", "code"]
        assert session.id is not None
        assert session.start_time is not None

    def test_get_session_by_id(self, tmp_path):
        """Can retrieve session by ID"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        created = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        retrieved = manager.get_session(created.id)

        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.name == created.name

    def test_get_session_by_name(self, tmp_path):
        """Can retrieve session by name"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        manager.create_session(
            name="my-unique-name",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        retrieved = manager.get_session_by_name("my-unique-name")

        assert retrieved is not None
        assert retrieved.name == "my-unique-name"

    def test_get_nonexistent_session_returns_none(self, tmp_path):
        """Getting a nonexistent session returns None"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        assert manager.get_session("nonexistent-id") is None
        assert manager.get_session_by_name("nonexistent-name") is None

    def test_list_sessions(self, tmp_path):
        """Can list all sessions"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        manager.create_session(name="session1", tmux_session="agents", tmux_window=1, command=["claude"])
        manager.create_session(name="session2", tmux_session="agents", tmux_window=2, command=["claude"])
        manager.create_session(name="session3", tmux_session="agents", tmux_window=3, command=["claude"])

        sessions = manager.list_sessions()

        assert len(sessions) == 3
        names = {s.name for s in sessions}
        assert names == {"session1", "session2", "session3"}

    def test_delete_session(self, tmp_path):
        """Can delete a session"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="to-delete",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        manager.delete_session(session.id)

        assert manager.get_session(session.id) is None
        assert len(manager.list_sessions()) == 0


class TestSessionManagerUpdates:
    """Test session update operations"""

    def test_update_session_status(self, tmp_path):
        """Can update session status"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        manager.update_session_status(session.id, "stopped")

        updated = manager.get_session(session.id)
        assert updated.status == "stopped"

    def test_update_session_fields(self, tmp_path):
        """Can update arbitrary session fields"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        manager.update_session(session.id, permissiveness_mode="strict", branch="feature-x")

        updated = manager.get_session(session.id)
        assert updated.permissiveness_mode == "strict"
        assert updated.branch == "feature-x"

    def test_set_standing_instructions(self, tmp_path):
        """Setting standing instructions resets complete flag"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        # First set instructions and mark complete
        manager.set_standing_instructions(session.id, "Do the thing")
        manager.set_standing_orders_complete(session.id, True)

        # Verify complete
        updated = manager.get_session(session.id)
        assert updated.standing_orders_complete is True

        # Set new instructions - should reset complete flag
        manager.set_standing_instructions(session.id, "Do a different thing")

        updated = manager.get_session(session.id)
        assert updated.standing_instructions == "Do a different thing"
        assert updated.standing_orders_complete is False

    def test_set_permissiveness(self, tmp_path):
        """Can set permissiveness mode"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        manager.set_permissiveness(session.id, "permissive")

        updated = manager.get_session(session.id)
        assert updated.permissiveness_mode == "permissive"

    def test_set_agent_value(self, tmp_path):
        """Can set agent value (#61)"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        # Default value is 1000
        assert session.agent_value == 1000

        # Set high priority
        manager.set_agent_value(session.id, 2000)

        updated = manager.get_session(session.id)
        assert updated.agent_value == 2000

        # Set low priority
        manager.set_agent_value(session.id, 500)

        updated = manager.get_session(session.id)
        assert updated.agent_value == 500


class TestSessionStats:
    """Test session statistics tracking"""

    def test_update_stats(self, tmp_path):
        """Can update session statistics"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        manager.update_stats(
            session.id,
            interaction_count=5,
            estimated_cost_usd=0.15,
            steers_count=2
        )

        updated = manager.get_session(session.id)
        assert updated.stats.interaction_count == 5
        assert updated.stats.estimated_cost_usd == 0.15
        assert updated.stats.steers_count == 2

    def test_stats_persist_across_loads(self, tmp_path):
        """Stats survive manager recreation"""
        manager1 = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager1.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )
        manager1.update_stats(session.id, interaction_count=10, total_tokens=5000)

        # Create new manager instance
        manager2 = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        reloaded = manager2.get_session(session.id)

        assert reloaded.stats.interaction_count == 10
        assert reloaded.stats.total_tokens == 5000

    def test_default_stats_values(self, tmp_path):
        """New sessions have sensible default stats"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        assert session.stats.interaction_count == 0
        assert session.stats.estimated_cost_usd == 0.0
        assert session.stats.steers_count == 0
        assert session.stats.current_task == "Initializing..."


class TestSessionPersistence:
    """Test state file persistence"""

    def test_state_file_created(self, tmp_path):
        """State file is created on first write"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        state_file = tmp_path / "sessions.json"
        assert state_file.exists()

    def test_state_file_is_valid_json(self, tmp_path):
        """State file contains valid JSON"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        state_file = tmp_path / "sessions.json"
        with open(state_file) as f:
            data = json.load(f)

        assert isinstance(data, dict)
        assert len(data) == 1

    def test_handles_corrupted_state_file(self, tmp_path):
        """Gracefully handles corrupted state file"""
        state_file = tmp_path / "sessions.json"
        state_file.write_text("not valid json {{{")

        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        sessions = manager.list_sessions()

        # Should return empty list rather than crash
        assert sessions == []

    def test_handles_missing_stats_in_old_data(self, tmp_path):
        """Handles sessions from before stats were added"""
        # Write old-format session without stats
        state_file = tmp_path / "sessions.json"
        old_data = {
            "session-123": {
                "id": "session-123",
                "name": "old-session",
                "tmux_session": "agents",
                "tmux_window": 1,
                "command": ["claude"],
                "start_directory": None,
                "start_time": "2024-01-01T00:00:00",
                "status": "running",
                "permissiveness_mode": "normal",
                "standing_instructions": "",
                "standing_orders_complete": False
                # Note: no "stats" field
            }
        }
        state_file.write_text(json.dumps(old_data))

        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)
        session = manager.get_session("session-123")

        # Should have default stats
        assert session is not None
        assert session.stats.interaction_count == 0


class TestConcurrency:
    """Test concurrent access handling"""

    def test_concurrent_writes_dont_corrupt(self, tmp_path):
        """Multiple concurrent writes don't corrupt state"""
        manager = SessionManager(state_dir=tmp_path, skip_git_detection=True)

        # Create initial session
        session = manager.create_session(
            name="test",
            tmux_session="agents",
            tmux_window=1,
            command=["claude"]
        )

        errors = []

        def update_stats(n):
            try:
                for i in range(10):
                    manager.update_stats(session.id, interaction_count=n * 10 + i)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        # Run concurrent updates
        threads = [threading.Thread(target=update_stats, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should not have crashed
        assert len(errors) == 0

        # State file should still be valid
        state_file = tmp_path / "sessions.json"
        with open(state_file) as f:
            data = json.load(f)
        assert "stats" in data[session.id]


# =============================================================================
# Run tests directly
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
