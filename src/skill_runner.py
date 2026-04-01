"""
Universal Skill Runner - 通用 skill 执行器（插件化架构）

架构原则：编排器管节奏（PACE），agent 管内容（CONTENT）

编排器职责（零业务逻辑）：
1. 启动 opencode serve
2. 创建 session，发送 skill 完整内容 + 用户请求
3. 轮询监控（通过检测插件）：
   - session 还活着吗？
   - todos 完成了吗？
   - 卡死了？→ 重启
   - 偷懒了（idle 但 todos 未完成）？→ 重启
   - 提前结束了（done 但 todos 未完成）？→ 重启
4. 验证所有 todos 完成（通过验证插件）
5. 返回结果

agent 职责：
1. 理解 skill 文件内容
2. 按 skill 定义的流程执行
3. 使用 todowrite 管理任务
4. 汇报结果

这样任何 skill 文件都能跑，编排器不需要知道 skill 具体做什么。

插件架构：
- 检测插件：StuckDetector, IdleIncompleteDetector, PrematureEndDetector, SessionInvalidDetector
- 恢复插件：RestartRecovery
- 验证插件：StableCompletionVerifier, TodoExecutionVerifier
"""

import logging
import time
from typing import Optional, Dict, Any, List

from .opencode_client import OpenCodeClient
from .agent_session import AgentSession, SessionError, MaxRetriesExceeded
from .plugins import PluginRegistry, DetectionResult
from .plugins.builtin_detectors import (
    StuckDetector,
    IdleIncompleteDetector,
    PrematureEndDetector,
    SessionInvalidDetector,
)
from .plugins.builtin_recovery import RestartRecovery
from .plugins.builtin_verification import (
    StableCompletionVerifier,
    TodoExecutionVerifier,
)

logger = logging.getLogger(__name__)


class SkillRunnerError(Exception):
    """Skill 执行异常"""

    pass


class SkillRunner:
    """通用 skill 执行器（插件化）

    不解析 skill 内容，不硬编码任何业务逻辑。
    纯粹的看门狗：发 prompt → 监控（插件） → 验证（插件） → 必要时重启（插件）。
    """

    def __init__(
        self,
        client: OpenCodeClient,
        agent_name: str = "skill-executor",
        max_restarts: int = 10,
        stuck_threshold: int = 300,
        max_execution_time: int = 3600,
        poll_interval: int = 10,
        verification_stable_count: int = 3,
        plugin_registry: Optional[PluginRegistry] = None,
    ):
        self.client = client
        self.agent_name = agent_name
        self.max_restarts = max_restarts
        self.stuck_threshold = stuck_threshold
        self.max_execution_time = max_execution_time
        self.poll_interval = poll_interval
        self.verification_stable_count = verification_stable_count

        self.session: Optional[AgentSession] = None
        self.execution_log: List[Dict[str, Any]] = []

        self.registry = plugin_registry or PluginRegistry()
        if not plugin_registry:
            self._register_builtin_plugins()

    def _register_builtin_plugins(self):
        """注册内置插件"""
        self.registry.register_detection(PrematureEndDetector(), priority=100)
        self.registry.register_detection(SessionInvalidDetector(), priority=90)
        self.registry.register_detection(
            StuckDetector(timeout=self.stuck_threshold), priority=80
        )
        self.registry.register_detection(IdleIncompleteDetector(), priority=70)

        self.registry.register_recovery(
            RestartRecovery(agent_name=self.agent_name), priority=100
        )

        self.registry.register_verification(
            StableCompletionVerifier(stable_count=self.verification_stable_count),
            priority=100,
        )
        self.registry.register_verification(TodoExecutionVerifier(), priority=50)

        logger.info(f"Built-in plugins registered: {self.registry.list_plugins()}")

    def run(
        self,
        skill_content: str,
        user_request: str,
        skill_name: str = "skill",
    ) -> Dict[str, Any]:
        """执行 skill

        Args:
            skill_content: skill 文件完整内容
            user_request: 用户请求（如"创建MR"）
            skill_name: skill 名称（用于日志）

        Returns:
            {
                "status": "success" | "failed" | "waiting_for_user",
                "skill_name": "...",
                "restart_count": int,
                "progress": {"total": N, "completed": N, "percentage": N},
                "todos": [...],
                "execution_log": [...],
                "error": "..." (if failed)
            }
        """
        result = {
            "status": "failed",
            "skill_name": skill_name,
            "restart_count": 0,
            "progress": {},
            "todos": [],
            "execution_log": self.execution_log,
            "error": None,
        }

        restart_count = 0

        try:
            prompt = self._build_prompt(skill_content, user_request)

            self.session = self._create_session(skill_name)
            self.session.send(prompt)

            logger.info(
                f"Skill '{skill_name}' execution started. "
                f"Session: {self.session.session_id}"
            )
            self.execution_log.append(
                {
                    "event": "started",
                    "session_id": self.session.session_id,
                    "timestamp": time.time(),
                }
            )

            start_time = time.time()

            while time.time() - start_time < self.max_execution_time:
                elapsed = time.time() - start_time

                if self._all_todos_completed():
                    logger.info("All todos completed! Verifying stability...")
                    if self._verify_completion():
                        result["status"] = "success"
                        result["progress"] = self._get_progress()
                        result["todos"] = self._get_todos()
                        logger.info(f"Skill '{skill_name}' completed successfully")
                        return result
                    else:
                        logger.warning(
                            "Completion verification failed, continuing to monitor..."
                        )

                detection_result = self.registry.run_all_detections(
                    self.session, self.client
                )
                if detection_result and detection_result.detected:
                    logger.warning(
                        f"Detection: {detection_result.reason} "
                        f"(severity: {detection_result.severity}, "
                        f"elapsed: {elapsed:.0f}s). "
                        f"Restart {restart_count + 1}/{self.max_restarts}"
                    )
                    self.execution_log.append(
                        {
                            "event": "detection_triggered",
                            "detector": detection_result.reason,
                            "severity": detection_result.severity,
                            "elapsed": elapsed,
                            "restart": restart_count + 1,
                            "timestamp": time.time(),
                        }
                    )

                    restart_count += 1
                    result["restart_count"] = restart_count
                    if restart_count >= self.max_restarts:
                        result["error"] = (
                            f"Max restarts ({self.max_restarts}) exceeded. "
                            f"Last issue: {detection_result.reason}"
                        )
                        result["progress"] = self._get_progress()
                        result["todos"] = self._get_todos()
                        return result

                    new_session = self.registry.run_recovery(
                        self.session,
                        self.client,
                        context={
                            "restart_count": restart_count,
                            "detection_result": detection_result,
                            "skill_name": skill_name,
                        },
                    )

                    if new_session is None:
                        result["error"] = (
                            f"Recovery failed after detecting: {detection_result.reason}"
                        )
                        result["progress"] = self._get_progress()
                        result["todos"] = self._get_todos()
                        return result

                    self.session = new_session

                    self.execution_log.append(
                        {
                            "event": "recovered",
                            "restart_count": restart_count,
                            "new_session_id": self.session.session_id,
                            "timestamp": time.time(),
                        }
                    )
                    continue

                time.sleep(self.poll_interval)

                progress = self._get_progress()
                if progress["total"] > 0:
                    logger.info(
                        f"Progress: {progress['completed']}/{progress['total']} "
                        f"({progress['percentage']}%) | "
                        f"Restarts: {restart_count} | "
                        f"Elapsed: {elapsed:.0f}s"
                    )

            result["error"] = (
                f"Execution timed out after {self.max_execution_time}s. "
                f"Completed {self._get_progress()['completed']}/"
                f"{self._get_progress()['total']} todos."
            )
            result["progress"] = self._get_progress()
            result["todos"] = self._get_todos()
            return result

        except MaxRetriesExceeded as e:
            result["error"] = str(e)
            result["progress"] = self._get_progress()
            result["todos"] = self._get_todos()
            return result
        except Exception as e:
            result["error"] = f"Unexpected error: {e}"
            logger.exception(result["error"])
            result["progress"] = self._get_progress()
            result["todos"] = self._get_todos()
            return result

    def _build_prompt(self, skill_content: str, user_request: str) -> str:
        """构建执行 prompt"""
        return (
            f"你是一个专业的 skill 执行 agent。请严格按照以下 skill 定义执行任务。\n\n"
            f"## Skill 定义\n\n"
            f"```\n{skill_content}\n```\n\n"
            f"## 用户请求\n\n"
            f"{user_request}\n\n"
            f"## 执行要求\n\n"
            f"1. 仔细阅读 skill 定义，理解完整流程\n"
            f"2. 使用 todowrite 工具创建任务列表\n"
            f"3. 严格按照 skill 定义的步骤逐条执行，不要跳步\n"
            f"4. 每完成一个步骤就更新 todowrite 标记为 completed\n"
            f"5. 不要提前结束，确保所有任务都完成\n"
            f"6. 如果 skill 需要用户确认，使用 question 工具\n"
            f"7. 如果遇到困难，尝试多种方法解决，不要跳过\n"
            f"8. 所有任务完成后，汇报最终结果"
        )

    def _create_session(self, skill_name: str) -> AgentSession:
        """创建新的 agent session"""
        session = AgentSession(
            self.client,
            agent_name=self.agent_name,
            stuck_threshold=self.stuck_threshold,
            max_retries=3,
        )
        session_id = session.create_session(
            title=f"skill-{skill_name}-{int(time.time())}"
        )
        logger.info(f"Created session: {session_id}")
        return session

    def _all_todos_completed(self) -> bool:
        """检查是否所有 todos 都完成了"""
        progress = self._get_progress()
        return progress["total"] > 0 and progress["pending"] == 0

    def _verify_completion(self) -> bool:
        """通过验证插件验证完成状态"""
        return self.registry.run_all_verifications(self.session, self.client)

    def _get_progress(self) -> Dict[str, Any]:
        """获取当前进度"""
        if not self.session or not self.session.session_id:
            return {"total": 0, "completed": 0, "pending": 0, "percentage": 0}
        return self.session.get_progress()

    def _get_todos(self) -> List[Dict[str, Any]]:
        """获取当前 todos"""
        if not self.session or not self.session.session_id:
            return []
        try:
            return self.client.get_todo(self.session.session_id)
        except Exception:
            return []
