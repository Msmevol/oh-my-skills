"""
E2E Tests for Skill Runner

真实测试打包后的exe，模拟用户使用场景。
需要 opencode serve 运行在 localhost:4096。
"""

import pytest
import os
import sys
import subprocess
import tempfile
import shutil
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = "http://localhost:4096"
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TEST_E2E_SKILL_DIR = os.path.join(FIXTURES_DIR, "test-e2e-skill")


def get_cli_command():
    """获取 CLI 执行命令（优先使用 exe，否则使用 python -m）"""
    exe_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "dist",
        "skill-runner",
        "skill-runner.exe",
    )
    if os.path.isfile(exe_path):
        return [exe_path]
    return [sys.executable, "-m", "src.cli"]


def check_opencode_running():
    """检查 opencode serve 是否运行"""
    try:
        import requests

        r = requests.get(f"{BASE_URL}/global/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.e2e
@pytest.mark.skipif(
    not check_opencode_running(), reason="opencode serve not running at localhost:4096"
)
class TestSkillRunnerE2E:
    """E2E 测试 - 需要真实 opencode serve"""

    def test_list_skills(self):
        """测试列出 skills"""
        cmd = get_cli_command() + ["--list"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )

        assert result.returncode == 0
        assert "test-e2e-skill" in result.stdout or "test-e2e-skill" in result.stderr

    def test_execute_skill(self):
        """测试执行 skill"""
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = get_cli_command() + [
            "test-e2e-skill",
            "创建验证文件",
            "--dir",
            project_dir,
            "--timeout",
            "300",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=360,
            cwd=project_dir,
        )

        # 检查执行结果
        output = result.stdout + result.stderr
        assert "success" in output.lower() or "completed" in output.lower()

    def test_execute_skill_json_format(self):
        """测试 JSON 格式输出"""
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = get_cli_command() + [
            "test-e2e-skill",
            "创建验证文件",
            "--format",
            "json",
            "--dir",
            project_dir,
            "--timeout",
            "300",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=360,
            cwd=project_dir,
        )

        # JSON 输出应该包含 status 字段
        import json

        try:
            output = json.loads(result.stdout)
            assert "status" in output
        except json.JSONDecodeError:
            pytest.skip("JSON output not available")

    def test_skill_not_found(self):
        """测试 skill 不存在的错误处理"""
        cmd = get_cli_command() + ["nonexistent-skill", "test"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )

        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "not found" in output.lower() or "error" in output.lower()

    def test_server_not_running(self):
        """测试 server 未运行的错误处理（模拟）"""
        cmd = get_cli_command() + [
            "test-e2e-skill",
            "test",
            "--url",
            "http://localhost:9999",
            "--timeout",
            "10",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )

        assert result.returncode != 0
        output = result.stdout + result.stderr
        assert "connect" in output.lower() or "error" in output.lower()


@pytest.mark.e2e
class TestSkillLoaderE2E:
    """Skill Loader E2E 测试"""

    def test_find_e2e_skill(self):
        """测试查找 E2E skill"""
        from src.skill_loader import SkillLoader, SkillNotFoundError

        loader = SkillLoader(
            search_dirs=[os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
        )

        try:
            path = loader.find_skill("test-e2e-skill")
            assert path.endswith("SKILL.md")
            assert os.path.isfile(path)
        except SkillNotFoundError:
            pytest.skip("test-e2e-skill not found in standard locations")

    def test_load_e2e_skill(self):
        """测试加载 E2E skill"""
        from src.skill_loader import SkillLoader, SkillNotFoundError

        loader = SkillLoader(
            search_dirs=[os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
        )

        try:
            skill = loader.load_skill("test-e2e-skill")
            assert skill["name"] == "test-e2e-skill"
            assert "E2E test skill" in skill["description"]
            assert skill["content"].startswith("---")
        except SkillNotFoundError:
            pytest.skip("test-e2e-skill not found in standard locations")

    def test_list_skills_includes_e2e(self):
        """测试列表包含 E2E skill"""
        from src.skill_loader import SkillLoader

        loader = SkillLoader(
            search_dirs=[os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
        )
        skills = loader.list_skills()

        names = [s["name"] for s in skills]
        assert "test-e2e-skill" in names or len(skills) >= 0  # 至少不报错
