"""
Tests for Built-in Verification Plugins
"""

import pytest
import sys
import os
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.plugins.builtin_verification import (
    StableCompletionVerifier,
    TodoExecutionVerifier,
)


class TestStableCompletionVerifier:
    def test_stable_completion_passes(self):
        verifier = StableCompletionVerifier(stable_count=2, check_interval=0.01)

        client = MagicMock()
        client.get_todo.return_value = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "completed"},
        ]
        client.get_session_status.return_value = {
            "session-1": {"state": "idle", "id": "session-1"}
        }

        session = MagicMock()
        session.session_id = "session-1"

        result = verifier.verify(session, client)
        assert result is True
        assert client.get_todo.call_count == 2

    def test_fails_when_todos_not_completed(self):
        verifier = StableCompletionVerifier(stable_count=2, check_interval=0.01)

        client = MagicMock()
        client.get_todo.return_value = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "pending"},
        ]

        session = MagicMock()
        session.session_id = "session-1"

        result = verifier.verify(session, client)
        assert result is False

    def test_fails_when_session_still_busy(self):
        verifier = StableCompletionVerifier(stable_count=2, check_interval=0.01)

        client = MagicMock()
        client.get_todo.return_value = [
            {"id": "1", "content": "Task 1", "status": "completed"},
        ]
        client.get_session_status.return_value = {
            "session-1": {"state": "busy", "id": "session-1"}
        }

        session = MagicMock()
        session.session_id = "session-1"

        result = verifier.verify(session, client)
        assert result is False

    def test_fails_when_no_session_id(self):
        verifier = StableCompletionVerifier()
        session = MagicMock()
        session.session_id = None
        client = MagicMock()
        result = verifier.verify(session, client)
        assert result is False

    def test_fails_on_exception(self):
        verifier = StableCompletionVerifier(stable_count=2, check_interval=0.01)

        client = MagicMock()
        client.get_todo.side_effect = Exception("API error")

        session = MagicMock()
        session.session_id = "session-1"

        result = verifier.verify(session, client)
        assert result is False


class TestTodoExecutionVerifier:
    def test_passes_when_messages_match_todos(self):
        verifier = TodoExecutionVerifier(min_ratio=0.5)

        client = MagicMock()
        client.get_todo.return_value = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "completed"},
        ]
        client.get_messages.return_value = [
            {"info": {"role": "assistant"}},
            {"info": {"role": "assistant"}},
        ]

        session = MagicMock()
        session.session_id = "session-1"

        result = verifier.verify(session, client)
        assert result is True

    def test_fails_when_too_few_messages(self):
        verifier = TodoExecutionVerifier(min_ratio=0.5)

        client = MagicMock()
        client.get_todo.return_value = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "completed"},
            {"id": "3", "content": "Task 3", "status": "completed"},
            {"id": "4", "content": "Task 4", "status": "completed"},
        ]
        client.get_messages.return_value = [
            {"info": {"role": "assistant"}},
        ]

        session = MagicMock()
        session.session_id = "session-1"

        result = verifier.verify(session, client)
        assert result is False

    def test_fails_when_no_completed_todos(self):
        verifier = TodoExecutionVerifier()

        client = MagicMock()
        client.get_todo.return_value = [
            {"id": "1", "content": "Task 1", "status": "pending"},
        ]

        session = MagicMock()
        session.session_id = "session-1"

        result = verifier.verify(session, client)
        assert result is False

    def test_fails_when_no_session_id(self):
        verifier = TodoExecutionVerifier()
        session = MagicMock()
        session.session_id = None
        client = MagicMock()
        result = verifier.verify(session, client)
        assert result is False

    def test_fails_on_exception(self):
        verifier = TodoExecutionVerifier()

        client = MagicMock()
        client.get_todo.side_effect = Exception("API error")

        session = MagicMock()
        session.session_id = "session-1"

        result = verifier.verify(session, client)
        assert result is False

    def test_name_property(self):
        verifier = StableCompletionVerifier()
        assert verifier.name == "stable_completion_verifier"

        verifier2 = TodoExecutionVerifier()
        assert verifier2.name == "todo_execution_verifier"
