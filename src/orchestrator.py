"""
Skill Orchestrator - 通用 skill 执行编排器

支持两种 skill 格式：
1. 标准 opencode skills: .opencode/skills/<name>/SKILL.md
2. 自定义 skill 文件: 任意 .txt/.md 文件

架构：
┌─────────────────────────────────────────────────┐
│              Orchestrator (Python)               │
│                                                  │
│  1. 启动 opencode serve                           │
│  2. 发现/加载 skill:                              │
│     - 如果是目录名 → 从 .opencode/skills/ 加载   │
│     - 如果是文件路径 → 直接读取                   │
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
import glob
from typing import Optional, Dict, Any, List

from .opencode_client import OpenCodeClient
from .skill_runner import SkillRunner

logger = logging.getLogger(__name__)


class OrchestratorError(Exception):
    pass


class SkillDiscovery:
    """Skill 发现器

    从多个位置发现和加载 skills：
    1. 项目本地: .opencode/skills/<name>/SKILL.md
    2. 全局配置: ~/.config/opencode/skills/<name>/SKILL.md
    3. Claude 兼容: .agents/skills/<name>/SKILL.md
    4. 自定义文件路径
    """

    SEARCH_PATHS = [
        ".opencode/skills",
        ".agents/skills",
        ".claude/skills",
    ]

    GLOBAL_PATHS = [
        os.path.expanduser("~/.config/opencode/skills"),
        os.path.expanduser("~/.agents/skills"),
        os.path.expanduser("~/.claude/skills"),
    ]

    @classmethod
    def find_skills(cls, base_dir: str = None) -> List[Dict[str, str]]:
        """发现所有可用的 skills

        Returns:
            [{"name": "xxx", "path": "/path/to/SKILL.md", "description": "..."}]
        """
        skills = []
        search_dirs = list(cls.SEARCH_PATHS)

        if base_dir:
            search_dirs = [os.path.join(base_dir, p) for p in cls.SEARCH_PATHS]

        # 添加全局路径
        search_dirs.extend(cls.GLOBAL_PATHS)

        for skills_dir in search_dirs:
            if not os.path.isdir(skills_dir):
                continue

            for skill_dir in os.listdir(skills_dir):
                skill_md = os.path.join(skills_dir, skill_dir, "SKILL.md")
                if not os.path.isfile(skill_md):
                    continue

                # 解析 frontmatter 获取 name 和 description
                try:
                    with open(skill_md, "r", encoding="utf-8") as f:
                        content = f.read()

                    name = skill_dir
                    description = ""

                    # 解析 YAML frontmatter
                    if content.startswith("---"):
                        end = content.find("---", 3)
                        if end > 0:
                            frontmatter = content[3:end]
                            for line in frontmatter.split("\n"):
                                if line.startswith("name:"):
                                    name = line.split(":", 1)[1].strip()
                                elif line.startswith("description:"):
                                    desc = line.split(":", 1)[1].strip()
                                    description = desc.strip('"').strip("'")

                    skills.append(
                        {
                            "name": name,
                            "path": os.path.abspath(skill_md),
                            "description": description,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to parse skill {skill_md}: {e}")

        return skills

    @classmethod
    def load_skill(
        cls, skill_name_or_path: str, base_dir: str = None
    ) -> Optional[Dict[str, str]]:
        """加载指定的 skill

        Args:
            skill_name_or_path: skill 名称或文件路径
            base_dir: 项目根目录

        Returns:
            {"name": "...", "content": "...", "path": "..."} 或 None
        """
        # 1. 如果是文件路径且存在
        if os.path.isfile(skill_name_or_path):
            try:
                with open(skill_name_or_path, "r", encoding="utf-8") as f:
                    content = f.read()

                name = os.path.basename(os.path.dirname(skill_name_or_path))
                if name == "skills":
                    name = (
                        os.path.basename(skill_name_or_path)
                        .replace(".md", "")
                        .replace(".txt", "")
                    )

                return {
                    "name": name,
                    "content": content,
                    "path": os.path.abspath(skill_name_or_path),
                }
            except Exception as e:
                logger.error(f"Failed to load skill file {skill_name_or_path}: {e}")
                return None

        # 2. 从搜索路径查找
        search_dirs = list(cls.SEARCH_PATHS)
        if base_dir:
            search_dirs = [os.path.join(base_dir, p) for p in cls.SEARCH_PATHS]
        search_dirs.extend(cls.GLOBAL_PATHS)

        for skills_dir in search_dirs:
            # 尝试 SKILL.md 格式
            skill_md = os.path.join(skills_dir, skill_name_or_path, "SKILL.md")
            if os.path.isfile(skill_md):
                try:
                    with open(skill_md, "r", encoding="utf-8") as f:
                        content = f.read()
                    return {
                        "name": skill_name_or_path,
                        "content": content,
                        "path": os.path.abspath(skill_md),
                    }
                except Exception as e:
                    logger.error(f"Failed to load skill {skill_md}: {e}")
                    return None

            # 尝试 .txt 格式（兼容旧格式）
            skill_txt = os.path.join(skills_dir, f"{skill_name_or_path}.txt")
            if os.path.isfile(skill_txt):
                try:
                    with open(skill_txt, "r", encoding="utf-8") as f:
                        content = f.read()
                    return {
                        "name": skill_name_or_path,
                        "content": content,
                        "path": os.path.abspath(skill_txt),
                    }
                except Exception as e:
                    logger.error(f"Failed to load skill {skill_txt}: {e}")
                    return None

        return None

    @classmethod
    def list_available(cls, base_dir: str = None) -> List[str]:
        """列出所有可用的 skill 名称"""
        skills = cls.find_skills(base_dir)
        return [s["name"] for s in skills]


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
        base_dir: str = None,
    ):
        self.port = port
        self.host = host
        self.base_url = f"http://{host}:{port}"
        self.check_interval = check_interval
        self.stuck_threshold = stuck_threshold
        self.max_execution_time = max_execution_time
        self.agent_name = agent_name
        self.base_dir = base_dir or os.getcwd()

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

    def list_skills(self) -> List[Dict[str, str]]:
        """列出所有可用的 skills"""
        return SkillDiscovery.find_skills(self.base_dir)

    def run(self, skill_name_or_path: str, user_request: str = None) -> Dict[str, Any]:
        """执行 skill

        Args:
            skill_name_or_path: skill 名称或文件路径
            user_request: 用户请求（可选，默认使用 skill 描述）

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

            # 加载 skill
            skill_info = SkillDiscovery.load_skill(skill_name_or_path, self.base_dir)
            if not skill_info:
                # 尝试列出可用的 skills
                available = SkillDiscovery.list_available(self.base_dir)
                if available:
                    raise OrchestratorError(
                        f"Skill not found: {skill_name_or_path}\n"
                        f"Available skills: {', '.join(available)}"
                    )
                else:
                    raise OrchestratorError(
                        f"Skill not found: {skill_name_or_path}\n"
                        f"No skills available. Place SKILL.md files in:\n"
                        f"  - .opencode/skills/<name>/SKILL.md\n"
                        f"  - .agents/skills/<name>/SKILL.md"
                    )

            skill_name = skill_info["name"]
            skill_content = skill_info["content"]
            result["skill_name"] = skill_name

            # 如果没有提供用户请求，使用 skill 内容的前 500 字符
            if not user_request:
                user_request = skill_content[:500]

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
            logger.info(f"Source: {skill_info['path']}")
            logger.info("=" * 60)

            execution_result = runner.run(
                skill_content=skill_content,
                user_request=user_request,
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
