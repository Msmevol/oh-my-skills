"""
OpenCode HTTP API Client
封装所有与 opencode serve 的 HTTP 交互
"""

import logging
import time
from typing import Optional

import requests
import sseclient

logger = logging.getLogger(__name__)


class OpenCodeClient:
    """OpenCode Server HTTP API 客户端"""

    def __init__(
        self,
        base_url: str = "http://localhost:4096",
        timeout: int = 30,
        retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """带重试的 HTTP 请求封装"""
        kwargs.setdefault("timeout", self.timeout)

        last_error = None
        for attempt in range(self.retries):
            try:
                response = self.session.request(method, self._url(path), **kwargs)
                response.raise_for_status()

                # 有些端点返回空 body
                if not response.text:
                    return {}

                return response.json()
            except requests.exceptions.ConnectionError as e:
                last_error = e
                logger.warning(
                    f"Connection error (attempt {attempt + 1}/{self.retries}): {e}"
                )
                time.sleep(2**attempt)
            except requests.exceptions.Timeout as e:
                last_error = e
                logger.warning(f"Timeout (attempt {attempt + 1}/{self.retries}): {e}")
                time.sleep(2**attempt)
            except requests.exceptions.HTTPError as e:
                logger.error(f"HTTP error: {e}")
                raise

        raise ConnectionError(f"Failed after {self.retries} attempts: {last_error}")

    def health_check(self) -> bool:
        """检查 server 是否存活"""
        try:
            result = self._request("GET", "/global/health")
            return result.get("healthy", False)
        except Exception:
            return False

    def create_session(self, title: str, parent_id: Optional[str] = None) -> str:
        """创建新 session，返回 session ID"""
        body = {"title": title}
        if parent_id:
            body["parentID"] = parent_id

        result = self._request("POST", "/session", json=body)
        session_id = result.get("id")
        if not session_id:
            raise ValueError(f"Failed to create session: {result}")
        logger.info(f"Created session: {session_id} (title: {title})")
        return session_id

    def send_message(
        self,
        session_id: str,
        message: str,
        agent: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict:
        """发送消息并等待响应"""
        body = {"message": message}
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model

        logger.info(
            f"Sending message to session {session_id} (agent: {agent or 'default'})"
        )
        result = self._request("POST", f"/session/{session_id}/message", json=body)
        return result

    def send_message_async(
        self, session_id: str, message: str, agent: Optional[str] = None
    ) -> None:
        """异步发送消息（不等待响应）"""
        body = {"message": message}
        if agent:
            body["agent"] = agent

        self._request("POST", f"/session/{session_id}/prompt_async", json=body)

    def get_session_status(self) -> dict:
        """获取所有 session 的状态
        返回: {session_id: {state, ...}}
        """
        result = self._request("GET", "/session/status")
        return result

    def get_session(self, session_id: str) -> dict:
        """获取单个 session 详情"""
        return self._request("GET", f"/session/{session_id}")

    def get_todo(self, session_id: str) -> list:
        """获取 session 的 todolist
        返回: [{content, status, ...}, ...]
        """
        try:
            result = self._request("GET", f"/session/{session_id}/todo")
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning(f"Failed to get todo for session {session_id}: {e}")
            return []

    def abort_session(self, session_id: str) -> bool:
        """中止 running session"""
        try:
            self._request("POST", f"/session/{session_id}/abort")
            logger.info(f"Aborted session {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to abort session {session_id}: {e}")
            return False

    def delete_session(self, session_id: str) -> bool:
        """删除 session"""
        try:
            self._request("DELETE", f"/session/{session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False

    def list_sessions(self) -> list:
        """列出所有 session"""
        return self._request("GET", "/session")

    def list_agents(self) -> list:
        """列出所有可用 agent"""
        return self._request("GET", "/agent")

    def subscribe_events(self):
        """订阅 SSE 事件流
        返回事件迭代器
        """
        try:
            response = self.session.get(
                self._url("/event"),
                stream=True,
                timeout=None,  # SSE 需要持久连接
            )
            response.raise_for_status()
            client = sseclient.SSEClient(response)
            for event in client.events():
                yield event
        except Exception as e:
            logger.error(f"SSE connection error: {e}")
            raise

    def execute_command(
        self, session_id: str, command: str, agent: Optional[str] = None
    ) -> dict:
        """执行 slash 命令"""
        body = {"command": command}
        if agent:
            body["agent"] = agent
        return self._request("POST", f"/session/{session_id}/command", json=body)

    def get_messages(self, session_id: str, limit: Optional[int] = None) -> list:
        """获取 session 的消息历史"""
        params = {}
        if limit:
            params["limit"] = limit
        return self._request("GET", f"/session/{session_id}/message", params=params)

    def get_diff(self, session_id: str) -> list:
        """获取 session 产生的文件 diff"""
        return self._request("GET", f"/session/{session_id}/diff")
