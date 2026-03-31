"""
Agent Session Manager
管理单个 agent session 的生命周期、状态检测和自动恢复
"""

import logging
import time
from typing import Optional

from .opencode_client import OpenCodeClient

logger = logging.getLogger(__name__)


class SessionError(Exception):
    """Session 操作异常"""

    pass


class MaxRetriesExceeded(SessionError):
    """超过最大重试次数"""

    pass


class AgentSession:
    """管理单个 agent session 的生命周期"""

    def __init__(
        self,
        client: OpenCodeClient,
        agent_name: str,
        session_id: Optional[str] = None,
        max_retries: int = 3,
        stuck_threshold: int = 300,
    ):
        self.client = client
        self.agent_name = agent_name
        self.session_id = session_id
        self.max_retries = max_retries
        self.stuck_threshold = stuck_threshold
        self.retry_count = 0
        self.last_activity_time = time.time()
        self.state = "idle"  # idle | busy | error | done
        self._last_todo_count = 0
        self._last_completed_count = 0

    def send(self, message: str, **kwargs) -> dict:
        """发送消息到 session"""
        if not self.session_id:
            raise SessionError("No active session. Call create_session() first.")

        try:
            result = self.client.send_message(
                self.session_id, message, agent=self.agent_name, **kwargs
            )
            self.update_activity()
            return result
        except Exception as e:
            self.state = "error"
            raise SessionError(f"Failed to send message: {e}") from e

    def create_session(self, title: str, parent_id: Optional[str] = None) -> str:
        """创建新 session"""
        self.session_id = self.client.create_session(title, parent_id)
        self.state = "idle"
        self.retry_count = 0
        self.update_activity()
        return self.session_id

    def is_stuck(self, timeout: Optional[int] = None) -> bool:
        """检测 session 是否卡死

        判断逻辑：
        1. 如果 state 是 error → 卡死
        2. 如果 busy 超过阈值 → 卡死
        3. 如果 idle 但还有未完成的 todo → 模型偷懒
        """
        timeout = timeout or self.stuck_threshold

        if not self.session_id:
            return False

        try:
            # 获取 session 状态
            all_status = self.client.get_session_status()
            session_status = all_status.get(self.session_id, {})
            current_state = session_status.get("state", "unknown")
            self.state = current_state

            # error 状态
            if current_state == "error":
                logger.warning(f"Session {self.session_id} in error state")
                return True

            # busy 超时
            if current_state == "busy":
                elapsed = time.time() - self.last_activity_time
                if elapsed > timeout:
                    logger.warning(
                        f"Session {self.session_id} busy for {elapsed:.0f}s (threshold: {timeout}s)"
                    )
                    return True

            # idle 但有未完成的任务
            if current_state == "idle":
                todos = self.client.get_todo(self.session_id)
                if todos:
                    incomplete = [t for t in todos if t.get("status") != "completed"]
                    if incomplete:
                        logger.warning(
                            f"Session {self.session_id} idle with {len(incomplete)} incomplete todos"
                        )
                        return True

            return False

        except Exception as e:
            logger.error(f"Error checking stuck status: {e}")
            # 无法获取状态时，保守处理：如果长时间没活动就认为卡死
            return (time.time() - self.last_activity_time) > timeout

    def restart(self, continue_msg: Optional[str] = None) -> str:
        """重启 session

        流程：
        1. abort 当前 session
        2. 获取未完成的 todo
        3. 创建新 session
        4. 发送继续指令
        """
        if self.retry_count >= self.max_retries:
            raise MaxRetriesExceeded(
                f"Max retries ({self.max_retries}) exceeded for agent {self.agent_name}"
            )

        logger.info(
            f"Restarting session {self.session_id} (attempt {self.retry_count + 1})"
        )

        # 1. 获取未完成的 todo
        remaining_todos = []
        if self.session_id:
            try:
                self.client.abort_session(self.session_id)
                todos = self.client.get_todo(self.session_id)
                remaining_todos = [t for t in todos if t.get("status") != "completed"]
            except Exception as e:
                logger.warning(f"Failed to get remaining todos: {e}")

        # 2. 创建新 session
        new_title = f"{self.agent_name}-restart-{self.retry_count + 1}"
        new_session_id = self.client.create_session(new_title)

        # 3. 构建继续执行的消息
        if continue_msg:
            message = continue_msg
        elif remaining_todos:
            todo_list = "\n".join(
                [f"- {t.get('content', 'unknown')}" for t in remaining_todos]
            )
            completed = [
                t
                for t in self.client.get_todo(self.session_id)
                if t.get("status") == "completed"
            ]
            completed_list = "\n".join(
                [f"- {t.get('content', 'unknown')}" for t in completed]
            )

            message = (
                f"之前的 session 意外中断，请继续执行剩余任务。\n\n"
                f"已完成的任务:\n{completed_list}\n\n"
                f"剩余任务:\n{todo_list}\n\n"
                f"请继续执行剩余任务，不要跳过。每完成一个就更新 todolist。"
            )
        else:
            message = "请继续执行当前的任务。"

        # 4. 发送消息
        self.session_id = new_session_id
        self.retry_count += 1
        self.state = "idle"

        try:
            self.client.send_message(new_session_id, message, agent=self.agent_name)
        except Exception as e:
            logger.error(f"Failed to send continue message: {e}")

        self.update_activity()
        return new_session_id

    def get_progress(self) -> dict:
        """获取当前进度

        返回: {total, completed, pending, percentage}
        """
        if not self.session_id:
            return {"total": 0, "completed": 0, "pending": 0, "percentage": 0}

        todos = self.client.get_todo(self.session_id)
        if not todos:
            return {"total": 0, "completed": 0, "pending": 0, "percentage": 0}

        total = len(todos)
        completed = sum(1 for t in todos if t.get("status") == "completed")
        pending = total - completed
        percentage = (completed / total * 100) if total > 0 else 0

        return {
            "total": total,
            "completed": completed,
            "pending": pending,
            "percentage": round(percentage, 1),
        }

    def is_done(self) -> bool:
        """检查是否所有任务都真正完成了

        增强验证：
        1. 检查 todos 是否全部标记为 completed
        2. 验证 session 状态是否为 idle（真正结束）
        3. 检查消息历史确认模型没有偷懒
        """
        if not self.session_id:
            return False

        progress = self.get_progress()
        if progress["total"] == 0:
            return False

        if progress["pending"] > 0:
            return False

        # 额外验证：检查 session 是否真的 idle 了
        try:
            all_status = self.client.get_session_status()
            session_status = all_status.get(self.session_id, {})
            current_state = session_status.get("state", "unknown")

            # 如果还在 busy，说明还在执行中，不算 done
            if current_state == "busy":
                return False

            # idle 状态且 todos 全部完成，才认为真正 done
            return current_state == "idle"

        except Exception as e:
            logger.warning(f"Error verifying completion: {e}")
            # 无法验证时，保守处理：认为未完成
            return False

    def verify_todos_actually_executed(self) -> dict:
        """验证 todos 是否真正被执行，而非仅仅标记为 completed

        返回: {
            "all_done": bool,
            "suspicious_todos": list,  # 可疑的 todos（标记完成但可能未真正执行）
            "verification_passed": bool
        }
        """
        if not self.session_id:
            return {
                "all_done": False,
                "suspicious_todos": [],
                "verification_passed": False,
            }

        todos = self.client.get_todo(self.session_id)
        if not todos:
            return {
                "all_done": False,
                "suspicious_todos": [],
                "verification_passed": False,
            }

        incomplete = [t for t in todos if t.get("status") != "completed"]
        completed = [t for t in todos if t.get("status") == "completed"]

        # 检查是否有可疑的 todos（刚创建就标记为完成，执行时间过短）
        suspicious = []
        try:
            messages = self.client.get_messages(self.session_id, limit=50)
            assistant_messages = [
                m for m in messages if m.get("info", {}).get("role") == "assistant"
            ]

            # 如果完成的消息数量远少于 completed todos 数量，可能有问题
            if len(assistant_messages) < len(completed) * 0.5:
                suspicious = completed

        except Exception as e:
            logger.warning(f"Error verifying execution: {e}")

        return {
            "all_done": len(incomplete) == 0,
            "suspicious_todos": suspicious,
            "verification_passed": len(incomplete) == 0 and len(suspicious) == 0,
        }

    def update_activity(self):
        """更新最后活动时间"""
        self.last_activity_time = time.time()
        self.state = "idle"

    def get_status(self) -> dict:
        """获取 session 状态信息"""
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "state": self.state,
            "retry_count": self.retry_count,
            "last_activity": self.last_activity_time,
            "progress": self.get_progress(),
        }

    def __repr__(self):
        return (
            f"AgentSession(agent={self.agent_name}, "
            f"session={self.session_id}, "
            f"state={self.state}, "
            f"retries={self.retry_count})"
        )
