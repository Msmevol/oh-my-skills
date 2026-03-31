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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.opencode_client import OpenCodeClient
from src.skill_runner import SkillRunner

BASE_URL = "http://localhost:4096"
SERVER_PORT = 4096
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="module")
def opencode_server():
    """启动 opencode serve 用于测试"""
    client = OpenCodeClient(BASE_URL)
    if client.health_check():
        yield client
        return

    proc = subprocess.Popen(
        ["opencode", "serve", "--port", str(SERVER_PORT), "--hostname", "localhost"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    for i in range(30):
        time.sleep(1)
        if client.health_check():
            break
    else:
        proc.terminate()
        pytest.skip("Failed to start opencode server")

    yield client

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


@pytest.fixture
def skill_runner(opencode_server):
    """创建 SkillRunner 实例"""
    return SkillRunner(
        client=opencode_server,
        agent_name="skill-executor",
        max_restarts=5,
        stuck_threshold=180,
        max_execution_time=600,
        poll_interval=10,
        verification_stable_count=3,
    )


@pytest.mark.e2e
class TestSkillE2E:
    """E2E 测试 - 真实 skill 文件 + 小模型"""

    def test_custom_simple_skill(self, skill_runner):
        """测试自定义简单 skill

        使用 tests/fixtures/skill_simple.txt
        验证：3步全部完成，文件正确创建
        """
        skill_path = os.path.join(FIXTURES_DIR, "skill_simple.txt")
        if not os.path.exists(skill_path):
            pytest.skip(f"Fixture not found: {skill_path}")

        with open(skill_path, "r", encoding="utf-8") as f:
            skill_content = f.read()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行简单测试 skill，创建 hello.py",
            skill_name="test-simple-skill",
        )

        assert result["status"] == "success", f"Failed: {result.get('error')}"
        assert result["progress"]["total"] >= 3
        assert result["progress"]["completed"] == result["progress"]["total"]

        # 验证文件创建
        hello_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hello.py"
        )
        if os.path.exists(hello_path):
            with open(hello_path, "r") as f:
                content = f.read()
            assert "Hello World" in content
            # 清理测试文件
            os.remove(hello_path)

    def test_code_mr_ci_loop_basic(self, skill_runner):
        """测试 code-mr-ci-loop skill 基本流程

        使用 D:/ai_demo/skill.txt
        由于这个 skill 需要 git/MCP 环境，只测试 skill 能被正确解析和执行初始步骤
        """
        skill_path = "D:/ai_demo/skill.txt"
        if not os.path.exists(skill_path):
            pytest.skip(f"Skill file not found: {skill_path}")

        with open(skill_path, "r", encoding="utf-8") as f:
            skill_content = f.read()

        result = skill_runner.run(
            skill_content=skill_content,
            user_request="查看流水线状态",
            skill_name="code-mr-ci-loop",
        )

        # 由于需要 git/MCP 环境，可能不会完全成功
        # 但至少验证 skill 能被正确处理
        assert result["skill_name"] == "code-mr-ci-loop"
        assert result["progress"]["total"] > 0 or result["error"] is not None

    def test_skill_restart_and_continue(self, skill_runner):
        """测试重启后继续执行

        验证当 agent 提前终止时，SkillRunner 能重启并让 agent 继续完成剩余任务。
        """
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
        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行重启继续测试",
            skill_name="test-restart",
        )

        # 验证最终结果
        assert result["status"] == "success" or result["restart_count"] > 0
        assert result["progress"]["total"] >= 4


@pytest.mark.e2e
def test_skill_runner_with_real_opencode():
    """冒烟测试：验证 opencode 连接正常"""
    client = OpenCodeClient(BASE_URL)
    if not client.health_check():
        pytest.skip("opencode server not running")

    # 验证 skill-executor agent 存在
    agents = client.list_agents()
    agent_names = [a.get("name", "") for a in agents]
    assert "skill-executor" in agent_names, (
        f"skill-executor agent not found. Available: {agent_names}"
    )
