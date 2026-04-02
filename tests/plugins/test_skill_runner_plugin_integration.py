"""
Plugin and SkillRunner Integration Tests

测试插件与 SkillRunner 的集成，使用 mock 模拟 opencode 行为。
"""

import pytest
import time
import sys
import os
from unittest.mock import MagicMock, patch

# Add the src directory's parent to sys.path so 'src' is a proper package
src_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"
)
sys.path.insert(0, os.path.dirname(src_dir))

from src.skill_runner import SkillRunner
from src.plugins import (
    PluginRegistry,
    DetectionPlugin,
    DetectionResult,
    RecoveryPlugin,
    VerificationPlugin,
)
from src.plugins.builtin_detectors import (
    StuckDetector,
    IdleIncompleteDetector,
    PrematureEndDetector,
    SessionInvalidDetector,
)
from src.plugins.builtin_recovery import RestartRecovery
from src.plugins.builtin_verification import (
    StableCompletionVerifier,
    TodoExecutionVerifier,
)
from src.agent_session import AgentSession


def make_mock_client():
    """创建 mock opencode 客户端"""
    client = MagicMock()
    client.health_check.return_value = True
    client.create_session.return_value = {"id": "mock-session-1"}
    client.send_message.return_value = {"status": "ok"}
    client.get_session_status.return_value = {"mock-session-1": {"state": "idle"}}
    client.get_session.return_value = {"state": "idle"}
    client.get_todo.return_value = []
    client.get_messages.return_value = []
    client.abort_session.return_value = None
    return client


class TestSkillRunnerPluginIntegration:
    """测试 SkillRunner 与插件的集成"""

    def test_skill_runner_registers_builtin_plugins(self):
        """SkillRunner 注册所有内置插件"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=60,
            max_execution_time=120,
            poll_interval=1,
            verification_stable_count=2,
        )

        plugins = runner.registry.list_plugins()
        assert "stuck_detector" in plugins["detection"]
        assert "idle_incomplete_detector" in plugins["detection"]
        assert "premature_end_detector" in plugins["detection"]
        assert "session_invalid_detector" in plugins["detection"]
        assert "restart_recovery" in plugins["recovery"]
        assert "stable_completion_verifier" in plugins["verification"]
        assert "todo_execution_verifier" in plugins["verification"]

    def test_skill_runner_uses_plugins_for_detection(self):
        """SkillRunner 使用插件进行检测"""
        client = make_mock_client()
        # 模拟 session 卡死
        client.get_session_status.return_value = {
            "mock-session-1": {"state": "busy", "busy_since": time.time() - 100}
        }
        client.get_session.return_value = {"state": "busy"}

        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=1,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        session = AgentSession(
            session_id="mock-session-1",
            agent_name="test-agent",
            client=client,
            stuck_threshold=10,
        )
        session.state = "busy"
        session.last_activity_time = time.time() - 100

        detection = runner.registry.run_all_detections(session, client)
        assert detection is not None
        assert detection.detected is True

    def test_skill_runner_uses_plugins_for_recovery(self):
        """SkillRunner 使用插件进行恢复"""
        client = make_mock_client()
        client.create_session.return_value = {"id": "recovered-session-1"}
        client.send_message.return_value = {"status": "ok"}

        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        old_session = AgentSession(
            session_id="old-session-1",
            agent_name="test-agent",
            client=client,
        )
        old_session.todos = [{"status": "pending", "content": "task1"}]

        detection = MagicMock()
        detection.detected = True
        detection.reason = "Session stuck"
        detection.severity = "high"

        context = {"detection": detection, "restart_count": 1, "max_restarts": 3}
        new_session = runner.registry.run_recovery(old_session, client, context)
        assert new_session is not None
        client.create_session.assert_called_once()
        client.send_message.assert_called_once()

    def test_skill_runner_uses_plugins_for_verification(self):
        """SkillRunner 使用插件进行验证"""
        client = make_mock_client()
        client.get_session_status.return_value = {"mock-session-1": {"state": "idle"}}
        client.get_session.return_value = {"state": "idle"}
        client.get_todo.return_value = [{"status": "completed", "content": "task1"}]
        # TodoExecutionVerifier needs messages with {"info": {"role": "assistant"}}
        client.get_messages.return_value = [
            {"info": {"role": "assistant"}, "content": "done task1"},
            {"info": {"role": "assistant"}, "content": "verification passed"},
        ]

        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        # Only test StableCompletionVerifier by clearing TodoExecutionVerifier
        runner.registry._verification_plugins = [
            (p, plugin)
            for p, plugin in runner.registry._verification_plugins
            if plugin.name != "todo_execution_verifier"
        ]

        session = AgentSession(
            session_id="mock-session-1",
            agent_name="test-agent",
            client=client,
        )
        session.todos = [{"status": "completed", "content": "task1"}]

        verification_passed = runner.registry.run_all_verifications(session, client)
        assert verification_passed is True


class TestSkillRunnerWithCustomPlugins:
    """测试 SkillRunner 与自定义插件的集成"""

    def test_add_custom_detection_plugin(self):
        """添加自定义检测插件"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        class CustomDetector(DetectionPlugin):
            @property
            def name(self):
                return "custom_detector"

            def detect(self, session, client):
                return DetectionResult(detected=False)

        runner.registry.register_detection(CustomDetector(), priority=100)
        plugins = runner.registry.list_plugins()
        assert "custom_detector" in plugins["detection"]

    def test_add_custom_recovery_plugin(self):
        """添加自定义恢复插件"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        class CustomRecovery(RecoveryPlugin):
            @property
            def name(self):
                return "custom_recovery"

            def recover(self, session, client, context):
                new_session = MagicMock()
                new_session.session_id = "custom-recovered"
                return new_session

        runner.registry.register_recovery(CustomRecovery(), priority=100)
        plugins = runner.registry.list_plugins()
        assert "custom_recovery" in plugins["recovery"]

    def test_add_custom_verification_plugin(self):
        """添加自定义验证插件"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        class CustomVerification(VerificationPlugin):
            @property
            def name(self):
                return "custom_verification"

            def verify(self, session, client):
                return True

        runner.registry.register_verification(CustomVerification(), priority=100)
        plugins = runner.registry.list_plugins()
        assert "custom_verification" in plugins["verification"]


class TestSkillRunnerPluginExecutionOrder:
    """测试 SkillRunner 插件执行顺序"""

    def test_detection_plugins_execute_in_priority_order(self):
        """检测插件按优先级顺序执行"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        execution_order = []

        class OrderDetector(DetectionPlugin):
            def __init__(self, name, order_list):
                self._name = name
                self._order = order_list

            @property
            def name(self):
                return self._name

            def detect(self, session, client):
                self._order.append(self._name)
                return DetectionResult(detected=False)

        order = []
        runner.registry.register_detection(OrderDetector("low", order), priority=1)
        runner.registry.register_detection(OrderDetector("high", order), priority=10)

        session = AgentSession(
            session_id="test-1",
            agent_name="test-agent",
            client=client,
        )

        runner.registry.run_all_detections(session, client)
        assert order == ["high", "low"]

    def test_recovery_plugins_execute_in_priority_order(self):
        """恢复插件按优先级顺序执行"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        execution_order = []

        class OrderRecovery(RecoveryPlugin):
            def __init__(self, name, order_list, succeed=False):
                self._name = name
                self._order = order_list
                self._succeed = succeed

            @property
            def name(self):
                return self._name

            def recover(self, session, client, context):
                self._order.append(self._name)
                if self._succeed:
                    new_session = MagicMock()
                    new_session.session_id = "recovered"
                    return new_session
                return None

        order = []
        # Clear existing recovery plugins first
        runner.registry._recovery_plugins = []
        runner.registry.register_recovery(
            OrderRecovery("high", order, succeed=True), priority=10
        )
        runner.registry.register_recovery(OrderRecovery("low", order), priority=1)

        session = AgentSession(
            session_id="test-1",
            agent_name="test-agent",
            client=client,
        )

        result = runner.registry.run_recovery(session, client, {"restart_count": 1})
        assert order == ["high"]
        assert result is not None


class TestSkillRunnerPluginErrorHandling:
    """测试 SkillRunner 插件错误处理"""

    def test_detection_error_does_not_crash_runner(self):
        """检测插件错误不导致 runner 崩溃"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        class ErrorDetector(DetectionPlugin):
            @property
            def name(self):
                return "error_detector"

            def detect(self, session, client):
                raise RuntimeError("Detection error")

        runner.registry.register_detection(ErrorDetector(), priority=100)

        session = AgentSession(
            session_id="test-1",
            agent_name="test-agent",
            client=client,
        )

        # 不应该抛出异常
        result = runner.registry.run_all_detections(session, client)
        assert result is None

    def test_recovery_error_does_not_crash_runner(self):
        """恢复插件错误不导致 runner 崩溃"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=10,
            max_execution_time=30,
            poll_interval=1,
            verification_stable_count=1,
        )

        class ErrorRecovery(RecoveryPlugin):
            @property
            def name(self):
                return "error_recovery"

            def recover(self, session, client, context):
                raise RuntimeError("Recovery error")

        # Clear existing recovery plugins and add error one
        runner.registry._recovery_plugins = []
        runner.registry.register_recovery(ErrorRecovery(), priority=100)

        session = AgentSession(
            session_id="test-1",
            agent_name="test-agent",
            client=client,
        )

        # 不应该抛出异常
        result = runner.registry.run_recovery(session, client, {"restart_count": 1})
        assert result is None


class TestSkillRunnerPluginConfiguration:
    """测试 SkillRunner 插件配置"""

    def test_stuck_threshold_configures_detector(self):
        """stuck_threshold 正确配置 StuckDetector"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=120,
            max_execution_time=300,
            poll_interval=1,
            verification_stable_count=2,
        )

        # 检查 StuckDetector 的 timeout
        for _, plugin in runner.registry._detection_plugins:
            if plugin.name == "stuck_detector":
                assert plugin._timeout == 120
                break
        else:
            pytest.fail("stuck_detector not found")

    def test_verification_stable_count_configures_verifier(self):
        """verification_stable_count 正确配置 StableCompletionVerifier"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=3,
            stuck_threshold=60,
            max_execution_time=120,
            poll_interval=1,
            verification_stable_count=5,
        )

        # 检查 StableCompletionVerifier 的 stable_count
        for _, plugin in runner.registry._verification_plugins:
            if plugin.name == "stable_completion_verifier":
                assert plugin._stable_count == 5
                break
        else:
            pytest.fail("stable_completion_verifier not found")

    def test_max_restarts_configures_recovery(self):
        """max_restarts 正确配置恢复上下文"""
        client = make_mock_client()
        runner = SkillRunner(
            client=client,
            agent_name="test-agent",
            max_restarts=5,
            stuck_threshold=60,
            max_execution_time=120,
            poll_interval=1,
            verification_stable_count=2,
        )

        assert runner.max_restarts == 5
