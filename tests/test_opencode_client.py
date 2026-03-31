"""
Unit tests for OpenCodeClient
"""

import pytest
from unittest.mock import MagicMock, patch
import requests

from src.opencode_client import OpenCodeClient


@pytest.fixture
def client():
    return OpenCodeClient("http://localhost:14096", timeout=5, retries=2)


class TestOpenCodeClient:
    @patch("src.opencode_client.requests.Session.request")
    def test_health_check_success(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"healthy": True, "version": "0.1.0"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        assert client.health_check() == True

    @patch("src.opencode_client.requests.Session.request")
    def test_health_check_failure(self, mock_request, client):
        mock_request.side_effect = requests.exceptions.ConnectionError()

        assert client.health_check() == False

    @patch("src.opencode_client.requests.Session.request")
    def test_create_session(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "session-123"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        session_id = client.create_session("test plan")
        assert session_id == "session-123"

    @patch("src.opencode_client.requests.Session.request")
    def test_create_session_with_parent(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": "session-456"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        session_id = client.create_session("child", parent_id="parent-1")
        assert session_id == "session-456"

        # 验证 parentID 被发送
        call_args = mock_request.call_args
        assert call_args.kwargs["json"]["parentID"] == "parent-1"

    @patch("src.opencode_client.requests.Session.request")
    def test_send_message(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        result = client.send_message("session-1", "hello", agent="planner")
        assert result == {"status": "ok"}

        call_args = mock_request.call_args
        assert call_args.kwargs["json"]["message"] == "hello"
        assert call_args.kwargs["json"]["agent"] == "planner"

    @patch("src.opencode_client.requests.Session.request")
    def test_get_session_status(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "session-1": {"state": "busy"},
            "session-2": {"state": "idle"},
        }
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        status = client.get_session_status()
        assert status["session-1"]["state"] == "busy"
        assert status["session-2"]["state"] == "idle"

    @patch("src.opencode_client.requests.Session.request")
    def test_get_todo(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"content": "task 1", "status": "completed"},
            {"content": "task 2", "status": "pending"},
        ]
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        todos = client.get_todo("session-1")
        assert len(todos) == 2
        assert todos[0]["status"] == "completed"
        assert todos[1]["status"] == "pending"

    @patch("src.opencode_client.requests.Session.request")
    def test_get_todo_empty(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        todos = client.get_todo("session-1")
        assert todos == []

    @patch("src.opencode_client.requests.Session.request")
    def test_abort_session(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        assert client.abort_session("session-1") == True

    @patch("src.opencode_client.requests.Session.request")
    def test_delete_session(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        assert client.delete_session("session-1") == True

    @patch("src.opencode_client.requests.Session.request")
    def test_list_sessions(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = [{"id": "s1"}, {"id": "s2"}]
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        sessions = client.list_sessions()
        assert len(sessions) == 2

    @patch("src.opencode_client.requests.Session.request")
    def test_list_agents(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = [{"name": "planner"}, {"name": "reviewer"}]
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        agents = client.list_agents()
        assert len(agents) == 2

    @patch("src.opencode_client.requests.Session.request")
    def test_http_error_raises(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Error"
        )
        mock_request.return_value = mock_response

        with pytest.raises(requests.exceptions.HTTPError):
            client.get_session_status()

    @patch("src.opencode_client.requests.Session.request")
    def test_retry_on_connection_error(self, mock_request, client):
        mock_request.side_effect = requests.exceptions.ConnectionError()

        with pytest.raises(ConnectionError):
            client.get_session_status()

        # 验证重试次数
        assert mock_request.call_count == 2  # retries=2

    @patch("src.opencode_client.requests.Session.request")
    def test_get_messages(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"info": {"role": "user"}, "parts": []},
            {
                "info": {"role": "assistant"},
                "parts": [{"type": "text", "content": "hello"}],
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        messages = client.get_messages("session-1", limit=10)
        assert len(messages) == 2

    @patch("src.opencode_client.requests.Session.request")
    def test_get_diff(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = [{"path": "file.py", "diff": "+hello"}]
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        diff = client.get_diff("session-1")
        assert len(diff) == 1
        assert diff[0]["path"] == "file.py"

    @patch("src.opencode_client.requests.Session.request")
    def test_execute_command(self, mock_request, client):
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_request.return_value = mock_response

        result = client.execute_command("session-1", "/undo", agent="planner")
        assert result == {"status": "ok"}

        call_args = mock_request.call_args
        assert call_args.kwargs["json"]["command"] == "/undo"
