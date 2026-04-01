"""
Tests for Built-in Recovery Plugins
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.plugins.builtin_recovery import RestartRecovery
from src.plugins import DetectionResult


class TestRestartRecovery:
    def test_successful_recovery(self):
        recovery = RestartRecovery(agent_name="test-agent")

        client = MagicMock()
        client.create_session.return_value = "new-session-123"
        client.get_todo.return_value = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "pending"},
        ]

        session = MagicMock()
        session.session_id = "old-session"

        context = {
            "restart_count": 1,
            "detection_result": DetectionResult(
                detected=True, reason="idle with incomplete todos"
            ),
        }

        result = recovery.recover(session, client, context)

        assert result is not None
        assert result.session_id == "new-session-123"
        client.abort_session.assert_called_once_with("old-session")
        client.create_session.assert_called_once()
        client.send_message.assert_called_once()

        call_args = client.send_message.call_args
        message = call_args[0][1]
        assert "第 2 次重启" in message
        assert "Task 1" in message
        assert "Task 2" in message

    def test_recovery_with_no_old_session(self):
        recovery = RestartRecovery(agent_name="test-agent")

        client = MagicMock()
        client.create_session.return_value = "new-session-123"
        client.get_todo.return_value = []

        context = {"restart_count": 0}

        result = recovery.recover(None, client, context)

        assert result is not None
        assert result.session_id == "new-session-123"

    def test_recovery_handles_abort_failure(self):
        recovery = RestartRecovery(agent_name="test-agent")

        client = MagicMock()
        client.abort_session.side_effect = Exception("Abort failed")
        client.create_session.return_value = "new-session-123"
        client.get_todo.return_value = []

        session = MagicMock()
        session.session_id = "old-session"

        context = {"restart_count": 0}

        result = recovery.recover(session, client, context)
        assert result is not None

    def test_recovery_handles_create_failure(self):
        recovery = RestartRecovery(agent_name="test-agent")

        client = MagicMock()
        client.create_session.side_effect = Exception("Create failed")

        session = MagicMock()
        session.session_id = "old-session"

        context = {"restart_count": 0}

        result = recovery.recover(session, client, context)
        assert result is None

    def test_recovery_handles_send_failure(self):
        recovery = RestartRecovery(agent_name="test-agent")

        client = MagicMock()
        client.create_session.return_value = "new-session-123"
        client.get_todo.return_value = []
        client.send_message.side_effect = Exception("Send failed")

        session = MagicMock()
        session.session_id = "old-session"

        context = {"restart_count": 0}

        result = recovery.recover(session, client, context)
        assert result is None

    def test_build_continue_message_content(self):
        recovery = RestartRecovery(agent_name="test-agent")

        completed = [{"content": "Done task"}]
        remaining = [{"content": "Pending task"}]
        msg = recovery._build_continue_message(completed, remaining, 2, None)

        assert "第 2 次重启" in msg
        assert "Done task" in msg
        assert "Pending task" in msg
        assert "不要提前结束" in msg

    def test_build_continue_message_with_detection_info(self):
        recovery = RestartRecovery(agent_name="test-agent")

        detection = DetectionResult(
            detected=True, reason="Session idle with 2 incomplete todos"
        )
        msg = recovery._build_continue_message([], [], 1, detection)

        assert "中断原因" in msg
        assert "Session idle with 2 incomplete todos" in msg

    def test_name_property(self):
        recovery = RestartRecovery()
        assert recovery.name == "restart_recovery"
