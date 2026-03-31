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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.opencode_client import OpenCodeClient
from src.skill_runner import SkillRunner

BASE_URL = "http://localhost:4096"
SERVER_PORT = 4096


@pytest.fixture(scope="module")
def opencode_server():
    """启动 opencode serve 用于测试"""
    # 检查是否已有 server 运行
    client = OpenCodeClient(BASE_URL)
    if client.health_check():
        yield client
        return

    # 启动新 server
    proc = subprocess.Popen(
        ["opencode", "serve", "--port", str(SERVER_PORT), "--hostname", "localhost"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    # 等待 server 启动
    for i in range(30):
        time.sleep(1)
        if client.health_check():
            break
    else:
        proc.terminate()
        pytest.skip("Failed to start opencode server")

    yield client

    # 清理
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
        max_restarts=3,
        stuck_threshold=120,
        max_execution_time=300,
        poll_interval=5,
        verification_stable_count=2,
    )


@pytest.mark.integration
class TestSkillRunnerIntegration:
    """集成测试 - 需要真实 opencode"""

    def test_simple_skill_execution(self, skill_runner):
        """测试简单 skill 完整执行

        使用一个极简 skill：创建文件并验证内容。
        """
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
        result = skill_runner.run(
            skill_content=skill_content,
            user_request="创建 hello.py 并验证内容",
            skill_name="test-simple",
        )

        assert result["status"] == "success"
        assert result["progress"]["completed"] == result["progress"]["total"]
        assert result["progress"]["total"] > 0

    def test_skill_with_multiple_todos(self, skill_runner):
        """测试多步骤 skill（5+ todos）

        验证 agent 能按顺序完成多个步骤。
        """
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
        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行多步骤测试",
            skill_name="test-multi-step",
        )

        assert result["status"] == "success"
        assert result["progress"]["total"] >= 5

    def test_skill_early_termination_recovery(self, skill_runner):
        """测试小模型提前终止后恢复

        这个测试验证当 agent 只完成部分任务就停止时，
        SkillRunner 能检测到并重启继续执行。
        """
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
        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行恢复测试",
            skill_name="test-recovery",
        )

        # 无论是否触发重启，最终应该成功
        assert result["status"] == "success" or result["restart_count"] > 0

    def test_skill_error_handling(self, skill_runner):
        """测试 skill 执行中出错的处理

        验证当 skill 执行遇到错误时，不会导致编排器崩溃。
        """
        # 使用一个会导致错误的 skill（不存在的命令）
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
        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行错误处理测试",
            skill_name="test-error",
        )

        # 不应该抛出异常
        assert result is not None
        assert "error" in result

    def test_skill_progress_tracking(self, skill_runner):
        """测试进度追踪准确性

        验证 progress 和 todos 信息正确。
        """
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
        result = skill_runner.run(
            skill_content=skill_content,
            user_request="执行进度追踪测试",
            skill_name="test-progress",
        )

        assert result["progress"]["total"] > 0
        assert result["progress"]["completed"] == result["progress"]["total"]
        assert len(result["todos"]) > 0
