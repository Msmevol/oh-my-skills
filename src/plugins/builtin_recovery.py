"""
Built-in Recovery Plugins - 内置恢复插件

提供恢复策略：
1. RestartRecovery - 重启 session 并发送继续消息（优化版）
"""

import logging
import time
from typing import Optional, Dict, Any

from . import RecoveryPlugin
from ..agent_session import AgentSession

logger = logging.getLogger(__name__)


class RestartRecovery(RecoveryPlugin):
    """重启恢复插件

    流程：
    1. abort 当前 session
    2. 获取已完成和未完成的 todos
    3. 创建新 session
    4. 发送优化的继续消息（包含上下文）
    """

    def __init__(self, agent_name: str = "skill-executor"):
        self._agent_name = agent_name

    @property
    def name(self) -> str:
        return "restart_recovery"

    def recover(
        self, session, client, context: Dict[str, Any]
    ) -> Optional[AgentSession]:
        restart_count = context.get("restart_count", 0)
        detection_result = context.get("detection_result")

        logger.info(
            f"RestartRecovery: restarting session (attempt {restart_count + 1})"
        )

        old_session_id = session.session_id if session else None

        completed_todos = []
        remaining_todos = []

        if old_session_id:
            try:
                client.abort_session(old_session_id)
            except Exception as e:
                logger.warning(f"Failed to abort old session: {e}")

            try:
                todos = client.get_todo(old_session_id)
                completed_todos = [t for t in todos if t.get("status") == "completed"]
                remaining_todos = [t for t in todos if t.get("status") != "completed"]
            except Exception as e:
                logger.warning(f"Failed to get todos: {e}")

        new_title = f"{self._agent_name}-recovery-{restart_count + 1}"
        try:
            new_session_id = client.create_session(new_title)
        except Exception as e:
            logger.error(f"Failed to create new session: {e}")
            return None

        continue_msg = self._build_continue_message(
            completed_todos, remaining_todos, restart_count + 1, detection_result
        )

        new_session = AgentSession(
            client,
            agent_name=self._agent_name,
            session_id=new_session_id,
            max_retries=3,
        )

        try:
            client.send_message(new_session_id, continue_msg, agent=self._agent_name)
        except Exception as e:
            logger.error(f"Failed to send continue message: {e}")
            return None

        logger.info(
            f"RestartRecovery: new session {new_session_id} created, "
            f"{len(completed_todos)} completed, {len(remaining_todos)} remaining"
        )

        return new_session

    def _build_continue_message(
        self,
        completed_todos: list,
        remaining_todos: list,
        restart_count: int,
        detection_result=None,
    ) -> str:
        completed_list = "\n".join(
            [f"- [x] {t.get('content', 'unknown')}" for t in completed_todos]
        )
        remaining_list = "\n".join(
            [f"- [ ] {t.get('content', 'unknown')}" for t in remaining_todos]
        )

        detection_info = ""
        if detection_result:
            detection_info = f"\n中断原因：{detection_result.reason}\n"

        return (
            f"⚠️ 之前的执行被中断（第 {restart_count} 次重启）。{detection_info}\n\n"
            f"✅ 已完成的任务:\n{completed_list or '无'}\n\n"
            f"📋 剩余必须完成的任务:\n{remaining_list}\n\n"
            f"⚡ 重要要求：\n"
            f"1. 你必须继续完成以上所有剩余任务，不要跳过\n"
            f"2. 每完成一个任务就立即用 todowrite 标记为 completed\n"
            f"3. 不要提前结束，不要说'任务已完成'除非所有任务都真正完成\n"
            f"4. 如果遇到困难，尝试多种方法解决\n"
            f"5. 只有当所有任务都完成后才能结束\n\n"
            f"现在开始继续执行剩余任务。"
        )
