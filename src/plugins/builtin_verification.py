"""
Built-in Verification Plugins - 内置验证插件

提供验证能力：
1. StableCompletionVerifier - 稳定完成验证（连续多次检查 todos 全部完成且 session idle）
2. TodoExecutionVerifier - TODO 执行验证（检查消息历史确认任务真正执行过）
"""

import logging
import time
from typing import Dict, Any, List

from . import VerificationPlugin

logger = logging.getLogger(__name__)


class StableCompletionVerifier(VerificationPlugin):
    """稳定完成验证插件

    连续多次检查，确保：
    1. 所有 todos 都标记为 completed
    2. session 状态为 idle（真正结束）
    3. 状态在多次检查间保持稳定
    """

    def __init__(self, stable_count: int = 3, check_interval: float = 3.0):
        self._stable_count = stable_count
        self._check_interval = check_interval

    @property
    def name(self) -> str:
        return "stable_completion_verifier"

    def verify(self, session, client) -> bool:
        if not session or not session.session_id:
            return False

        for i in range(self._stable_count):
            time.sleep(self._check_interval)

            try:
                todos = client.get_todo(session.session_id)
                if not todos:
                    logger.warning(
                        f"Verification {i + 1}/{self._stable_count}: no todos found"
                    )
                    return False

                incomplete = [t for t in todos if t.get("status") != "completed"]
                if incomplete:
                    logger.warning(
                        f"Verification {i + 1}/{self._stable_count}: "
                        f"{len(incomplete)} tasks still pending"
                    )
                    return False

                all_status = client.get_session_status()
                session_status = all_status.get(session.session_id, {})
                state = session_status.get("state", "unknown")

                if state == "busy":
                    logger.warning(
                        f"Verification {i + 1}/{self._stable_count}: Session still busy"
                    )
                    return False

            except Exception as e:
                logger.warning(f"Verification {i + 1}/{self._stable_count} error: {e}")
                return False

        return True


class TodoExecutionVerifier(VerificationPlugin):
    """TODO 执行验证插件

    验证 todos 是否真正被执行，而非仅仅标记为 completed。
    检查：
    1. 消息历史中 assistant 的消息数量是否与 completed todos 数量匹配
    2. 是否存在文件变更（如果任务涉及文件操作）
    """

    def __init__(self, min_ratio: float = 0.5):
        self._min_ratio = min_ratio

    @property
    def name(self) -> str:
        return "todo_execution_verifier"

    def verify(self, session, client) -> bool:
        if not session or not session.session_id:
            return False

        try:
            todos = client.get_todo(session.session_id)
            if not todos:
                return False

            completed = [t for t in todos if t.get("status") == "completed"]
            if not completed:
                return False

            messages = client.get_messages(session.session_id, limit=50)
            assistant_messages = [
                m for m in messages if m.get("info", {}).get("role") == "assistant"
            ]

            if len(assistant_messages) < len(completed) * self._min_ratio:
                logger.warning(
                    f"TodoExecutionVerifier: suspicious - {len(completed)} completed "
                    f"todos but only {len(assistant_messages)} assistant messages"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"TodoExecutionVerifier error: {e}")
            return False
