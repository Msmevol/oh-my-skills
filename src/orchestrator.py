"""
Skill Orchestrator - 通用 skill 执行编排器

架构：
┌─────────────────────────────────────────────────┐
│              Orchestrator (Python)               │
│                                                  │
│  1. 启动 opencode serve                           │
│  2. 读取 skill 文件                               │
│  3. SkillRunner 执行：                            │
│     - 发送 skill 完整内容 + 用户请求              │
│     - 轮询监控进度                                │
│     - 卡死/偷懒 → 重启                            │
│     - 验证全部完成                                │
│  4. 输出结果                                      │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  SkillRunner → AgentSession → opencode   │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
"""

import logging
import subprocess
import time
import os
import sys
from typing import Optional, Dict, Any

from .opencode_client import OpenCodeClient
from .skill_runner import SkillRunner

logger = logging.getLogger(__name__)


class OrchestratorError(Exception):
    pass


class Orchestrator:
    """Skill 执行编排器"""

    def __init__(
        self,
        port: int = 4096,
        host: str = "localhost",
        check_interval: int = 10,
        stuck_threshold: int = 300,
        max_execution_time: int = 3600,
        agent_name: str = "skill-executor",
    ):
        self.port = port
        self.host = host
        self.base_url = f"http://{host}:{port}"
        self.check_interval = check_interval
        self.stuck_threshold = stuck_threshold
        self.max_execution_time = max_execution_time
        self.agent_name = agent_name

        self.client: Optional[OpenCodeClient] = None
        self.server_proc: Optional[subprocess.Popen] = None

    def start_server(self) -> bool:
        """启动 opencode serve"""
        test_client = OpenCodeClient(self.base_url)
        if test_client.health_check():
            logger.info("OpenCode server already running")
            self.client = test_client
            return True

        try:
            self.server_proc = subprocess.Popen(
                [
                    "opencode",
                    "serve",
                    "--port",
                    str(self.port),
                    "--hostname",
                    self.host,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32"
                else 0,
            )
        except FileNotFoundError:
            raise OrchestratorError(
                "opencode not found. Install: npm install -g opencode-ai"
            )

        logger.info("Waiting for opencode server to start...")
        self.client = OpenCodeClient(self.base_url)
        for i in range(30):
            time.sleep(1)
            if self.client.health_check():
                logger.info("OpenCode server started")
                return True

        raise OrchestratorError("Failed to start opencode server within 30 seconds")

    def stop_server(self):
        """停止 opencode serve"""
        if self.server_proc:
            logger.info("Stopping opencode server...")
            try:
                self.server_proc.terminate()
                self.server_proc.wait(timeout=5)
            except Exception:
                self.server_proc.kill()
            self.server_proc = None

    def run(self, skill_path: str) -> Dict[str, Any]:
        """执行 skill 文件

        Args:
            skill_path: skill 文件路径

        Returns:
            {
                "status": "success" | "failed",
                "skill_name": "...",
                "restart_count": int,
                "progress": {...},
                "todos": [...],
                "execution_log": [...],
                "error": "..." (if failed)
            }
        """
        result = {
            "status": "failed",
            "skill_name": None,
            "restart_count": 0,
            "progress": {},
            "todos": [],
            "execution_log": [],
            "error": None,
        }

        try:
            self.start_server()

            if not os.path.exists(skill_path):
                raise OrchestratorError(f"Skill file not found: {skill_path}")

            with open(skill_path, "r", encoding="utf-8") as f:
                skill_content = f.read()

            skill_name = (
                os.path.basename(skill_path).replace(".txt", "").replace(".skill", "")
            )
            for line in skill_content.split("\n")[:5]:
                if line.startswith("name:"):
                    skill_name = line.split(":", 1)[1].strip()
                    break

            result["skill_name"] = skill_name

            runner = SkillRunner(
                client=self.client,
                agent_name=self.agent_name,
                max_restarts=10,
                stuck_threshold=self.stuck_threshold,
                max_execution_time=self.max_execution_time,
                poll_interval=self.check_interval,
                verification_stable_count=3,
            )

            logger.info("=" * 60)
            logger.info(f"EXECUTING SKILL: {skill_name}")
            logger.info("=" * 60)

            execution_result = runner.run(
                skill_content=skill_content,
                user_request=skill_content[:500],
                skill_name=skill_name,
            )

            result.update(execution_result)

            if execution_result.get("status") == "success":
                result["status"] = "success"
                logger.info(f"SKILL COMPLETED: {skill_name}")
            else:
                result["error"] = execution_result.get("error", "Unknown error")
                logger.warning(f"Skill execution failed: {result['error']}")

        except OrchestratorError as e:
            result["error"] = str(e)
            logger.error(result["error"])
        except Exception as e:
            result["error"] = f"Unexpected error: {e}"
            logger.exception(result["error"])
        finally:
            self.stop_server()

        return result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_server()
