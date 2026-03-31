"""
Universal Skill Runner - 通用 skill 执行器

架构原则：编排器管节奏（PACE），agent 管内容（CONTENT）

编排器职责（零业务逻辑）：
1. 启动 opencode serve
2. 创建 session，发送 skill 完整内容 + 用户请求
3. 轮询监控：
   - session 还活着吗？
   - todos 完成了吗？
   - 卡死了？→ 重启
   - 偷懒了（idle 但 todos 未完成）？→ 重启
4. 验证所有 todos 完成
5. 返回结果

agent 职责：
1. 理解 skill 文件内容
2. 按 skill 定义的流程执行
3. 使用 todowrite 管理任务
4. 汇报结果

这样任何 skill 文件都能跑，编排器不需要知道 skill 具体做什么。
"""

import logging
import time
from typing import Optional, Dict, Any, List

from .opencode_client import OpenCodeClient
from .agent_session import AgentSession, SessionError, MaxRetriesExceeded

logger = logging.getLogger(__name__)


class SkillRunnerError(Exception):
    """Skill 执行异常"""

    pass


class SkillRunner:
    """通用 skill 执行器

    不解析 skill 内容，不硬编码任何业务逻辑。
    纯粹的看门狗：发 prompt → 监控 → 验证 → 必要时重启。
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
            # 1. 构建 prompt（skill 完整内容 + 用户请求）
            prompt = self._build_prompt(skill_content, user_request)

            # 2. 创建 session 并发送 prompt
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

            # 3. 主循环：监控 → 验证 → 必要时重启
            start_time = time.time()

            while time.time() - start_time < self.max_execution_time:
                elapsed = time.time() - start_time

                # 3a. 检查是否全部完成
                if self._all_todos_completed():
                    logger.info("All todos completed! Verifying stability...")
                    if self._verify_stable_completion():
                        result["status"] = "success"
                        result["progress"] = self._get_progress()
                        result["todos"] = self._get_todos()
                        logger.info(f"Skill '{skill_name}' completed successfully")
                        return result
                    else:
                        logger.warning(
                            "Completion not stable, continuing to monitor..."
                        )

                # 3b. 检查是否卡死
                if self.session.is_stuck(timeout=self.stuck_threshold):
                    logger.warning(
                        f"Session stuck (elapsed: {elapsed:.0f}s). "
                        f"Restart {restart_count + 1}/{self.max_restarts}"
                    )
                    self.execution_log.append(
                        {
                            "event": "stuck_detected",
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
                            f"Skill execution failed."
                        )
                        result["progress"] = self._get_progress()
                        result["todos"] = self._get_todos()
                        return result

                    self.session = self._restart_session(skill_name, restart_count)
                    continue

                # 3c. 检查是否 idle 但有未完成 todos（模型偷懒）
                if self._is_idle_but_incomplete():
                    logger.warning(
                        f"Session idle but todos incomplete (elapsed: {elapsed:.0f}s). "
                        f"Restart {restart_count + 1}/{self.max_restarts}"
                    )
                    self.execution_log.append(
                        {
                            "event": "idle_incomplete",
                            "elapsed": elapsed,
                            "progress": self._get_progress(),
                            "restart": restart_count + 1,
                            "timestamp": time.time(),
                        }
                    )

                    restart_count += 1
                    result["restart_count"] = restart_count
                    if restart_count >= self.max_restarts:
                        result["error"] = (
                            f"Max restarts ({self.max_restarts}) exceeded. "
                            f"Agent unable to complete todos."
                        )
                        result["progress"] = self._get_progress()
                        result["todos"] = self._get_todos()
                        return result

                    self.session = self._restart_session(skill_name, restart_count)
                    continue

                # 3d. 等待后继续轮询
                time.sleep(self.poll_interval)

                # 打印进度
                progress = self._get_progress()
                if progress["total"] > 0:
                    logger.info(
                        f"Progress: {progress['completed']}/{progress['total']} "
                        f"({progress['percentage']}%) | "
                        f"Restarts: {restart_count} | "
                        f"Elapsed: {elapsed:.0f}s"
                    )

            # 超时
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
        """构建执行 prompt

        将 skill 完整内容 + 用户请求组合成一个 prompt。
        agent 负责理解并执行。
        """
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

    def _restart_session(self, skill_name: str, restart_count: int) -> AgentSession:
        """重启 session 并继续执行

        构建继续执行的消息，包含剩余任务信息。
        """
        # 获取剩余任务
        remaining_todos = []
        completed_todos = []
        try:
            todos = self.client.get_todo(self.session.session_id)
            remaining_todos = [t for t in todos if t.get("status") != "completed"]
            completed_todos = [t for t in todos if t.get("status") == "completed"]
        except Exception as e:
            logger.warning(f"Failed to get todos: {e}")

        # 构建继续消息
        remaining_list = "\n".join(
            [f"- {t.get('content', 'unknown')}" for t in remaining_todos]
        )
        completed_list = "\n".join(
            [f"- {t.get('content', 'unknown')}" for t in completed_todos]
        )

        continue_msg = (
            f"之前的执行被中断（第 {restart_count} 次重启），请继续完成剩余任务。\n\n"
            f"已完成的任务:\n{completed_list}\n\n"
            f"剩余必须完成的任务:\n{remaining_list}\n\n"
            f"重要要求：\n"
            f"1. 你必须继续完成以上所有剩余任务\n"
            f"2. 每完成一个任务就立即用 todowrite 标记为 completed\n"
            f"3. 不要跳过任何任务，不要提前结束\n"
            f"4. 如果遇到困难，尝试多种方法解决\n"
            f"5. 只有当所有任务都真正完成后才能结束\n\n"
            f"现在开始继续执行剩余任务。"
        )

        new_session = self._create_session(f"{skill_name}-restart-{restart_count}")
        new_session.send(continue_msg)

        self.execution_log.append(
            {
                "event": "restarted",
                "restart_count": restart_count,
                "remaining_tasks": len(remaining_todos),
                "completed_tasks": len(completed_todos),
                "new_session_id": new_session.session_id,
                "timestamp": time.time(),
            }
        )

        return new_session

    def _all_todos_completed(self) -> bool:
        """检查是否所有 todos 都完成了"""
        progress = self._get_progress()
        return progress["total"] > 0 and progress["pending"] == 0

    def _is_idle_but_incomplete(self) -> bool:
        """检查 session 是否 idle 但 todos 未完成（模型偷懒）"""
        if not self.session or not self.session.session_id:
            return False

        try:
            all_status = self.client.get_session_status()
            session_status = all_status.get(self.session.session_id, {})
            state = session_status.get("state", "unknown")

            if state != "idle":
                return False

            progress = self._get_progress()
            return progress["total"] > 0 and progress["pending"] > 0

        except Exception as e:
            logger.warning(f"Error checking idle status: {e}")
            return False

    def _verify_stable_completion(self) -> bool:
        """验证完成状态是否稳定

        连续多次检查，确保 todos 确实全部完成且 session 稳定 idle。
        防止 agent 短暂标记完成后又改变。
        """
        for i in range(self.verification_stable_count):
            time.sleep(3)

            # 检查 todos
            progress = self._get_progress()
            if progress["pending"] > 0:
                logger.warning(
                    f"Verification {i + 1}/{self.verification_stable_count}: "
                    f"{progress['pending']} tasks still pending"
                )
                return False

            # 检查 session 状态
            try:
                all_status = self.client.get_session_status()
                session_status = all_status.get(self.session.session_id, {})
                state = session_status.get("state", "unknown")

                if state == "busy":
                    logger.warning(
                        f"Verification {i + 1}/{self.verification_stable_count}: "
                        f"Session still busy"
                    )
                    return False
            except Exception as e:
                logger.warning(f"Error checking status: {e}")

        return True

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
