"""
Tests for Built-in Detection Plugins

验证四种检测插件：
1. StuckDetector - 卡死检测
2. IdleIncompleteDetector - 偷懒检测
3. PrematureEndDetector - 提前结束检测（核心新增）
4. SessionInvalidDetector - 会话失效检测
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.plugins.builtin_detectors import (
    StuckDetector,
    IdleIncompleteDetector,
    PrematureEndDetector,
    SessionInvalidDetector,
)


def make_session(session_id="test-123", last_activity=None):
    session = MagicMock()
    session.session_id = session_id
    session.last_activity_time = last_activity or 0
    return session


def make_client(state="idle", todos=None, status_error=None):
    client = MagicMock()
    if status_error:
        client.get_session_status.side_effect = status_error
    else:
        client.get_session_status.return_value = {
            "test-123": {"state": state, "id": "test-123"}
        }
    client.get_todo.return_value = todos or []
    return client


class TestStuckDetector:
    def test_no_stuck_when_idle(self):
        detector = StuckDetector(timeout=300)
        session = make_session()
        client = make_client(state="idle")
        result = detector.detect(session, client)
        assert result.detected is False

    def test_detects_error_state(self):
        detector = StuckDetector(timeout=300)
        session = make_session()
        client = make_client(state="error")
        result = detector.detect(session, client)
        assert result.detected is True
        assert "error" in result.reason.lower()
        assert result.severity == "high"

    def test_detects_busy_timeout(self):
        import time

        detector = StuckDetector(timeout=10)
        session = make_session(last_activity=time.time() - 20)
        client = make_client(state="busy")
        result = detector.detect(session, client)
        assert result.detected is True
        assert "busy" in result.reason.lower()
        assert result.severity == "medium"

    def test_no_stuck_when_busy_under_threshold(self):
        import time

        detector = StuckDetector(timeout=300)
        session = make_session(last_activity=time.time() - 10)
        client = make_client(state="busy")
        result = detector.detect(session, client)
        assert result.detected is False

    def test_handles_status_error_gracefully(self):
        import time

        detector = StuckDetector(timeout=10)
        session = make_session(last_activity=time.time() - 20)
        client = make_client(status_error=ConnectionError("refused"))
        result = detector.detect(session, client)
        assert result.detected is True

    def test_no_session_id(self):
        detector = StuckDetector()
        session = make_session(session_id=None)
        client = make_client()
        result = detector.detect(session, client)
        assert result.detected is False


class TestIdleIncompleteDetector:
    def test_detects_idle_with_incomplete_todos(self):
        detector = IdleIncompleteDetector()
        session = make_session()
        todos = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "pending"},
        ]
        client = make_client(state="idle", todos=todos)
        result = detector.detect(session, client)
        assert result.detected is True
        assert "1 incomplete" in result.reason
        assert result.severity == "high"

    def test_no_detect_when_all_completed(self):
        detector = IdleIncompleteDetector()
        session = make_session()
        todos = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "completed"},
        ]
        client = make_client(state="idle", todos=todos)
        result = detector.detect(session, client)
        assert result.detected is False

    def test_no_detect_when_busy(self):
        detector = IdleIncompleteDetector()
        session = make_session()
        todos = [{"id": "1", "content": "Task 1", "status": "pending"}]
        client = make_client(state="busy", todos=todos)
        result = detector.detect(session, client)
        assert result.detected is False

    def test_no_detect_when_no_todos(self):
        detector = IdleIncompleteDetector()
        session = make_session()
        client = make_client(state="idle", todos=[])
        result = detector.detect(session, client)
        assert result.detected is False


class TestPrematureEndDetector:
    def test_detects_done_with_incomplete_todos(self):
        detector = PrematureEndDetector()
        session = make_session()
        todos = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "pending"},
            {"id": "3", "content": "Task 3", "status": "pending"},
        ]
        client = make_client(state="done", todos=todos)
        result = detector.detect(session, client)
        assert result.detected is True
        assert "2 incomplete" in result.reason
        assert result.severity == "critical"

    def test_detects_completed_with_incomplete_todos(self):
        detector = PrematureEndDetector()
        session = make_session()
        todos = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "in_progress"},
        ]
        client = make_client(state="completed", todos=todos)
        result = detector.detect(session, client)
        assert result.detected is True

    def test_detects_finished_with_incomplete_todos(self):
        detector = PrematureEndDetector()
        session = make_session()
        todos = [
            {"id": "1", "content": "Task 1", "status": "pending"},
        ]
        client = make_client(state="finished", todos=todos)
        result = detector.detect(session, client)
        assert result.detected is True

    def test_no_detect_when_all_completed(self):
        detector = PrematureEndDetector()
        session = make_session()
        todos = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "completed"},
        ]
        client = make_client(state="done", todos=todos)
        result = detector.detect(session, client)
        assert result.detected is False

    def test_no_detect_when_idle(self):
        detector = PrematureEndDetector()
        session = make_session()
        todos = [{"id": "1", "content": "Task 1", "status": "pending"}]
        client = make_client(state="idle", todos=todos)
        result = detector.detect(session, client)
        assert result.detected is False

    def test_no_detect_when_no_todos(self):
        detector = PrematureEndDetector()
        session = make_session()
        client = make_client(state="done", todos=[])
        result = detector.detect(session, client)
        assert result.detected is False


class TestSessionInvalidDetector:
    def test_detects_missing_session(self):
        detector = SessionInvalidDetector(grace_period=0)
        session = make_session()
        client = MagicMock()
        client.get_session.return_value = {}
        result = detector.detect(session, client)
        assert result.detected is True
        assert "empty response" in result.reason.lower()
        assert result.severity == "critical"

    def test_detects_aborted_session(self):
        detector = SessionInvalidDetector()
        session = make_session()
        client = MagicMock()
        client.get_session.return_value = {"state": "aborted"}
        result = detector.detect(session, client)
        assert result.detected is True
        assert "aborted" in result.reason.lower()
        assert result.severity == "high"

    def test_no_detect_when_valid(self):
        detector = SessionInvalidDetector()
        session = make_session()
        client = MagicMock()
        client.get_session.return_value = {"state": "idle"}
        result = detector.detect(session, client)
        assert result.detected is False

    def test_handles_404_error(self):
        detector = SessionInvalidDetector()
        session = make_session()
        client = MagicMock()
        client.get_session.side_effect = Exception("404 Not Found")
        result = detector.detect(session, client)
        assert result.detected is True

    def test_handles_connection_error(self):
        detector = SessionInvalidDetector()
        session = make_session()
        client = MagicMock()
        client.get_session.side_effect = ConnectionError("Connection refused")
        result = detector.detect(session, client)
        assert result.detected is True

    def test_no_session_id(self):
        detector = SessionInvalidDetector()
        session = make_session(session_id=None)
        client = MagicMock()
        result = detector.detect(session, client)
        assert result.detected is False

    def test_grace_period(self):
        import time

        detector = SessionInvalidDetector(grace_period=10)
        session = make_session()
        session.last_activity_time = time.time() - 2
        client = MagicMock()
        client.get_session.return_value = {}
        result = detector.detect(session, client)
        assert result.detected is False

    def test_handles_404_error(self):
        detector = SessionInvalidDetector()
        session = make_session()
        client = MagicMock()
        client.get_session.side_effect = Exception("404 Not Found")
        result = detector.detect(session, client)
        assert result.detected is True

    def test_handles_connection_error(self):
        detector = SessionInvalidDetector()
        session = make_session()
        client = MagicMock()
        client.get_session.side_effect = ConnectionError("Connection refused")
        result = detector.detect(session, client)
        assert result.detected is True

    def test_no_session_id(self):
        detector = SessionInvalidDetector()
        session = make_session(session_id=None)
        client = MagicMock()
        result = detector.detect(session, client)
        assert result.detected is False

    def test_grace_period(self):
        import time

        detector = SessionInvalidDetector(grace_period=10)
        session = make_session()
        session.last_activity_time = time.time() - 2
        client = MagicMock()
        client.get_session.return_value = {}
        result = detector.detect(session, client)
        assert result.detected is False
