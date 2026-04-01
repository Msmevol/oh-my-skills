# Multi-Agent Orchestrator for OpenCode

使用 Python 编排器管理多个 OpenCode agent session，实现**真正的并行 + 自动容错 + 监督拉起**。

## 架构

### 双方案架构

本系统采用**双方案**解决小模型会话提前停止问题：

```
┌──────────────────────────────────────────────────────────────┐
│                    方案 A: OpenCode Plugin                    │
│              (TypeScript, 实时守护在 server 内部)              │
│                                                              │
│  session-guard.ts  → 监听 session.idle 事件                  │
│                      自动检测 todos 是否完成                   │
│                      未完成 → 自动发送继续消息                 │
│                                                              │
│  todo-validator.ts → 拦截 todowrite 调用                     │
│                      防止批量作弊标记 completed                │
│                      验证 in_progress 状态流转                 │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                   方案 B: Python 插件化编排器                  │
│              (Python, 外部调度 + 插件化检测)                   │
│                                                              │
│  PluginRegistry → 检测插件 / 恢复插件 / 验证插件              │
│                                                              │
│  检测插件:                                                    │
│  ├── StuckDetector          (busy 超时, error 状态)           │
│  ├── IdleIncompleteDetector (idle 但 todos 未完成)            │
│  ├── PrematureEndDetector   (done/completed 但 todos 未完成)  │
│  └── SessionInvalidDetector (session 不存在, 连接断开)         │
│                                                              │
│  恢复插件:                                                     │
│  └── RestartRecovery        (重启 + 优化继续消息)              │
│                                                              │
│  验证插件:                                                     │
│  ├── StableCompletionVerifier (稳定完成验证)                   │
│  └── TodoExecutionVerifier    (真正执行验证)                   │
└──────────────────────────────────────────────────────────────┘
```

### 工作流程

```
┌─────────────────────────────────────────────────┐
│              Python Orchestrator                 │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Skill    │  │ Skill    │  │  Plugin       │  │
│  │ Runner   │  │ Runner   │  │  Registry     │  │
│  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
│       │              │               │           │
│       ▼              ▼               ▼           │
│  ┌──────────────────────────────────────────┐   │
│  │  opencode serve + Plugin (session-guard) │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

## 核心能力

| 能力 | 说明 |
|------|------|
| 卡死检测 | 检测 idle 但有未完成 todo 的 session，检测 busy 超时的 session |
| 提前结束检测 | **新增** 检测 session done/completed 但 todos 未完成（小模型最常见问） |
| 会话失效检测 | **新增** 检测 session 不存在、连接断开、被 abort |
| 自动拉起 | abort 卡住的 session → 创建新 session → 发送继续指令 |
| 实时守护 | **新增** OpenCode Plugin 实时监听 session.idle 事件，自动恢复 |
| TODO 验证 | **新增** 拦截 todowrite 调用，防止模型作弊批量标记完成 |
| 插件化架构 | **新增** 检测/恢复/验证三类插件，可扩展自定义策略 |
| 审核循环 | reviewer 驳回 → planner 修改 → 重新审核 |
| 进度监控 | 定期轮询 todolist 进度，生成进度报告 |
| 故障恢复 | server 崩溃、网络断开、异常响应都能优雅处理 |

## 安装

```bash
pip install -r requirements.txt
```

## 配置

1. 编辑 `opencode.json`，设置你的模型和 API key
2. 编辑 `prompts/planner.txt` 和 `prompts/reviewer.txt` 自定义 agent 行为
3. Plugin 已配置在 `opencode.json` 的 `plugin` 字段中，启动时自动加载

## 使用

```bash
# 运行完整工作流
python main.py "实现一个用户注册功能，包括前端表单和后端 API"

# 或直接运行单元测试
pytest tests/ -v

# 只跑快速测试（不需要 opencode）
pytest tests/ -v -m "not slow"
```

## 项目结构

```
├── opencode.json              # agent 配置 + plugin 注册
├── plugins/                   # OpenCode Plugin（方案 A）
│   ├── session-guard.ts       #   会话守卫：实时检测 + 自动恢复
│   └── todo-validator.ts      #   TODO 验证器：防止作弊
├── prompts/
│   ├── planner.txt            # planner system prompt
│   └── reviewer.txt           # reviewer system prompt
├── src/
│   ├── opencode_client.py     # HTTP API 封装
│   ├── agent_session.py       # Session 生命周期管理
│   ├── orchestrator.py        # 主编排器
│   ├── skill_runner.py        # Skill 执行器（插件化架构）
│   └── plugins/               # Python 插件框架（方案 B）
│       ├── __init__.py        #   PluginRegistry, 基类定义
│       ├── builtin_detectors.py    #   内置检测插件
│       ├── builtin_recovery.py     #   内置恢复插件
│       └── builtin_verification.py #   内置验证插件
├── tests/
│   ├── test_opencode_client.py
│   ├── test_agent_session.py
│   ├── test_skill_runner.py
│   ├── test_skill_integration.py
│   ├── test_skill_e2e.py
│   └── plugins/               # 插件测试
│       ├── test_plugin_framework.py
│       ├── test_detectors.py
│       ├── test_recovery.py
│       └── test_verification.py
├── config.py                  # 配置项
├── main.py                    # 入口
└── requirements.txt
```

## 测试

```bash
# 全部测试
pytest tests/ -v

# 只跑单元测试（不需要 opencode）
pytest tests/test_opencode_client.py tests/test_agent_session.py tests/plugins/ -v

# 插件框架测试
pytest tests/plugins/ -v

# 集成测试（需要 mock server）
pytest tests/test_integration.py -v

# E2E 测试（标记为 slow）
pytest tests/test_e2e.py -v -m slow
```

## 插件开发指南

### 创建自定义检测插件

```python
from src.plugins import DetectionPlugin, DetectionResult

class MyDetector(DetectionPlugin):
    @property
    def name(self) -> str:
        return "my_detector"

    def detect(self, session, client) -> DetectionResult:
        # 检测逻辑
        if problem_detected:
            return DetectionResult(
                detected=True,
                reason="描述问题",
                severity="high",
            )
        return DetectionResult(detected=False)
```

### 注册插件

```python
from src.skill_runner import SkillRunner

runner = SkillRunner(client=client)
runner.registry.register_detection(MyDetector(), priority=100)
```
