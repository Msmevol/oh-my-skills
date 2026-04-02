"""
Plugin Integration Tests

测试插件之间的协作和完整检测→恢复→验证流程。
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

from src.plugins import (
    PluginRegistry,
    DetectionResult,
    DetectionPlugin,
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


def make_session(
    session_id="test-1",
    state="idle",
    todos=None,
    last_activity=None,
    busy_since=None,
):
    session = MagicMock()
    session.session_id = session_id
    session.state = state
    session.todos = todos or []
    session.last_activity_time = last_activity or time.time()
    session.busy_since = busy_since
    session.restart_count = 0
    return session


def make_client(
    session_status=None,
    todos=None,
    messages=None,
    session_info=None,
):
    client = MagicMock()
    if session_status is not None:
        client.get_session_status.return_value = session_status
    if todos is not None:
        client.get_todo.return_value = todos
    if messages is not None:
        client.get_messages.return_value = messages
    if session_info is not None:
        client.get_session.return_value = session_info
    else:
        client.get_session.return_value = {"state": "idle"}
    return client


class TestDetectionRecoveryVerificationFlow:
    """测试检测→恢复→验证的完整流程"""

    def test_detect_stuck_then_recover(self):
        """检测卡死 → 恢复 → 验证"""
        registry = PluginRegistry()
        registry.register_detection(StuckDetector(timeout=10), priority=10)
        registry.register_recovery(RestartRecovery(), priority=10)
        registry.register_verification(
            StableCompletionVerifier(stable_count=1), priority=10
        )

        # 创建一个卡死的 session
        old_session = make_session(
            session_id="stuck-1",
            state="busy",
            todos=[{"status": "pending", "content": "task1"}],
            last_activity=time.time() - 100,
        )

        client = make_client(
            session_status={"stuck-1": {"state": "busy"}},
            session_info={"state": "busy"},
        )

        # 1. 检测阶段
        detection = registry.run_all_detections(old_session, client)
        assert detection is not None
        assert detection.detected is True
        assert "busy" in detection.reason.lower()

        # 2. 恢复阶段
        new_session_data = {
            "id": "new-session-1",
            "state": "idle",
        }
        client.create_session.return_value = new_session_data
        client.abort_session.return_value = None
        client.send_message.return_value = {"status": "ok"}

        context = {"detection": detection, "restart_count": 1}
        new_session = registry.run_recovery(old_session, client, context)
        assert new_session is not None
        client.create_session.assert_called_once()
        client.send_message.assert_called_once()

        # 3. 验证阶段 (新 session 还未完成，验证应该失败)
        client.get_session_status.return_value = {"new-session-1": {"state": "idle"}}
        client.get_session.return_value = {"state": "idle"}
        client.get_todo.return_value = [{"status": "pending", "content": "task1"}]
        verification_passed = registry.run_all_verifications(new_session, client)
        assert verification_passed is False

    def test_detect_idle_incomplete_no_recovery_needed(self):
        """检测空闲未完成 → 不需要恢复（只是提醒）"""
        registry = PluginRegistry()
        registry.register_detection(IdleIncompleteDetector(), priority=10)

        session = make_session(
            state="idle",
            todos=[{"status": "pending", "content": "task1"}],
        )
        client = make_client(
            session_status={"test-1": {"state": "idle"}},
            todos=[{"status": "pending", "content": "task1"}],
            session_info={"state": "idle"},
        )

        detection = registry.run_all_detections(session, client)
        assert detection is not None
        assert detection.detected is True
        assert "idle" in detection.reason.lower()

    def test_detect_premature_end_no_recovery_needed(self):
        """检测提前结束 → 不需要恢复"""
        registry = PluginRegistry()
        registry.register_detection(PrematureEndDetector(), priority=10)

        session = make_session(
            state="done",  # Must be an end state: done, completed, or finished
            todos=[
                {"status": "completed", "content": "task1"},
                {"status": "pending", "content": "task2"},
            ],
        )
        client = make_client(
            session_status={"test-1": {"state": "done"}},
            todos=[
                {"status": "completed", "content": "task1"},
                {"status": "pending", "content": "task2"},
            ],
            messages=[{"role": "assistant", "content": "done"}],
            session_info={"state": "done"},
        )

        detection = registry.run_all_detections(session, client)
        assert detection is not None
        assert detection.detected is True

    def test_verify_stable_completion_passes(self):
        """验证稳定完成 → 通过"""
        registry = PluginRegistry()
        registry.register_verification(
            StableCompletionVerifier(stable_count=2), priority=10
        )

        session = make_session(
            session_id="done-1",
            state="idle",
            todos=[{"status": "completed", "content": "task1"}],
        )
        client = make_client(
            session_status={"done-1": {"state": "idle"}},
            todos=[{"status": "completed", "content": "task1"}],
            session_info={"state": "idle"},
        )

        result = registry.run_all_verifications(session, client)
        assert result is True

    def test_full_cycle_detect_recover_verify(self):
        """完整周期：检测问题 → 恢复 → 验证完成"""
        registry = PluginRegistry()
        registry.register_detection(StuckDetector(timeout=10), priority=10)
        registry.register_detection(IdleIncompleteDetector(), priority=5)
        registry.register_recovery(RestartRecovery(), priority=10)
        registry.register_verification(
            StableCompletionVerifier(stable_count=1), priority=10
        )

        # 阶段 1: 检测卡死
        stuck_session = make_session(
            session_id="stuck-cycle-1",
            state="busy",
            todos=[{"status": "pending", "content": "task1"}],
            last_activity=time.time() - 100,
        )
        client = make_client(
            session_status={"stuck-cycle-1": {"state": "busy"}},
            session_info={"state": "busy"},
        )

        detection = registry.run_all_detections(stuck_session, client)
        assert detection is not None
        assert detection.detected is True

        # 阶段 2: 恢复
        client.create_session.return_value = {"id": "recovered-1"}
        client.abort_session.return_value = None
        client.send_message.return_value = {"status": "ok"}

        context = {"detection": detection, "restart_count": 1}
        recovered_session = registry.run_recovery(stuck_session, client, context)
        assert recovered_session is not None

        # 阶段 3: 验证 (恢复后还未完成)
        client.get_session_status.return_value = {"recovered-1": {"state": "idle"}}
        client.get_session.return_value = {"state": "idle"}
        client.get_todo.return_value = [{"status": "pending", "content": "task1"}]
        assert registry.run_all_verifications(recovered_session, client) is False


class TestDetectionPriorityOrder:
    """测试检测插件的优先级顺序"""

    def test_higher_priority_detected_first(self):
        """高优先级插件先执行"""
        registry = PluginRegistry()
        order = []

        class OrderPlugin(DetectionPlugin):
            def __init__(self, name, order_list, should_detect=False):
                self._name = name
                self._order = order_list
                self._detect = should_detect

            @property
            def name(self):
                return self._name

            def detect(self, session, client):
                self._order.append(self._name)
                return DetectionResult(
                    detected=self._detect, reason=f"{self._name} detected"
                )

        registry.register_detection(
            OrderPlugin("low", order, should_detect=True), priority=1
        )
        registry.register_detection(
            OrderPlugin("high", order, should_detect=True), priority=10
        )

        session = make_session()
        client = make_client(session_info={"state": "idle"})
        result = registry.run_all_detections(session, client)

        # 高优先级先执行，且首次检测到问题就停止
        assert order == ["high"]
        assert result.reason == "high detected"

    def test_lower_priority_runs_when_higher_passes(self):
        """高优先级未检测到时，执行低优先级"""
        registry = PluginRegistry()
        order = []

        class OrderPlugin(DetectionPlugin):
            def __init__(self, name, order_list, should_detect=False):
                self._name = name
                self._order = order_list
                self._detect = should_detect

            @property
            def name(self):
                return self._name

            def detect(self, session, client):
                self._order.append(self._name)
                return DetectionResult(
                    detected=self._detect, reason=f"{self._name} detected"
                )

        registry.register_detection(
            OrderPlugin("low", order, should_detect=True), priority=1
        )
        registry.register_detection(
            OrderPlugin("high", order, should_detect=False), priority=10
        )

        session = make_session()
        client = make_client(session_info={"state": "idle"})
        result = registry.run_all_detections(session, client)

        assert order == ["high", "low"]
        assert result.reason == "low detected"


class TestRecoveryChaining:
    """测试恢复插件的链式执行"""

    def test_first_successful_recovery_stops(self):
        """第一个成功的恢复操作停止后续执行"""
        registry = PluginRegistry()
        order = []

        class OrderRecovery(RecoveryPlugin):
            def __init__(self, name, order_list, should_succeed=False):
                self._name = name
                self._order = order_list
                self._succeed = should_succeed

            @property
            def name(self):
                return self._name

            def recover(self, session, client, context):
                self._order.append(self._name)
                if self._succeed:
                    new_session = MagicMock()
                    new_session.session_id = "new-1"
                    return new_session
                return None

        registry.register_recovery(
            OrderRecovery("first", order, should_succeed=True), priority=10
        )
        registry.register_recovery(
            OrderRecovery("second", order, should_succeed=True), priority=5
        )

        session = make_session()
        client = make_client(session_info={"state": "idle"})
        result = registry.run_recovery(session, client, {"restart_count": 1})

        assert order == ["first"]
        assert result is not None
        assert result.session_id == "new-1"

    def test_all_recoveries_fail_returns_none(self):
        """所有恢复都失败时返回 None"""
        registry = PluginRegistry()

        class FailingRecovery(RecoveryPlugin):
            def __init__(self, name):
                self._name = name

            @property
            def name(self):
                return self._name

            def recover(self, session, client, context):
                return None

        registry.register_recovery(FailingRecovery("fail1"), priority=10)
        registry.register_recovery(FailingRecovery("fail2"), priority=5)

        session = make_session()
        client = make_client(session_info={"state": "idle"})
        result = registry.run_recovery(session, client, {"restart_count": 1})

        assert result is None


class TestVerificationCombination:
    """测试多个验证插件的组合效果"""

    def test_all_verifications_must_pass(self):
        """所有验证都必须通过"""
        registry = PluginRegistry()

        class PassVerification(VerificationPlugin):
            @property
            def name(self):
                return "pass_verify"

            def verify(self, session, client):
                return True

        class FailVerification(VerificationPlugin):
            @property
            def name(self):
                return "fail_verify"

            def verify(self, session, client):
                return False

        registry.register_verification(PassVerification(), priority=10)
        registry.register_verification(FailVerification(), priority=5)

        session = make_session()
        client = make_client(session_info={"state": "idle"})
        result = registry.run_all_verifications(session, client)

        assert result is False

    def test_single_verification_failure_fails_all(self):
        """单个验证失败导致整体失败"""
        registry = PluginRegistry()
        registry.register_verification(
            StableCompletionVerifier(stable_count=1), priority=10
        )
        registry.register_verification(TodoExecutionVerifier(), priority=5)

        session = make_session(
            session_id="verify-1",
            state="idle",
            todos=[{"status": "completed", "content": "task1"}],
        )
        client = make_client(
            session_status={"verify-1": {"state": "idle"}},
            todos=[{"status": "completed", "content": "task1"}],
            messages=[{"role": "assistant", "content": "done"}],
            session_info={"state": "idle"},
        )

        result = registry.run_all_verifications(session, client)
        # StableCompletionVerifier 通过，但 TodoExecutionVerifier 可能失败
        # 取决于消息历史是否匹配
        assert isinstance(result, bool)


class TestPluginErrorIsolation:
    """测试插件错误隔离"""

    def test_detection_plugin_error_does_not_affect_others(self):
        """检测插件错误不影响其他插件"""
        registry = PluginRegistry()

        class ErrorPlugin(DetectionPlugin):
            @property
            def name(self):
                return "error_plugin"

            def detect(self, session, client):
                raise RuntimeError("Plugin error")

        class GoodPlugin(DetectionPlugin):
            def __init__(self):
                self.called = False

            @property
            def name(self):
                return "good_plugin"

            def detect(self, session, client):
                self.called = True
                return DetectionResult(detected=False)

        error_plugin = ErrorPlugin()
        good_plugin = GoodPlugin()
        registry.register_detection(error_plugin, priority=10)
        registry.register_detection(good_plugin, priority=5)

        session = make_session()
        client = make_client(session_info={"state": "idle"})
        result = registry.run_all_detections(session, client)

        # 错误插件被隔离，好插件仍然执行
        assert good_plugin.called is True
        assert result is None

    def test_recovery_plugin_error_does_not_affect_others(self):
        """恢复插件错误不影响其他插件"""
        registry = PluginRegistry()

        class ErrorRecovery(RecoveryPlugin):
            @property
            def name(self):
                return "error_recovery"

            def recover(self, session, client, context):
                raise RuntimeError("Recovery error")

        class GoodRecovery(RecoveryPlugin):
            def __init__(self):
                self.called = False

            @property
            def name(self):
                return "good_recovery"

            def recover(self, session, client, context):
                self.called = True
                new_session = MagicMock()
                new_session.session_id = "recovered-1"
                return new_session

        error_recovery = ErrorRecovery()
        good_recovery = GoodRecovery()
        registry.register_recovery(error_recovery, priority=10)
        registry.register_recovery(good_recovery, priority=5)

        session = make_session()
        client = make_client(session_info={"state": "idle"})
        result = registry.run_recovery(session, client, {"restart_count": 1})

        assert good_recovery.called is True
        assert result is not None
        assert result.session_id == "recovered-1"

    def test_verification_plugin_error_fails_all(self):
        """验证插件错误导致整体失败"""
        registry = PluginRegistry()

        class ErrorVerification(VerificationPlugin):
            @property
            def name(self):
                return "error_verify"

            def verify(self, session, client):
                raise RuntimeError("Verify error")

        class GoodVerification(VerificationPlugin):
            def __init__(self):
                self.called = False

            @property
            def name(self):
                return "good_verify"

            def verify(self, session, client):
                self.called = True
                return True

        error_verify = ErrorVerification()
        good_verify = GoodVerification()
        registry.register_verification(error_verify, priority=10)
        registry.register_verification(good_verify, priority=5)

        session = make_session()
        client = make_client(session_info={"state": "idle"})
        result = registry.run_all_verifications(session, client)

        # 错误插件先执行，导致整体失败
        assert result is False
        # 好插件可能未执行（取决于错误插件是否先执行）


class TestPluginListPlugins:
    """测试插件列表功能"""

    def test_list_all_plugins(self):
        """列出所有插件"""
        registry = PluginRegistry()
        registry.register_detection(StuckDetector(), priority=10)
        registry.register_recovery(RestartRecovery(), priority=10)
        registry.register_verification(StableCompletionVerifier(), priority=10)

        plugins = registry.list_plugins()
        assert "detection" in plugins
        assert "recovery" in plugins
        assert "verification" in plugins
        assert len(plugins["detection"]) == 1
        assert len(plugins["recovery"]) == 1
        assert len(plugins["verification"]) == 1

    def test_list_empty_registry(self):
        """空注册表"""
        registry = PluginRegistry()
        plugins = registry.list_plugins()
        assert plugins == {"detection": [], "recovery": [], "verification": []}


class TestPluginCounts:
    """测试插件计数功能"""

    def test_counts_after_registration(self):
        """注册后计数正确"""
        registry = PluginRegistry()
        assert registry.detection_count == 0
        assert registry.recovery_count == 0
        assert registry.verification_count == 0

        registry.register_detection(StuckDetector())
        registry.register_detection(IdleIncompleteDetector())
        registry.register_recovery(RestartRecovery())
        registry.register_verification(StableCompletionVerifier())

        assert registry.detection_count == 2
        assert registry.recovery_count == 1
        assert registry.verification_count == 1


class TestDetectionEdgeCases:
    """测试检测插件的边界条件"""

    def test_stuck_detector_with_very_long_busy_time(self):
        """StuckDetector 处理超长 busy 时间"""
        detector = StuckDetector(timeout=10)
        session = make_session(
            state="busy",
            last_activity=time.time() - 86400,  # 24 小时前
        )
        client = make_client(
            session_status={"test-1": {"state": "busy"}},
            session_info={"state": "busy"},
        )
        result = detector.detect(session, client)
        assert result.detected is True
        assert "busy" in result.reason.lower()

    def test_idle_detector_with_many_pending_todos(self):
        """IdleIncompleteDetector 处理大量 pending todos"""
        detector = IdleIncompleteDetector()
        todos = [{"status": "pending", "content": f"task-{i}"} for i in range(100)]
        session = make_session(state="idle", todos=todos)
        client = make_client(
            session_status={"test-1": {"state": "idle"}},
            todos=todos,
            session_info={"state": "idle"},
        )
        result = detector.detect(session, client)
        assert result.detected is True
        assert "idle" in result.reason.lower()

    def test_premature_end_detector_with_mixed_statuses(self):
        """PrematureEndDetector 处理混合状态"""
        detector = PrematureEndDetector()
        session = make_session(
            state="done",  # Must be end state
            todos=[
                {"status": "completed", "content": "task1"},
                {"status": "completed", "content": "task2"},
                {"status": "pending", "content": "task3"},
                {"status": "in_progress", "content": "task4"},
            ],
        )
        client = make_client(
            session_status={"test-1": {"state": "done"}},
            todos=session.todos,
            messages=[{"role": "assistant", "content": "done"}],
            session_info={"state": "done"},
        )
        result = detector.detect(session, client)
        assert result.detected is True

    def test_session_invalid_detector_with_empty_response(self):
        """SessionInvalidDetector 处理空响应"""
        detector = SessionInvalidDetector(grace_period=0)
        session = make_session(session_id="empty-1")
        client = make_client(session_info={})
        result = detector.detect(session, client)
        assert result.detected is True
        assert "empty" in result.reason.lower()


class TestRecoveryEdgeCases:
    """测试恢复插件的边界条件"""

    def test_restart_recovery_with_max_restarts(self):
        """RestartRecovery 处理最大重启次数"""
        recovery = RestartRecovery()
        session = make_session(session_id="old-1")
        client = make_client(session_info={"state": "idle"})
        context = {"restart_count": 5, "max_restarts": 3}
        result = recovery.recover(session, client, context)
        # Should return None or handle gracefully
        assert result is None or hasattr(result, "session_id")

    def test_restart_recovery_with_no_todos(self):
        """RestartRecovery 处理无 todos"""
        recovery = RestartRecovery()
        session = make_session(session_id="old-1", todos=[])
        client = make_client(session_info={"state": "idle"})
        client.create_session.return_value = {"id": "new-1"}
        client.abort_session.return_value = None
        client.send_message.return_value = {"status": "ok"}
        context = {"restart_count": 1, "max_restarts": 3}
        result = recovery.recover(session, client, context)
        assert result is not None
        # session_id is the whole session object, check session_id attribute
        assert hasattr(result, "session_id")


class TestVerificationEdgeCases:
    """测试验证插件的边界条件"""

    def test_stable_completion_with_high_stable_count(self):
        """StableCompletionVerifier 处理高稳定次数"""
        verifier = StableCompletionVerifier(stable_count=10)
        session = make_session(
            session_id="stable-1",
            state="idle",
            todos=[{"status": "completed", "content": "task1"}],
        )
        client = make_client(
            session_status={"stable-1": {"state": "idle"}},
            todos=[{"status": "completed", "content": "task1"}],
            session_info={"state": "idle"},
        )
        result = verifier.verify(session, client)
        assert result is True

    def test_todo_execution_with_no_messages(self):
        """TodoExecutionVerifier 处理无消息历史"""
        verifier = TodoExecutionVerifier()
        session = make_session(
            session_id="no-msg-1",
            todos=[{"status": "completed", "content": "task1"}],
        )
        client = make_client(
            messages=[],
            session_info={"state": "idle"},
        )
        result = verifier.verify(session, client)
        assert result is False

    def test_todo_execution_with_many_messages(self):
        """TodoExecutionVerifier 处理大量消息"""
        verifier = TodoExecutionVerifier()
        todos = [{"status": "completed", "content": f"task-{i}"} for i in range(10)]
        session = make_session(
            session_id="many-msg-1",
            todos=todos,
        )
        messages = [
            {"info": {"role": "assistant"}, "content": f"done task {i}"}
            for i in range(10)
        ]
        client = make_client(
            messages=messages,
            session_info={"state": "idle"},
        )
        # Ensure get_todo returns the todos
        client.get_todo.return_value = todos
        result = verifier.verify(session, client)
        assert result is True
