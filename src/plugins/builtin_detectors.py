"""
Built-in Detection Plugins - 内置检测插件

提供四种核心检测能力：
1. StuckDetector - 卡死检测（busy 超时、error 状态）
2. IdleIncompleteDetector - 偷懒检测（idle 但 todos 未完成）
3. PrematureEndDetector - 提前结束检测（done/completed 但 todos 未完成）
4. SessionInvalidDetector - 会话失效检测（session 不存在、连接断开）
"""

import logging
import time
from typing import Optional

from . import DetectionPlugin, DetectionResult

logger = logging.getLogger(__name__)


class StuckDetector(DetectionPlugin):
    """卡死检测插件

    检测场景：
    1. session 状态为 error
    2. session 状态为 busy 且超过阈值时间
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
            current_state = session_status.get("state", "unknown")

            if current_state == "error":
                return DetectionResult(
                    detected=True,
                    reason=f"Session in error state",
                    severity="high",
                    details={"state": current_state},
                )

            if current_state == "busy":
                elapsed = time.time() - session.last_activity_time
                if elapsed > self._timeout:
                    return DetectionResult(
                        detected=True,
                        reason=f"Session busy for {elapsed:.0f}s (threshold: {self._timeout}s)",
                        severity="medium",
                        details={
                            "state": current_state,
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
            current_state = session_status.get("state", "unknown")

            if current_state != "idle":
                return DetectionResult(detected=False)

            todos = client.get_todo(session.session_id)
            if not todos:
                return DetectionResult(detected=False)

            incomplete = [t for t in todos if t.get("status") != "completed"]
            if incomplete:
                return DetectionResult(
                    detected=True,
                    reason=f"Session idle with {len(incomplete)} incomplete todos",
                    severity="high",
                    details={
                        "state": current_state,
                        "incomplete_count": len(incomplete),
                        "total_count": len(todos),
                    },
                )

            return DetectionResult(detected=False)

        except Exception as e:
            logger.error(f"IdleIncompleteDetector error: {e}")
            return DetectionResult(detected=False)


class PrematureEndDetector(DetectionPlugin):
    """提前结束检测插件

    检测场景：
    - session 状态为 done/completed
    - 但还有未完成的 todos
    - 这是小模型最常见的问题：自作主张提前结束
    """

    @property
    def name(self) -> str:
        return "premature_end_detector"

    def detect(self, session, client) -> DetectionResult:
        if not session or not session.session_id:
            return DetectionResult(detected=False)

        try:
            all_status = client.get_session_status()
            session_status = all_status.get(session.session_id, {})
            current_state = session_status.get("state", "unknown")

            end_states = {"done", "completed", "finished"}
            if current_state.lower() not in end_states:
                return DetectionResult(detected=False)

            todos = client.get_todo(session.session_id)
            if not todos:
                return DetectionResult(detected=False)

            incomplete = [t for t in todos if t.get("status") != "completed"]
            if incomplete:
                return DetectionResult(
                    detected=True,
                    reason=f"Session ended ({current_state}) with {len(incomplete)} incomplete todos",
                    severity="critical",
                    details={
                        "state": current_state,
                        "incomplete_count": len(incomplete),
                        "total_count": len(todos),
                    },
                )

            return DetectionResult(detected=False)

        except Exception as e:
            logger.error(f"PrematureEndDetector error: {e}")
            return DetectionResult(detected=False)


class SessionInvalidDetector(DetectionPlugin):
    """会话失效检测插件

    检测场景：
    - session 不存在（被删除或关闭）
    - 连接断开无法获取状态
    - session 被 abort
    """

    @property
    def name(self) -> str:
        return "session_invalid_detector"

    def detect(self, session, client) -> DetectionResult:
        if not session or not session.session_id:
            return DetectionResult(detected=False)

        try:
            all_status = client.get_session_status()
            session_status = all_status.get(session.session_id)

            if session_status is None:
                return DetectionResult(
                    detected=True,
                    reason=f"Session {session.session_id} not found in status list",
                    severity="critical",
                    details={"session_id": session.session_id},
                )

            current_state = session_status.get("state", "unknown")
            if current_state == "aborted":
                return DetectionResult(
                    detected=True,
                    reason="Session was aborted",
                    severity="high",
                    details={"state": current_state},
                )

            return DetectionResult(detected=False)

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

            logger.error(f"SessionInvalidDetector error: {e}")
            return DetectionResult(detected=False)
