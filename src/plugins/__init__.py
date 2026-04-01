"""
Plugin Framework for Skill Orchestrator

插件化架构：编排器通过插件机制扩展检测和恢复能力，
而不是硬编码所有逻辑。

三类插件：
1. DetectionPlugin - 检测问题（卡死、偷懒、提前结束等）
2. RecoveryPlugin - 恢复问题（重启 session、发送继续消息等）
3. VerificationPlugin - 验证完成状态（稳定完成、真正执行等）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    """检测结果"""

    detected: bool
    reason: str = ""
    severity: str = "low"  # low, medium, high, critical
    details: Dict[str, Any] = field(default_factory=dict)


class DetectionPlugin(ABC):
    """检测插件基类

    每个检测插件负责检测一种特定的问题场景。
    """

    @abstractmethod
    def detect(self, session, client) -> DetectionResult:
        """检测 session 是否存在问题

        Args:
            session: AgentSession 实例
            client: OpenCodeClient 实例

        Returns:
            DetectionResult: 检测结果
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称"""
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"


class RecoveryPlugin(ABC):
    """恢复插件基类

    每个恢复插件负责一种特定的恢复策略。
    """

    @abstractmethod
    def recover(self, session, client, context: Dict[str, Any]) -> Any:
        """执行恢复操作

        Args:
            session: 当前 AgentSession 实例
            client: OpenCodeClient 实例
            context: 恢复上下文（包含检测结果、重启次数等）

        Returns:
            新的 AgentSession 实例或 None（如果无法恢复）
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称"""
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"


class VerificationPlugin(ABC):
    """验证插件基类

    每个验证插件负责一种特定的完成验证。
    """

    @abstractmethod
    def verify(self, session, client) -> bool:
        """验证 session 是否真正完成

        Args:
            session: AgentSession 实例
            client: OpenCodeClient 实例

        Returns:
            bool: 验证是否通过
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """插件名称"""
        pass

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"


class PluginRegistry:
    """插件注册表

    管理所有插件的注册、注销和执行。
    支持按类型分组，支持执行顺序控制。
    """

    def __init__(self):
        self._detection_plugins: List[DetectionPlugin] = []
        self._recovery_plugins: List[RecoveryPlugin] = []
        self._verification_plugins: List[VerificationPlugin] = []

    def register_detection(self, plugin: DetectionPlugin, priority: int = 0):
        """注册检测插件

        Args:
            plugin: 检测插件实例
            priority: 优先级（数字越大越先执行）
        """
        self._detection_plugins.append((priority, plugin))
        self._detection_plugins.sort(key=lambda x: x[0], reverse=True)
        logger.info(f"Registered detection plugin: {plugin.name}")

    def register_recovery(self, plugin: RecoveryPlugin, priority: int = 0):
        """注册恢复插件

        Args:
            plugin: 恢复插件实例
            priority: 优先级（数字越大越先执行）
        """
        self._recovery_plugins.append((priority, plugin))
        self._recovery_plugins.sort(key=lambda x: x[0], reverse=True)
        logger.info(f"Registered recovery plugin: {plugin.name}")

    def register_verification(self, plugin: VerificationPlugin, priority: int = 0):
        """注册验证插件

        Args:
            plugin: 验证插件实例
            priority: 优先级（数字越大越先执行）
        """
        self._verification_plugins.append((priority, plugin))
        self._verification_plugins.sort(key=lambda x: x[0], reverse=True)
        logger.info(f"Registered verification plugin: {plugin.name}")

    def run_all_detections(self, session, client) -> Optional[DetectionResult]:
        """运行所有检测插件

        按优先级顺序执行，一旦有插件检测到问题，立即返回。

        Returns:
            第一个检测到的问题，如果没有问题则返回 None
        """
        for _, plugin in self._detection_plugins:
            try:
                result = plugin.detect(session, client)
                if result.detected:
                    logger.warning(
                        f"Detection plugin {plugin.name} detected issue: "
                        f"{result.reason} (severity: {result.severity})"
                    )
                    return result
            except Exception as e:
                logger.error(f"Detection plugin {plugin.name} error: {e}")
        return None

    def run_recovery(self, session, client, context: Dict[str, Any]) -> Optional[Any]:
        """运行恢复插件

        按优先级顺序执行，一旦有插件成功恢复，立即返回。

        Returns:
            恢复后的 session，如果所有插件都失败则返回 None
        """
        for _, plugin in self._recovery_plugins:
            try:
                result = plugin.recover(session, client, context)
                if result is not None:
                    logger.info(f"Recovery plugin {plugin.name} succeeded")
                    return result
            except Exception as e:
                logger.error(f"Recovery plugin {plugin.name} error: {e}")
        return None

    def run_all_verifications(self, session, client) -> bool:
        """运行所有验证插件

        所有验证插件都必须通过才算真正完成。

        Returns:
            bool: 所有验证是否通过
        """
        for _, plugin in self._verification_plugins:
            try:
                if not plugin.verify(session, client):
                    logger.warning(f"Verification plugin {plugin.name} failed")
                    return False
            except Exception as e:
                logger.error(f"Verification plugin {plugin.name} error: {e}")
                return False
        return True

    @property
    def detection_count(self) -> int:
        return len(self._detection_plugins)

    @property
    def recovery_count(self) -> int:
        return len(self._recovery_plugins)

    @property
    def verification_count(self) -> int:
        return len(self._verification_plugins)

    def list_plugins(self) -> Dict[str, List[str]]:
        """列出所有已注册的插件"""
        return {
            "detection": [p.name for _, p in self._detection_plugins],
            "recovery": [p.name for _, p in self._recovery_plugins],
            "verification": [p.name for _, p in self._verification_plugins],
        }
