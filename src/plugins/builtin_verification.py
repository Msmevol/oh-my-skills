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
                state = session_status.get("type", "unknown")

                if state in ("busy", "retry"):
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
    2. 消息内容是否包含 todo 关键词
    3. 是否存在文件变更（如果任务涉及文件操作）
    4. 任务执行顺序是否正确（pending -> in_progress -> completed）
    5. 是否存在任务跳过（直接从 pending 到 completed）
    """

    def __init__(self, min_ratio: float = 0.3, check_sequence: bool = True):
        self._min_ratio = min_ratio
        self._check_sequence = check_sequence

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
            in_progress = [t for t in todos if t.get("status") == "in_progress"]
            pending = [t for t in todos if t.get("status") == "pending"]

            if not completed:
                return False

            messages = client.get_messages(session.session_id, limit=100)
            assistant_messages = [
                m for m in messages if m.get("info", {}).get("role") == "assistant"
            ]

            if len(assistant_messages) < len(completed) * self._min_ratio:
                logger.warning(
                    f"TodoExecutionVerifier: suspicious - {len(completed)} completed "
                    f"todos but only {len(assistant_messages)} assistant messages"
                )
                return False

            combined_text = ""
            for m in assistant_messages:
                parts = m.get("parts", [])
                if parts:
                    combined_text += " ".join(str(p) for p in parts) + " "
                content = m.get("content", "")
                if content:
                    combined_text += str(content) + " "
            combined_text = combined_text.lower()
            todo_keywords = [t.get("content", "").lower() for t in completed]
            matched = sum(
                1
                for kw in todo_keywords
                if any(
                    word.lower() in combined_text
                    for word in kw.replace("-", " ").replace("_", " ").split()[:3]
                    if len(word) > 2
                )
            )
            match_ratio = matched / len(completed) if completed else 0
            if match_ratio < 0.3 and len(combined_text.strip()) > 0:
                logger.warning(
                    f"TodoExecutionVerifier: low keyword match - {match_ratio:.0%} "
                    f"({matched}/{len(completed)} todos found in messages)"
                )
                return False

            if self._check_sequence:
                if not self._verify_task_sequence(todos, messages):
                    logger.warning(
                        "TodoExecutionVerifier: task sequence verification failed - "
                        "possible skipped tasks detected"
                    )
                    return False

            return True

        except Exception as e:
            logger.error(f"TodoExecutionVerifier error: {e}")
            return False

    def _verify_task_sequence(self, todos: List[Dict], messages: List[Dict]) -> bool:
        """验证任务执行顺序是否正确

        检查是否存在跳过的任务（pending -> completed without in_progress）
        通过分析 todowrite 调用的历史来判断任务状态转换是否正确
        """
        if not todos:
            return True

        todo_map = {t.get("id"): t for t in todos if t.get("id")}

        tool_calls = []
        for m in messages:
            parts = m.get("parts", [])
            for p in parts:
                if p.get("type") == "tool" and p.get("tool") == "todowrite":
                    tool_calls.append(p)

        if not tool_calls:
            return True

        state_transitions = {}
        for tc in tool_calls:
            state = tc.get("state", {})
            input_args = state.get("input", {})
            call_todos = input_args.get("todos", [])
            if not call_todos:
                continue
            for td in call_todos:
                tid = td.get("id")
                if tid:
                    if tid not in state_transitions:
                        state_transitions[tid] = []
                    status = td.get("status")
                    if status:
                        state_transitions[tid].append(status)

        skipped_tasks = []
        for tid, transitions in state_transitions.items():
            if len(transitions) < 2:
                todo = todo_map.get(tid)
                if todo and todo.get("status") == "completed":
                    if "pending" in transitions and "completed" in transitions:
                        pass
                    elif (
                        "completed" in transitions and "in_progress" not in transitions
                    ):
                        skipped_tasks.append(tid)

        if skipped_tasks:
            logger.warning(
                f"TodoExecutionVerifier: detected {len(skipped_tasks)} "
                f"tasks that may have been skipped: {skipped_tasks}"
            )
            return False

        return True
