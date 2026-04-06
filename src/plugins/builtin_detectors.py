"""
Built-in Detection Plugins - 内置检测插件

提供四种核心检测能力：
1. StuckDetector - 卡死检测（busy 超时、retry 超时）
2. IdleIncompleteDetector - 偷懒检测（idle 但 todos 未完成）
3. PrematureEndDetector - 提前结束检测（session 从 status 消失但 todos 未完成）
4. SessionInvalidDetector - 会话失效检测（session 不存在、连接断开）

注意：OpenCode API /session/status 返回 {session_id: {type: "busy"|"idle"|"retry"}}
不存在 done/error/completed 等状态。session 执行完成后会从 status 列表中消失。
"""

import logging
import time
from typing import Optional, List, Dict, Any

from . import DetectionPlugin, DetectionResult

logger = logging.getLogger(__name__)


class StuckDetector(DetectionPlugin):
    """卡死检测插件

    检测场景：
    1. session 状态为 busy 且超过阈值时间
    2. session 状态为 retry 且超过阈值时间（持续重试视为卡死）
    """

    def __init__(self, timeout: int = 300):
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "stuck_detector"

    def detect(self, session, client) -> DetectionResult:
        if not session or not session.session_id:
            return DetectionResult(detected=False)

        try:
            all_status = client.get_session_status()
            session_status = all_status.get(session.session_id, {})
            current_type = session_status.get("type", "unknown")

            # retry 状态（持续重试视为卡死）
            if current_type == "retry":
                elapsed = time.time() - session.last_activity_time
                if elapsed > self._timeout:
                    retry_msg = session_status.get("message", "")
                    return DetectionResult(
                        detected=True,
                        reason=f"Session in retry for {elapsed:.0f}s: {retry_msg}",
                        severity="medium",
                        details={
                            "type": current_type,
                            "elapsed": elapsed,
                            "threshold": self._timeout,
                        },
                    )

            if current_type == "busy":
                elapsed = time.time() - session.last_activity_time
                if elapsed > self._timeout:
                    return DetectionResult(
                        detected=True,
                        reason=f"Session busy for {elapsed:.0f}s (threshold: {self._timeout}s)",
                        severity="medium",
                        details={
                            "type": current_type,
                            "elapsed": elapsed,
                            "threshold": self._timeout,
                        },
                    )

            return DetectionResult(detected=False)

        except Exception as e:
            logger.error(f"StuckDetector error: {e}")
            elapsed = time.time() - session.last_activity_time
            if elapsed > self._timeout:
                return DetectionResult(
                    detected=True,
                    reason=f"Cannot get status, no activity for {elapsed:.0f}s",
                    severity="high",
                    details={"error": str(e)},
                )
            return DetectionResult(detected=False)


class IdleIncompleteDetector(DetectionPlugin):
    """偷懒检测插件

    检测场景：
    - session 状态为 idle
    - 但还有未完成的 todos
    - 分析消息历史，确认是否所有任务都被尝试执行过
    """

    @property
    def name(self) -> str:
        return "idle_incomplete_detector"

    def detect(self, session, client) -> DetectionResult:
        if not session or not session.session_id:
            return DetectionResult(detected=False)

        try:
            all_status = client.get_session_status()
            session_status = all_status.get(session.session_id, {})
            current_type = session_status.get("type", "unknown")

            if current_type != "idle":
                return DetectionResult(detected=False)

            todos = client.get_todo(session.session_id)
            if not todos:
                return DetectionResult(detected=False)

            incomplete = [t for t in todos if t.get("status") != "completed"]
            if incomplete:
                logger.warning(
                    f"IdleIncompleteDetector: detected idle state with {len(incomplete)} "
                    f"incomplete todos out of {len(todos)} total"
                )

                messages = client.get_messages(session.session_id, limit=100)
                attempt_analysis = self._analyze_task_attempts(incomplete, messages)

                if attempt_analysis["unattempted_count"] > 0:
                    return DetectionResult(
                        detected=True,
                        reason=f"Session idle with {len(incomplete)} incomplete todos, "
                        f"{attempt_analysis['unattempted_count']} tasks never attempted",
                        severity="high",
                        details={
                            "type": current_type,
                            "incomplete_count": len(incomplete),
                            "total_count": len(todos),
                            "unattempted": attempt_analysis["unattempted"],
                        },
                    )

                return DetectionResult(
                    detected=True,
                    reason=f"Session idle with {len(incomplete)} incomplete todos",
                    severity="medium",
                    details={
                        "type": current_type,
                        "incomplete_count": len(incomplete),
                        "total_count": len(todos),
                    },
                )

            return DetectionResult(detected=False)

        except Exception as e:
            logger.error(f"IdleIncompleteDetector error: {e}")
            return DetectionResult(detected=False)

    def _analyze_task_attempts(
        self, incomplete_todos: List[Dict], messages: List[Dict]
    ) -> Dict:
        """分析每个未完成任务是否被尝试执行过"""
        tool_calls = []
        for m in messages:
            parts = m.get("parts", [])
            for p in parts:
                if p.get("type") == "tool":
                    tool_calls.append(p)

        attempted_content = set()
        for tc in tool_calls:
            state = tc.get("state", {})
            input_args = state.get("input", {})
            call_todos = input_args.get("todos", [])
            if call_todos:
                for td in call_todos:
                    if td.get("id"):
                        attempted_content.add(td.get("id"))

        unattempted = []
        for td in incomplete_todos:
            if td.get("id") not in attempted_content:
                unattempted.append(td.get("content", "unknown")[:50])

        return {
            "unattempted_count": len(unattempted),
            "unattempted": unattempted,
        }


class PrematureEndDetector(DetectionPlugin):
    """提前结束检测插件

    检测场景：
    - session 从 /session/status 列表中消失（意味着已完成或被清理）
    - 但还有未完成的 todos
    - 这是小模型最常见的问题：模型停止执行但任务没做完

    增强检测：
    - 分析消息历史，确认每个任务是否都有对应的执行步骤
    - 检测是否存在跳过的任务（未尝试就标记完成）
    - 检查任务执行步骤与任务数量是否匹配

    注意：OpenCode API 没有 done/completed 状态，
    session 执行完成或停止后会从 status 列表中移除。
    """

    def __init__(self, grace_period: int = 45, min_steps_per_task: int = 2):
        self._grace_period = grace_period
        self._min_steps_per_task = min_steps_per_task

    @property
    def name(self) -> str:
        return "premature_end_detector"

    def detect(self, session, client) -> DetectionResult:
        if not session or not session.session_id:
            return DetectionResult(detected=False)

        try:
            elapsed = time.time() - session.last_activity_time
            if elapsed < self._grace_period:
                return DetectionResult(detected=False)

            all_status = client.get_session_status()

            if session.session_id in all_status:
                return DetectionResult(detected=False)

            todos = client.get_todo(session.session_id)
            if not todos:
                return DetectionResult(detected=False)

            incomplete = [t for t in todos if t.get("status") != "completed"]
            if incomplete:
                return DetectionResult(
                    detected=True,
                    reason=f"Session disappeared from status with {len(incomplete)} incomplete todos (elapsed: {elapsed:.0f}s)",
                    severity="critical",
                    details={
                        "incomplete_count": len(incomplete),
                        "total_count": len(todos),
                        "elapsed": elapsed,
                    },
                )

            completed = [t for t in todos if t.get("status") == "completed"]
            if completed:
                messages = client.get_messages(session.session_id, limit=100)
                if not self._verify_tasks_executed(completed, messages):
                    return DetectionResult(
                        detected=True,
                        reason=f"Session completed but tasks may have been skipped - incomplete execution detected",
                        severity="high",
                        details={
                            "completed_count": len(completed),
                            "verification": "task_execution_mismatch",
                        },
                    )

            return DetectionResult(detected=False)

        except Exception as e:
            logger.error(f"PrematureEndDetector error: {e}")
            return DetectionResult(detected=False)

    def _verify_tasks_executed(
        self, completed_todos: List[Dict], messages: List[Dict]
    ) -> bool:
        """验证任务是否真正被执行

        检查每个已完成的任务是否在消息历史中有对应的执行步骤
        """
        if not completed_todos or not messages:
            return True

        tool_calls = []
        for m in messages:
            parts = m.get("parts", [])
            for p in parts:
                if p.get("type") == "tool":
                    tool_calls.append(p)

        if len(tool_calls) < len(completed_todos) * self._min_steps_per_task:
            logger.warning(
                f"PrematureEndDetector: too few tool calls ({len(tool_calls)}) "
                f"for {len(completed_todos)} completed tasks"
            )
            return False

        return True


class SessionInvalidDetector(DetectionPlugin):
    """会话失效检测插件

    检测场景：
    - session 不存在（被删除或关闭）
    - 连接断开无法获取状态

    使用 get_session() 直接检查 session 是否存在，
    与 PrematureEndDetector（检查 status 列表）互补。
    """

    def __init__(self, grace_period: int = 10):
        self._grace_period = grace_period

    @property
    def name(self) -> str:
        return "session_invalid_detector"

    def detect(self, session, client) -> DetectionResult:
        if not session or not session.session_id:
            return DetectionResult(detected=False)

        try:
            # 直接获取 session 详情，而不是依赖 status 列表
            session_info = client.get_session(session.session_id)

            # 如果 get_session 成功返回数据，说明 session 存在
            if session_info and isinstance(session_info, dict):
                return DetectionResult(detected=False)

            # 宽限期：刚创建的 session 可能需要时间初始化
            elapsed = time.time() - session.last_activity_time
            if elapsed < self._grace_period:
                return DetectionResult(
                    detected=False,
                    details={"grace_period": True, "elapsed": elapsed},
                )

            return DetectionResult(
                detected=True,
                reason=f"Session {session.session_id} returned empty response (elapsed: {elapsed:.1f}s)",
                severity="critical",
                details={"session_id": session.session_id, "elapsed": elapsed},
            )

        except Exception as e:
            error_str = str(e).lower()
            if "not found" in error_str or "404" in error_str:
                return DetectionResult(
                    detected=True,
                    reason=f"Session not found: {e}",
                    severity="critical",
                    details={"error": str(e)},
                )
            if "connection" in error_str or "refused" in error_str:
                return DetectionResult(
                    detected=True,
                    reason=f"Connection error: {e}",
                    severity="high",
                    details={"error": str(e)},
                )

            # 其他错误，给予宽限期
            elapsed = time.time() - session.last_activity_time
            if elapsed < self._grace_period:
                return DetectionResult(
                    detected=False,
                    details={"grace_period": True, "elapsed": elapsed, "error": str(e)},
                )

            logger.error(f"SessionInvalidDetector error: {e}")
            return DetectionResult(
                detected=True,
                reason=f"Session check error: {e}",
                severity="high",
                details={"error": str(e)},
            )
