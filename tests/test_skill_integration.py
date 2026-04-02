"""
Integration tests for the Universal Skill Runner

需要真实 opencode serve 运行。
测试 SkillRunner 与 opencode 的实际交互。
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

# 日志配置
TEST_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_logs"
)
os.makedirs(TEST_LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(
    TEST_LOG_DIR, f"integration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)

# 配置日志：同时输出到文件和控制台
logger = logging.getLogger("integration_test")
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
logger.info(f"集成测试日志文件: {LOG_FILE}")
logger.info("=" * 80)


@pytest.fixture(scope="module")
def opencode_server():
    """启动 opencode serve 用于测试"""
    logger.info("=" * 80)
    logger.info("开始初始化 opencode server fixture")
    logger.info(f"目标地址: {BASE_URL}")

    # 检查是否已有 server 运行
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
        logger.warning("opencode 命令未找到，跳过集成测试")
        pytest.skip("opencode not found in PATH")
    logger.info(f"opencode serve 进程已启动 (PID: {proc.pid})")

    # 等待 server 启动
    logger.info("等待 server 启动...")
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

    # 清理
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
    temp_dir = tempfile.mkdtemp(prefix="test_integration_")
    logger.info(f"创建临时工作目录: {temp_dir}")
    original_cwd = os.getcwd()
    os.chdir(temp_dir)

    yield temp_dir

    # 清理临时目录
    os.chdir(original_cwd)
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.info(f"清理临时工作目录: {temp_dir}")
    except Exception as e:
        logger.warning(f"清理临时目录失败: {e}")


@pytest.mark.integration
class TestSkillRunnerIntegration:
    """集成测试 - 需要真实 opencode"""

    def test_simple_skill_execution(self, skill_runner, temp_workspace):
        """测试简单 skill 完整执行"""
        logger.info("=" * 60)
        logger.info("[TEST START] test_simple_skill_execution")
        logger.info(f"工作目录: {temp_workspace}")

        skill_content = """---
name: test-simple
description: "创建 hello.py 并验证"
---

# 简单测试 Skill

## 步骤
1. 使用 todowrite 创建以下任务：
   - 创建 hello.py 文件，内容为 print("Hello World")
   - 读取 hello.py 验证内容正确
   - 标记所有任务为 completed

2. 逐个执行任务

3. 汇报结果
"""
        logger.info("发送 skill 内容到 SkillRunner...")
        start_time = time.time()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="创建 hello.py 并验证内容",
            skill_name="test-simple",
        )

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"[TEST RESULT] test_simple_skill_execution")
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

        assert result["status"] == "success"
        assert result["progress"]["completed"] == result["progress"]["total"]
        assert result["progress"]["total"] > 0
        logger.info("[TEST PASS] test_simple_skill_execution")

    def test_skill_with_multiple_todos(self, skill_runner, temp_workspace):
        """测试多步骤 skill（5+ todos）"""
        logger.info("=" * 60)
        logger.info("[TEST START] test_skill_with_multiple_todos")
        logger.info(f"工作目录: {temp_workspace}")

        skill_content = """---
name: test-multi-step
description: "多步骤测试"
---

# 多步骤测试 Skill

## 步骤
1. 使用 todowrite 创建以下 5 个任务：
   - 创建 step1.txt，写入 "Step 1 done"
   - 创建 step2.txt，写入 "Step 2 done"
   - 创建 step3.txt，写入 "Step 3 done"
   - 创建 step4.txt，写入 "Step 4 done"
   - 创建 step5.txt，写入 "Step 5 done"

2. 逐个执行每个任务

3. 验证所有文件都创建了

4. 标记所有任务为 completed

5. 汇报结果
"""
        logger.info("发送 skill 内容到 SkillRunner...")
        start_time = time.time()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行多步骤测试",
            skill_name="test-multi-step",
        )

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"[TEST RESULT] test_skill_with_multiple_todos")
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
        for i in range(1, 6):
            fpath = os.path.join(temp_workspace, f"step{i}.txt")
            if os.path.exists(fpath):
                logger.info(f"文件已创建: step{i}.txt")
                with open(fpath, "r") as f:
                    logger.info(f"  内容: {f.read().strip()}")
            else:
                logger.warning(f"文件未创建: step{i}.txt")

        assert result["status"] == "success"
        assert result["progress"]["total"] >= 5
        logger.info("[TEST PASS] test_skill_with_multiple_todos")

    def test_skill_early_termination_recovery(self, skill_runner, temp_workspace):
        """测试小模型提前终止后恢复"""
        logger.info("=" * 60)
        logger.info("[TEST START] test_skill_early_termination_recovery")
        logger.info(f"工作目录: {temp_workspace}")

        skill_content = """---
name: test-recovery
description: "测试恢复能力"
---

# 恢复测试 Skill

## 步骤
1. 使用 todowrite 创建以下任务：
   - 创建 recovery1.txt，写入 "Recovery 1"
   - 创建 recovery2.txt，写入 "Recovery 2"
   - 创建 recovery3.txt，写入 "Recovery 3"

2. 逐个执行

3. 验证所有文件存在

4. 标记所有任务为 completed

5. 汇报结果
"""
        logger.info("发送 skill 内容到 SkillRunner...")
        start_time = time.time()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行恢复测试",
            skill_name="test-recovery",
        )

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"[TEST RESULT] test_skill_early_termination_recovery")
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
        for i in range(1, 4):
            fpath = os.path.join(temp_workspace, f"recovery{i}.txt")
            if os.path.exists(fpath):
                logger.info(f"文件已创建: recovery{i}.txt")
            else:
                logger.warning(f"文件未创建: recovery{i}.txt")

        assert result["status"] == "success" or result["restart_count"] > 0
        logger.info("[TEST PASS] test_skill_early_termination_recovery")

    def test_skill_error_handling(self, skill_runner, temp_workspace):
        """测试 skill 执行中出错的处理"""
        logger.info("=" * 60)
        logger.info("[TEST START] test_skill_error_handling")
        logger.info(f"工作目录: {temp_workspace}")

        skill_content = """---
name: test-error
description: "错误处理测试"
---

# 错误处理测试

## 步骤
1. 使用 todowrite 创建任务：
   - 尝试运行一个不存在的命令
   - 汇报错误信息
   - 标记任务为 completed

2. 执行任务

3. 汇报结果
"""
        logger.info("发送 skill 内容到 SkillRunner...")
        start_time = time.time()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行错误处理测试",
            skill_name="test-error",
        )

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"[TEST RESULT] test_skill_error_handling")
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

        assert result is not None
        assert "error" in result
        logger.info("[TEST PASS] test_skill_error_handling")

    def test_skill_progress_tracking(self, skill_runner, temp_workspace):
        """测试进度追踪准确性"""
        logger.info("=" * 60)
        logger.info("[TEST START] test_skill_progress_tracking")
        logger.info(f"工作目录: {temp_workspace}")

        skill_content = """---
name: test-progress
description: "进度追踪测试"
---

# 进度追踪测试

## 步骤
1. 使用 todowrite 创建 3 个任务：
   - 创建 progress_test.txt
   - 验证文件存在
   - 删除文件

2. 逐个执行

3. 汇报结果
"""
        logger.info("发送 skill 内容到 SkillRunner...")
        start_time = time.time()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行进度追踪测试",
            skill_name="test-progress",
        )

        elapsed = time.time() - start_time
        logger.info("=" * 60)
        logger.info(f"[TEST RESULT] test_skill_progress_tracking")
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

        assert result["progress"]["total"] > 0
        assert result["progress"]["completed"] == result["progress"]["total"]
        assert len(result["todos"]) > 0
        logger.info("[TEST PASS] test_skill_progress_tracking")
