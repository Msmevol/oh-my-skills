"""
E2E tests for the Universal Skill Runner

需要真实 opencode serve + 小模型运行。
测试真实 skill 文件的完整执行流程。
"""

import pytest
import time
import os
import subprocess
import sys
import tempfile
import shutil
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.opencode_client import OpenCodeClient
from src.skill_runner import SkillRunner

BASE_URL = "http://localhost:4096"
SERVER_PORT = 4096
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# 日志配置
TEST_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_logs"
)
os.makedirs(TEST_LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(
    TEST_LOG_DIR, f"e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)

# 配置日志：同时输出到文件和控制台
logger = logging.getLogger("e2e_test")
logger.setLevel(logging.DEBUG)

# 文件 handler
fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.addHandler(fh)

# 控制台 handler
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(ch)

logger.info("=" * 80)
logger.info(f"E2E 测试日志文件: {LOG_FILE}")
logger.info("=" * 80)


@pytest.fixture(scope="module")
def opencode_server():
    """启动 opencode serve 用于测试"""
    logger.info("=" * 80)
    logger.info("开始初始化 opencode server fixture")
    logger.info(f"目标地址: {BASE_URL}")

    client = OpenCodeClient(BASE_URL)
    if client.health_check():
        logger.info("检测到已有 opencode server 运行，复用现有连接")
        yield client
        logger.info("opencode server fixture 清理完成（复用模式）")
        return

    logger.info("未检测到运行中的 server，尝试启动 opencode serve 进程...")

    # Windows 下 opencode 是 .cmd 文件，需要 shell=True
    if sys.platform == "win32":
        cmd = f"opencode serve --port {SERVER_PORT} --hostname localhost"
        shell = True
    else:
        cmd = [
            "opencode",
            "serve",
            "--port",
            str(SERVER_PORT),
            "--hostname",
            "localhost",
        ]
        shell = False

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=shell,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except FileNotFoundError:
        logger.warning("opencode 命令未找到，跳过 E2E 测试")
        pytest.skip("opencode not found in PATH")
    logger.info(f"opencode serve 进程已启动 (PID: {proc.pid})")

    for i in range(30):
        time.sleep(1)
        if client.health_check():
            logger.info(f"server 启动成功！等待时间: {i + 1}秒")
            break
    else:
        logger.error("server 启动失败，终止进程")
        proc.terminate()
        pytest.skip("Failed to start opencode server")

    yield client

    logger.info("开始清理 opencode server fixture...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
        logger.info("server 进程已正常终止")
    except Exception:
        logger.warning("server 进程未正常终止，强制杀死")
        proc.kill()
    logger.info("opencode server fixture 清理完成")


@pytest.fixture
def skill_runner(opencode_server):
    """创建 SkillRunner 实例"""
    logger.info(
        "创建 SkillRunner 实例 (max_restarts=3, max_execution_time=600s, stuck_threshold=120s)"
    )
    return SkillRunner(
        client=opencode_server,
        agent_name="skill-executor",
        max_restarts=3,
        stuck_threshold=120,
        max_execution_time=600,
        poll_interval=5,
        verification_stable_count=2,
    )


@pytest.fixture
def temp_workspace():
    """创建临时工作目录，避免文件污染"""
    temp_dir = tempfile.mkdtemp(prefix="test_e2e_")
    logger.info(f"创建临时工作目录: {temp_dir}")
    original_cwd = os.getcwd()
    os.chdir(temp_dir)

    yield temp_dir

    os.chdir(original_cwd)
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info(f"清理临时工作目录: {temp_dir}")
    except Exception as e:
        logger.warning(f"清理临时目录失败: {e}")


@pytest.mark.e2e
class TestSkillE2E:
    """E2E 测试 - 真实 skill 文件 + 小模型"""

    def test_custom_simple_skill(self, skill_runner, temp_workspace):
        """测试自定义简单 skill"""
        logger.info("=" * 60)
        logger.info("[TEST START] test_custom_simple_skill")
        logger.info(f"工作目录: {temp_workspace}")

        skill_path = os.path.join(FIXTURES_DIR, "skill_simple.txt")
        if not os.path.exists(skill_path):
            logger.warning(f"Fixture 不存在: {skill_path}")
            pytest.skip(f"Fixture not found: {skill_path}")

        with open(skill_path, "r", encoding="utf-8") as f:
            skill_content = f.read()
        logger.info(f"加载 skill 文件: {skill_path}")

        logger.info("发送 skill 内容到 SkillRunner...")
        start_time = time.time()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行简单测试 skill，创建 hello.py",
            skill_name="test-simple-skill",
        )

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"[TEST RESULT] test_custom_simple_skill")
        logger.info(f"耗时: {elapsed:.1f}秒")
        logger.info(f"状态: {result['status']}")
        logger.info(f"重启次数: {result.get('restart_count', 0)}")
        logger.info(f"进度: {result.get('progress', {})}")
        logger.info(f"Todos 数量: {len(result.get('todos', []))}")
        logger.info(f"执行日志事件数: {len(result.get('execution_log', []))}")
        if result.get("error"):
            logger.warning(f"错误: {result['error']}")
        for i, todo in enumerate(result.get("todos", [])):
            logger.info(
                f"  Todo {i + 1}: [{todo.get('status', '?')}] {todo.get('content', '?')}"
            )
        for evt in result.get("execution_log", []):
            logger.info(f"  事件: {evt.get('event')} @ {evt.get('elapsed', 0):.1f}s")

        # 验证文件创建
        hello_path = os.path.join(temp_workspace, "hello.py")
        if os.path.exists(hello_path):
            logger.info(f"文件已创建: hello.py")
            with open(hello_path, "r") as f:
                content = f.read()
                logger.info(f"  内容: {content.strip()}")
            assert "Hello World" in content
        else:
            logger.warning(f"文件未创建: hello.py")

        assert result["status"] == "success", f"Failed: {result.get('error')}"
        assert result["progress"]["total"] >= 3
        assert result["progress"]["completed"] == result["progress"]["total"]
        logger.info("[TEST PASS] test_custom_simple_skill")

    @pytest.mark.skip(reason="需要 git/MCP 环境，暂时跳过")
    def test_code_mr_ci_loop_basic(self, skill_runner, temp_workspace):
        """测试 code-mr-ci-loop skill 基本流程"""
        logger.info("=" * 60)
        logger.info("[TEST START] test_code_mr_ci_loop_basic")
        logger.info(f"工作目录: {temp_workspace}")

        skill_path = "D:/ai_demo/skill.txt"
        if not os.path.exists(skill_path):
            logger.warning(f"Skill 文件不存在: {skill_path}")
            pytest.skip(f"Skill file not found: {skill_path}")

        with open(skill_path, "r", encoding="utf-8") as f:
            skill_content = f.read()
        logger.info(f"加载 skill 文件: {skill_path}")

        logger.info("发送 skill 内容到 SkillRunner...")
        start_time = time.time()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="查看流水线状态",
            skill_name="code-mr-ci-loop",
        )

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"[TEST RESULT] test_code_mr_ci_loop_basic")
        logger.info(f"耗时: {elapsed:.1f}秒")
        logger.info(f"状态: {result['status']}")
        logger.info(f"重启次数: {result.get('restart_count', 0)}")
        logger.info(f"进度: {result.get('progress', {})}")
        logger.info(f"Todos 数量: {len(result.get('todos', []))}")
        if result.get("error"):
            logger.warning(f"错误: {result['error']}")

        assert result["skill_name"] == "code-mr-ci-loop"
        assert result["progress"]["total"] > 0 or result["error"] is not None
        logger.info("[TEST PASS] test_code_mr_ci_loop_basic")

    def test_skill_restart_and_continue(self, skill_runner, temp_workspace):
        """测试重启后继续执行"""
        logger.info("=" * 60)
        logger.info("[TEST START] test_skill_restart_and_continue")
        logger.info(f"工作目录: {temp_workspace}")

        skill_content = """---
name: test-restart
description: "测试重启继续"
---

# 重启继续测试

## 步骤
1. 使用 todowrite 创建 4 个任务：
   - 创建 restart_test_1.txt，写入 "Task 1"
   - 创建 restart_test_2.txt，写入 "Task 2"
   - 创建 restart_test_3.txt，写入 "Task 3"
   - 创建 restart_test_4.txt，写入 "Task 4"

2. 逐个执行

3. 验证所有文件存在

4. 标记所有任务为 completed

5. 汇报结果
"""
        logger.info("发送 skill 内容到 SkillRunner...")
        start_time = time.time()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行重启继续测试",
            skill_name="test-restart",
        )

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"[TEST RESULT] test_skill_restart_and_continue")
        logger.info(f"耗时: {elapsed:.1f}秒")
        logger.info(f"状态: {result['status']}")
        logger.info(f"重启次数: {result.get('restart_count', 0)}")
        logger.info(f"进度: {result.get('progress', {})}")
        logger.info(f"Todos 数量: {len(result.get('todos', []))}")
        logger.info(f"执行日志事件数: {len(result.get('execution_log', []))}")
        if result.get("error"):
            logger.warning(f"错误: {result['error']}")
        for i, todo in enumerate(result.get("todos", [])):
            logger.info(
                f"  Todo {i + 1}: [{todo.get('status', '?')}] {todo.get('content', '?')}"
            )
        for evt in result.get("execution_log", []):
            logger.info(f"  事件: {evt.get('event')} @ {evt.get('elapsed', 0):.1f}s")

        # 验证文件创建
        for i in range(1, 5):
            fpath = os.path.join(temp_workspace, f"restart_test_{i}.txt")
            if os.path.exists(fpath):
                logger.info(f"文件已创建: restart_test_{i}.txt")
                with open(fpath, "r") as f:
                    logger.info(f"  内容: {f.read().strip()}")
            else:
                logger.warning(f"文件未创建: restart_test_{i}.txt")

        assert result["status"] == "success" or result["restart_count"] > 0
        assert result["progress"]["total"] >= 4
        logger.info("[TEST PASS] test_skill_restart_and_continue")


@pytest.mark.e2e
def test_skill_runner_with_real_opencode():
    """冒烟测试：验证 opencode 连接正常"""
    logger.info("=" * 60)
    logger.info("[TEST START] test_skill_runner_with_real_opencode")

    client = OpenCodeClient(BASE_URL)
    if not client.health_check():
        logger.warning("opencode server 未运行")
        pytest.skip("opencode server not running")

    agents = client.list_agents()
    agent_names = [a.get("name", "") for a in agents]
    logger.info(f"可用 agents: {agent_names}")

    assert "skill-executor" in agent_names, (
        f"skill-executor agent not found. Available: {agent_names}"
    )
    logger.info("[TEST PASS] test_skill_runner_with_real_opencode")
