"""
Tests for the Plugin Framework

验证：
1. PluginRegistry 注册/注销/执行
2. 插件执行顺序
3. 插件异常隔离
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.plugins import (
    PluginRegistry,
    DetectionPlugin,
    DetectionResult,
    RecoveryPlugin,
    VerificationPlugin,
)


class MockDetectionPlugin(DetectionPlugin):
    def __init__(self, name="mock", should_detect=False):
        self._name = name
        self._should_detect = should_detect
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def detect(self, session, client) -> DetectionResult:
        self.call_count += 1
        return DetectionResult(
            detected=self._should_detect,
            reason=f"{self._name} detected" if self._should_detect else "",
        )


class MockRecoveryPlugin(RecoveryPlugin):
    def __init__(self, name="mock", should_succeed=True):
        self._name = name
        self._should_succeed = should_succeed
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def recover(self, session, client, context):
        self.call_count += 1
        if self._should_succeed:
            return MagicMock()
        return None


class MockVerificationPlugin(VerificationPlugin):
    def __init__(self, name="mock", should_pass=True):
        self._name = name
        self._should_pass = should_pass
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def verify(self, session, client) -> bool:
        self.call_count += 1
        return self._should_pass


class TestPluginRegistry:
    def test_register_detection(self):
        registry = PluginRegistry()
        plugin = MockDetectionPlugin(name="test_detect")
        registry.register_detection(plugin)
        assert registry.detection_count == 1
        assert registry.list_plugins()["detection"] == ["test_detect"]

    def test_register_recovery(self):
        registry = PluginRegistry()
        plugin = MockRecoveryPlugin(name="test_recovery")
        registry.register_recovery(plugin)
        assert registry.recovery_count == 1
        assert registry.list_plugins()["recovery"] == ["test_recovery"]

    def test_register_verification(self):
        registry = PluginRegistry()
        plugin = MockVerificationPlugin(name="test_verify")
        registry.register_verification(plugin)
        assert registry.verification_count == 1
        assert registry.list_plugins()["verification"] == ["test_verify"]

    def test_detection_priority_order(self):
        registry = PluginRegistry()
        p1 = MockDetectionPlugin(name="low", should_detect=False)
        p2 = MockDetectionPlugin(name="high", should_detect=False)
        registry.register_detection(p1, priority=10)
        registry.register_detection(p2, priority=100)
        registry.run_all_detections(MagicMock(), MagicMock())
        assert p2.call_count == 1
        assert p1.call_count == 1
        names = [p.name for _, p in registry._detection_plugins]
        assert names == ["high", "low"]

    def test_detection_stops_on_first_match(self):
        registry = PluginRegistry()
        p1 = MockDetectionPlugin(name="first", should_detect=True)
        p2 = MockDetectionPlugin(name="second", should_detect=True)
        registry.register_detection(p1, priority=100)
        registry.register_detection(p2, priority=50)

        result = registry.run_all_detections(MagicMock(), MagicMock())
        assert result.detected is True
        assert result.reason == "first detected"
        assert p1.call_count == 1
        assert p2.call_count == 0

    def test_detection_returns_none_when_no_issues(self):
        registry = PluginRegistry()
        registry.register_detection(MockDetectionPlugin(should_detect=False))
        result = registry.run_all_detections(MagicMock(), MagicMock())
        assert result is None

    def test_detection_plugin_error_isolated(self):
        registry = PluginRegistry()

        class ErrorPlugin(DetectionPlugin):
            @property
            def name(self):
                return "error_plugin"

            def detect(self, session, client):
                raise RuntimeError("Plugin error")

        p2 = MockDetectionPlugin(name="good_plugin", should_detect=False)
        registry.register_detection(ErrorPlugin(), priority=100)
        registry.register_detection(p2, priority=50)

        result = registry.run_all_detections(MagicMock(), MagicMock())
        assert result is None
        assert p2.call_count == 1

    def test_recovery_stops_on_first_success(self):
        registry = PluginRegistry()
        p1 = MockRecoveryPlugin(name="first", should_succeed=True)
        p2 = MockRecoveryPlugin(name="second", should_succeed=True)
        registry.register_recovery(p1, priority=100)
        registry.register_recovery(p2, priority=50)

        result = registry.run_recovery(MagicMock(), MagicMock(), {})
        assert result is not None
        assert p1.call_count == 1
        assert p2.call_count == 0

    def test_recovery_returns_none_when_all_fail(self):
        registry = PluginRegistry()
        registry.register_recovery(MockRecoveryPlugin(should_succeed=False))
        result = registry.run_recovery(MagicMock(), MagicMock(), {})
        assert result is None

    def test_recovery_plugin_error_isolated(self):
        registry = PluginRegistry()

        class ErrorPlugin(RecoveryPlugin):
            @property
            def name(self):
                return "error_recovery"

            def recover(self, session, client, context):
                raise RuntimeError("Recovery error")

        p2 = MockRecoveryPlugin(name="good_recovery", should_succeed=True)
        registry.register_recovery(ErrorPlugin(), priority=100)
        registry.register_recovery(p2, priority=50)

        result = registry.run_recovery(MagicMock(), MagicMock(), {})
        assert result is not None
        assert p2.call_count == 1

    def test_verification_all_must_pass(self):
        registry = PluginRegistry()
        registry.register_verification(MockVerificationPlugin(should_pass=True))
        registry.register_verification(MockVerificationPlugin(should_pass=True))
        assert registry.run_all_verifications(MagicMock(), MagicMock()) is True

    def test_verification_fails_if_any_fails(self):
        registry = PluginRegistry()
        registry.register_verification(MockVerificationPlugin(should_pass=True))
        registry.register_verification(MockVerificationPlugin(should_pass=False))
        assert registry.run_all_verifications(MagicMock(), MagicMock()) is False

    def test_verification_plugin_error_isolated(self):
        registry = PluginRegistry()

        class ErrorPlugin(VerificationPlugin):
            @property
            def name(self):
                return "error_verify"

            def verify(self, session, client):
                raise RuntimeError("Verify error")

        registry.register_detection = MagicMock()
        registry.register_verification(ErrorPlugin())
        assert registry.run_all_verifications(MagicMock(), MagicMock()) is False

    def test_list_plugins(self):
        registry = PluginRegistry()
        registry.register_detection(MockDetectionPlugin(name="d1"))
        registry.register_recovery(MockRecoveryPlugin(name="r1"))
        registry.register_verification(MockVerificationPlugin(name="v1"))

        plugins = registry.list_plugins()
        assert plugins["detection"] == ["d1"]
        assert plugins["recovery"] == ["r1"]
        assert plugins["verification"] == ["v1"]
