"""
Unit tests for AgentSession
"""

import pytest
import time
from unittest.mock import MagicMock, patch

from src.agent_session import AgentSession, SessionError, MaxRetriesExceeded


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_session_status.return_value = {"session-1": {"state": "idle"}}
    client.get_todo.return_value = []
    return client


@pytest.fixture
def session(mock_client):
    return AgentSession(
        client=mock_client,
        agent_name="planner",
        session_id="session-1",
        max_retries=3,
        stuck_threshold=300,
    )


class TestAgentSession:
    def test_send_updates_activity(self, session, mock_client):
        mock_client.send_message.return_value = {"status": "ok"}
        old_time = session.last_activity_time

        time.sleep(0.01)
        session.send("hello")

        assert session.last_activity_time > old_time

    def test_send_without_session_raises(self, session):
        session.session_id = None
        with pytest.raises(SessionError, match="No active session"):
            session.send("hello")

    def test_send_error_sets_state(self, session, mock_client):
        mock_client.send_message.side_effect = Exception("connection error")

        with pytest.raises(SessionError):
            session.send("hello")

        assert session.state == "error"

    def test_create_session(self, session, mock_client):
        mock_client.create_session.return_value = "new-session-1"

        sid = session.create_session("test plan")

        assert sid == "new-session-1"
        assert session.session_id == "new-session-1"
        assert session.state == "idle"
        assert session.retry_count == 0

    def test_is_stuck_error_state(self, session, mock_client):
        mock_client.get_session_status.return_value = {"session-1": {"state": "error"}}

        assert session.is_stuck() == True
        assert session.state == "error"

    def test_is_stuck_busy_timeout(self, session, mock_client):
        mock_client.get_session_status.return_value = {"session-1": {"state": "busy"}}
        session.last_activity_time = time.time() - 600  # 10 分钟前

        assert session.is_stuck() == True

    def test_is_stuck_idle_with_incomplete_todos(self, session, mock_client):
        mock_client.get_session_status.return_value = {"session-1": {"state": "idle"}}
        mock_client.get_todo.return_value = [
            {"content": "task 1", "status": "completed"},
            {"content": "task 2", "status": "pending"},
            {"content": "task 3", "status": "pending"},
        ]

        assert session.is_stuck() == True

    def test_not_stuck_idle_all_done(self, session, mock_client):
        mock_client.get_session_status.return_value = {"session-1": {"state": "idle"}}
        mock_client.get_todo.return_value = [
            {"content": "task 1", "status": "completed"},
            {"content": "task 2", "status": "completed"},
        ]

        assert session.is_stuck() == False

    def test_not_stuck_busy_recently_active(self, session, mock_client):
        mock_client.get_session_status.return_value = {"session-1": {"state": "busy"}}
        session.last_activity_time = time.time() - 10  # 10 秒前

        assert session.is_stuck() == False

    def test_restart_aborts_and_creates_new(self, session, mock_client):
        mock_client.get_todo.return_value = [
            {"content": "task 1", "status": "completed"},
            {"content": "task 2", "status": "pending"},
        ]
        mock_client.create_session.return_value = "new-session-2"
        mock_client.send_message.return_value = {"status": "ok"}

        old_id = session.session_id
        new_id = session.restart()

        mock_client.abort_session.assert_called_once()
        mock_client.create_session.assert_called_once()
        assert new_id == "new-session-2"
        assert session.session_id == "new-session-2"
        assert session.retry_count == 1

    def test_restart_includes_remaining_todos(self, session, mock_client):
        mock_client.get_todo.return_value = [
            {"content": "done task", "status": "completed"},
            {"content": "pending task", "status": "pending"},
        ]
        mock_client.create_session.return_value = "new-session"
        mock_client.send_message.return_value = {"status": "ok"}

        session.restart()

        # 验证发送的消息包含剩余任务
        call_args = mock_client.send_message.call_args
        message = call_args.args[1]
        assert "pending task" in message

    def test_max_retries_exceeded(self, session, mock_client):
        session.retry_count = 3  # max_retries=3

        with pytest.raises(MaxRetriesExceeded):
            session.restart()

    def test_get_progress(self, session, mock_client):
        mock_client.get_todo.return_value = [
            {"content": "t1", "status": "completed"},
            {"content": "t2", "status": "completed"},
            {"content": "t3", "status": "pending"},
            {"content": "t4", "status": "pending"},
            {"content": "t5", "status": "pending"},
        ]

        progress = session.get_progress()

        assert progress["total"] == 5
        assert progress["completed"] == 2
        assert progress["pending"] == 3
        assert progress["percentage"] == 40.0

    def test_get_progress_empty(self, session, mock_client):
        mock_client.get_todo.return_value = []

        progress = session.get_progress()

        assert progress["total"] == 0
        assert progress["completed"] == 0
        assert progress["pending"] == 0
        assert progress["percentage"] == 0

    def test_is_done(self, session, mock_client):
        mock_client.get_todo.return_value = [
            {"content": "t1", "status": "completed"},
            {"content": "t2", "status": "completed"},
        ]

        assert session.is_done() == True

    def test_is_done_not_yet(self, session, mock_client):
        mock_client.get_todo.return_value = [
            {"content": "t1", "status": "completed"},
            {"content": "t2", "status": "pending"},
        ]

        assert session.is_done() == False

    def test_is_done_no_todos(self, session, mock_client):
        mock_client.get_todo.return_value = []

        assert session.is_done() == False

    def test_update_activity(self, session):
        old_time = session.last_activity_time
        time.sleep(0.01)

        session.update_activity()

        assert session.last_activity_time > old_time
        assert session.state == "idle"

    def test_get_status(self, session):
        status = session.get_status()

        assert status["session_id"] == "session-1"
        assert status["agent_name"] == "planner"
        assert status["state"] == "idle"
        assert status["retry_count"] == 0
        assert "progress" in status

    def test_repr(self, session):
        repr_str = repr(session)
        assert "planner" in repr_str
        assert "session-1" in repr_str
        assert "idle" in repr_str
