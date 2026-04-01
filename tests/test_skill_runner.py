"""
Tests for the Universal Skill Runner (Plugin Architecture)

三层测试：
1. 单元测试（Mock，不需要真实 opencode）
2. 集成测试（需要真实 opencode）
3. E2E 测试（真实 skill 文件 + 小模型）
"""

import unittest
from unittest.mock import MagicMock, patch
import time

from src.skill_runner import SkillRunner, SkillRunnerError
from src.plugins import PluginRegistry, DetectionResult, DetectionPlugin


class TestSkillRunnerUnit(unittest.TestCase):
    """单元测试 - 使用 Mock，不需要真实 opencode"""

    def setUp(self):
        self.mock_client = MagicMock()
        self.runner = SkillRunner(
            client=self.mock_client,
            agent_name="skill-executor",
            max_restarts=3,
            stuck_threshold=60,
            max_execution_time=120,
            poll_interval=1,
            verification_stable_count=2,
        )

    def test_init_defaults(self):
        """测试默认参数初始化"""
        assert self.runner.agent_name == "skill-executor"
        assert self.runner.max_restarts == 3
        assert self.runner.poll_interval == 1
        assert self.runner.verification_stable_count == 2
        assert self.runner.session is None
        assert self.runner.execution_log == []
        assert self.runner.registry is not None
        assert self.runner.registry.detection_count > 0
        assert self.runner.registry.recovery_count > 0
        assert self.runner.registry.verification_count > 0

    def test_build_prompt(self):
        """测试 prompt 构建正确性"""
        skill_content = "# Test Skill\n\n步骤1: 创建文件\n步骤2: 验证"
        user_request = "请执行这个 skill"

        prompt = self.runner._build_prompt(skill_content, user_request)

        assert "# Test Skill" in prompt
        assert "步骤1: 创建文件" in prompt
        assert "请执行这个 skill" in prompt
        assert "todowrite" in prompt
        assert "不要跳步" in prompt
        assert "不要提前结束" in prompt

    def test_custom_plugin_registry(self):
        """测试自定义插件注册表"""
        custom_registry = PluginRegistry()
        runner = SkillRunner(
            client=self.mock_client,
            plugin_registry=custom_registry,
        )
        assert runner.registry is custom_registry
        assert runner.registry.detection_count == 0

    @patch.object(SkillRunner, "_create_session")
    @patch.object(SkillRunner, "_all_todos_completed")
    @patch.object(SkillRunner, "_verify_completion")
    @patch.object(SkillRunner, "_get_progress")
    @patch.object(SkillRunner, "_get_todos")
    def test_run_success(
        self, mock_get_todos, mock_get_progress, mock_verify, mock_all_done, mock_create
    ):
        """测试正常完成流程"""
        mock_session = MagicMock()
        mock_session.session_id = "test-session-1"
        mock_create.return_value = mock_session

        mock_all_done.return_value = True
        mock_verify.return_value = True
        mock_get_progress.return_value = {
            "total": 3,
            "completed": 3,
            "pending": 0,
            "percentage": 100.0,
        }
        mock_get_todos.return_value = [
            {"content": "t1", "status": "completed"},
            {"content": "t2", "status": "completed"},
            {"content": "t3", "status": "completed"},
        ]

        result = self.runner.run(
            skill_content="# Test Skill",
            user_request="Execute this skill",
            skill_name="test-skill",
        )

        assert result["status"] == "success"
        assert result["skill_name"] == "test-skill"
        assert result["progress"]["completed"] == 3
        assert result["restart_count"] == 0
        assert mock_create.called
        assert mock_session.send.called

    def test_run_detection_triggers_restart(self):
        """测试检测插件触发重启"""

        class TriggerOnceDetector(DetectionPlugin):
            def __init__(self):
                self.triggered = False

            @property
            def name(self):
                return "trigger_once"

            def detect(self, session, client):
                if not self.triggered:
                    self.triggered = True
                    return DetectionResult(
                        detected=True,
                        reason="test detection",
                        severity="high",
                    )
                return DetectionResult(detected=False)

        detector = TriggerOnceDetector()
        self.runner.registry.register_detection(detector, priority=200)
        self.runner.poll_interval = 0.1
        self.runner.max_restarts = 3

        mock_session = MagicMock()
        mock_session.session_id = "test-session-1"
        mock_session.get_progress.return_value = {
            "total": 3,
            "completed": 1,
            "pending": 2,
            "percentage": 33.3,
        }
        self.runner.session = mock_session

        self.mock_client.get_session_status.return_value = {
            "test-session-1": {"state": "idle"},
            "new-session": {"state": "idle"},
        }
        self.mock_client.get_todo.return_value = [
            {"content": "t1", "status": "completed"},
            {"content": "t2", "status": "pending"},
        ]
        self.mock_client.create_session.return_value = "new-session"
        self.mock_client.send_message.return_value = {}
        self.mock_client.abort_session.return_value = True

        result = self.runner.run(
            skill_content="# Test Skill",
            user_request="Execute",
            skill_name="test-skill",
        )

        assert result["restart_count"] >= 1
        assert self.mock_client.create_session.called
        assert self.mock_client.abort_session.called

    @patch.object(SkillRunner, "_create_session")
    @patch.object(SkillRunner, "_all_todos_completed")
    @patch.object(SkillRunner, "_verify_completion")
    @patch.object(SkillRunner, "_get_progress")
    @patch.object(SkillRunner, "_get_todos")
    def test_run_max_restarts(
        self, mock_get_todos, mock_get_progress, mock_verify, mock_all_done, mock_create
    ):
        """测试超过最大重启次数"""
        mock_session = MagicMock()
        mock_session.session_id = "test-session-1"
        mock_create.return_value = mock_session

        mock_all_done.return_value = False
        mock_get_progress.return_value = {
            "total": 3,
            "completed": 0,
            "pending": 3,
            "percentage": 0,
        }
        mock_get_todos.return_value = [
            {"content": "t1", "status": "pending"},
        ]

        class AlwaysDetect(DetectionPlugin):
            @property
            def name(self):
                return "always_detect"

            def detect(self, session, client):
                return DetectionResult(
                    detected=True,
                    reason="always detected",
                    severity="high",
                )

        self.runner.registry.register_detection(AlwaysDetect(), priority=200)
        self.runner.max_restarts = 3
        self.runner.poll_interval = 0.1

        result = self.runner.run(
            skill_content="# Test Skill",
            user_request="Execute",
            skill_name="test-skill",
        )

        assert result["status"] == "failed"
        assert "Max restarts" in result["error"]
        assert result["restart_count"] == 3

    def test_run_timeout(self):
        """测试执行超时"""
        mock_session = MagicMock()
        mock_session.session_id = "test-session-1"
        mock_session.get_progress.return_value = {
            "total": 3,
            "completed": 1,
            "pending": 2,
            "percentage": 33.3,
        }
        self.runner.session = mock_session

        self.mock_client.get_session_status.return_value = {
            "test-session-1": {"state": "busy"}
        }

        self.runner.max_execution_time = 0
        self.runner.poll_interval = 0.1

        result = self.runner.run(
            skill_content="# Test Skill",
            user_request="Execute",
            skill_name="test-skill",
        )

        assert result["status"] == "failed"
        assert "timed out" in result["error"]

    @patch.object(SkillRunner, "_create_session")
    @patch.object(SkillRunner, "_all_todos_completed")
    @patch.object(SkillRunner, "_verify_completion")
    @patch.object(SkillRunner, "_get_progress")
    @patch.object(SkillRunner, "_get_todos")
    def test_run_detection_restart_then_succeed(
        self, mock_get_todos, mock_get_progress, mock_verify, mock_all_done, mock_create
    ):
        """测试检测触发重启后最终成功"""
        mock_session1 = MagicMock()
        mock_session1.session_id = "test-session-1"
        mock_session2 = MagicMock()
        mock_session2.session_id = "test-session-2"

        call_count = [0]

        def create_side_effect(*args):
            call_count[0] += 1
            return mock_session1 if call_count[0] == 1 else mock_session2

        mock_create.side_effect = create_side_effect

        mock_all_done.side_effect = [False, True]
        mock_verify.return_value = True
        mock_get_progress.return_value = {
            "total": 3,
            "completed": 1,
            "pending": 2,
            "percentage": 33.3,
        }
        mock_get_todos.return_value = [
            {"content": "t1", "status": "completed"},
            {"content": "t2", "status": "pending"},
        ]

        class TriggerOnceDetector(DetectionPlugin):
            def __init__(self):
                self.triggered = False

            @property
            def name(self):
                return "trigger_once"

            def detect(self, session, client):
                if not self.triggered:
                    self.triggered = True
                    return DetectionResult(
                        detected=True,
                        reason="idle with incomplete todos",
                        severity="high",
                    )
                return DetectionResult(detected=False)

        detector = TriggerOnceDetector()
        self.runner.registry.register_detection(detector, priority=200)

        self.runner.max_restarts = 3
        self.runner.poll_interval = 0.1

        result = self.runner.run(
            skill_content="# Test Skill",
            user_request="Execute",
            skill_name="test-skill",
        )

        assert result["restart_count"] >= 1
        assert result["status"] == "success"

    def test_verify_completion(self):
        """测试通过插件验证完成"""
        mock_session = MagicMock()
        mock_session.session_id = "test-session-1"
        mock_session.get_progress.return_value = {
            "total": 3,
            "completed": 3,
            "pending": 0,
            "percentage": 100.0,
        }
        self.runner.session = mock_session
        self.mock_client.get_session_status.return_value = {
            "test-session-1": {"state": "idle"}
        }
        self.mock_client.get_todo.return_value = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "completed"},
            {"id": "3", "content": "Task 3", "status": "completed"},
        ]
        self.mock_client.get_messages.return_value = [
            {"info": {"role": "assistant"}},
            {"info": {"role": "assistant"}},
            {"info": {"role": "assistant"}},
        ]

        result = self.runner._verify_completion()
        assert result is True

    def test_get_progress_no_session(self):
        """测试无 session 时获取进度"""
        self.runner.session = None
        progress = self.runner._get_progress()
        assert progress == {"total": 0, "completed": 0, "pending": 0, "percentage": 0}

    def test_all_todos_completed(self):
        """测试 all_todos_completed 检查"""
        mock_session = MagicMock()
        mock_session.session_id = "test-session-1"
        mock_session.get_progress.return_value = {
            "total": 3,
            "completed": 3,
            "pending": 0,
            "percentage": 100.0,
        }
        self.runner.session = mock_session
        assert self.runner._all_todos_completed() == True

        mock_session.get_progress.return_value = {
            "total": 3,
            "completed": 2,
            "pending": 1,
            "percentage": 66.7,
        }
        assert self.runner._all_todos_completed() == False


class TestSkillRunnerEdgeCases(unittest.TestCase):
    """边界情况测试"""

    def setUp(self):
        self.mock_client = MagicMock()
        self.runner = SkillRunner(
            client=self.mock_client,
            agent_name="skill-executor",
            max_restarts=2,
            max_execution_time=10,
            poll_interval=0.5,
        )

    def test_empty_skill_content(self):
        """测试空 skill 内容"""
        mock_session = MagicMock()
        mock_session.session_id = "test-1"
        mock_session.get_progress.return_value = {
            "total": 0,
            "completed": 0,
            "pending": 0,
            "percentage": 0,
        }
        self.runner.session = mock_session

        self.mock_client.get_session_status.return_value = {"test-1": {"state": "idle"}}
        self.mock_client.get_todo.return_value = []

        result = self.runner.run("", "Execute", "empty-skill")
        assert result["status"] == "failed"

    @patch.object(SkillRunner, "_create_session")
    @patch.object(SkillRunner, "_all_todos_completed")
    @patch.object(SkillRunner, "_verify_completion")
    @patch.object(SkillRunner, "_get_progress")
    @patch.object(SkillRunner, "_get_todos")
    def test_execution_log_records_events(
        self, mock_get_todos, mock_get_progress, mock_verify, mock_all_done, mock_create
    ):
        """测试执行日志记录事件"""
        mock_session1 = MagicMock()
        mock_session1.session_id = "test-1"
        mock_session2 = MagicMock()
        mock_session2.session_id = "test-2"

        call_count = [0]

        def create_side_effect(*args):
            call_count[0] += 1
            return mock_session1 if call_count[0] == 1 else mock_session2

        mock_create.side_effect = create_side_effect

        mock_all_done.side_effect = [False, True]
        mock_verify.return_value = True
        mock_get_progress.return_value = {
            "total": 2,
            "completed": 0,
            "pending": 2,
            "percentage": 0,
        }
        mock_get_todos.return_value = [
            {"content": "t1", "status": "pending"},
        ]

        class TriggerOnceDetector(DetectionPlugin):
            def __init__(self):
                self.triggered = False

            @property
            def name(self):
                return "trigger_once"

            def detect(self, session, client):
                if not self.triggered:
                    self.triggered = True
                    return DetectionResult(
                        detected=True,
                        reason="test detection",
                        severity="high",
                    )
                return DetectionResult(detected=False)

        detector = TriggerOnceDetector()
        self.runner.registry.register_detection(detector, priority=200)

        self.runner.poll_interval = 0.1

        result = self.runner.run("# Skill", "Execute", "test")

        assert len(result["execution_log"]) > 0
        events = [e["event"] for e in result["execution_log"]]
        assert "started" in events
        assert "detection_triggered" in events
        assert "recovered" in events


if __name__ == "__main__":
    unittest.main()
