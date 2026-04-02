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
cd multi-agent-orchestrator
pip install -r requirements.txt
```

## 配置

1. 编辑 `opencode.json`，设置你的模型和 API key
2. 编辑 `prompts/planner.txt` 和 `prompts/reviewer.txt` 自定义 agent 行为
3. Plugin 已配置在 `opencode.json` 的 `plugin` 字段中，启动时自动加载

## 使用

### 运行完整工作流

```bash
python main.py "实现一个用户注册功能，包括前端表单和后端 API"
```

### 运行 Skill Runner（插件化模式）

```python
from src.opencode_client import OpenCodeClient
from src.skill_runner import SkillRunner

# 1. 连接 opencode serve
client = OpenCodeClient("http://localhost:4096")

# 2. 创建 SkillRunner
runner = SkillRunner(
    client=client,
    agent_name="skill-executor",
    max_restarts=3,           # 最大重启次数
    stuck_threshold=120,      # 卡死检测阈值（秒）
    max_execution_time=600,   # 最大执行时间（秒）
    poll_interval=5,          # 轮询间隔（秒）
    verification_stable_count=2,  # 稳定完成验证次数
)

# 3. 执行 skill
skill_content = """---
name: my-skill
description: "我的 skill 描述"
---

# Skill 内容

## 步骤
1. 使用 todowrite 创建任务
2. 逐个执行任务
3. 汇报结果
"""

result = runner.run(
    skill_content=skill_content,
    user_request="执行我的 skill",
    skill_name="my-skill",
)

print(f"状态: {result['status']}")
print(f"进度: {result['progress']}")
print(f"Todos: {result['todos']}")
```

### 启动 opencode serve

```bash
# 启动 headless server
opencode serve --port 4096 --hostname localhost

# 或使用 web 界面
opencode web --port 4096
```

## 测试

### 运行全部测试

```bash
# 全部测试（146 个，约 71 秒）
pytest tests/ -v

# 只跑单元测试（不需要 opencode server）
pytest tests/test_opencode_client.py tests/test_agent_session.py tests/test_skill_runner.py tests/plugins/ -v

# 只跑插件测试
pytest tests/plugins/ -v

# 带日志输出
pytest tests/plugins/ -v -s

# 带超时控制
pytest tests/ -v --timeout=600
```

### 测试覆盖

| 测试类型 | 数量 | 状态 |
|----------|------|------|
| 插件单元测试 | 56 | ✅ 全部通过 |
| 插件集成测试 | 40 | ✅ 全部通过 |
| 客户端/会话/运行器单元测试 | 50 | ✅ 全部通过 |
| **总计** | **146** | **✅ 全部通过** |

### 测试日志

测试日志保存在 `test_logs/` 目录：
- `integration_YYYYMMDD_HHMMSS.log` - 集成测试日志
- `e2e_YYYYMMDD_HHMMSS.log` - E2E 测试日志
- `pytest.log` - pytest 统一日志

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
│   ├── test_opencode_client.py      # 客户端测试 (17)
│   ├── test_agent_session.py        # 会话测试 (20)
│   ├── test_skill_runner.py         # 运行器测试 (12)
│   ├── test_skill_integration.py    # 集成测试 (5)
│   ├── test_skill_e2e.py            # E2E 测试 (4)
│   └── plugins/                     # 插件测试 (88)
│       ├── test_plugin_framework.py #   框架测试 (12)
│       ├── test_detectors.py        #   检测插件测试 (22)
│       ├── test_recovery.py         #   恢复插件测试 (8)
│       ├── test_verification.py     #   验证插件测试 (13)
│       ├── test_plugin_integration.py          # 插件集成测试 (40)
│       └── test_skill_runner_plugin_integration.py  # SkillRunner 插件集成测试 (16)
├── config.py                  # 配置项
├── main.py                    # 入口
├── pytest.ini                 # pytest 配置
├── requirements.txt           # 依赖
└── test_logs/                 # 测试日志目录
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

### 创建自定义恢复插件

```python
from src.plugins import RecoveryPlugin

class MyRecovery(RecoveryPlugin):
    @property
    def name(self) -> str:
        return "my_recovery"

    def recover(self, session, client, context):
        # 恢复逻辑
        # 返回新的 session 或 None
        new_session = client.create_session("recovery-session")
        client.send_message(new_session["id"], "继续执行")
        return new_session
```

### 创建自定义验证插件

```python
from src.plugins import VerificationPlugin

class MyVerification(VerificationPlugin):
    @property
    def name(self) -> str:
        return "my_verification"

    def verify(self, session, client) -> bool:
        # 验证逻辑
        todos = client.get_todo(session.session_id)
        return all(t.get("status") == "completed" for t in todos)
```

### 注册插件

```python
from src.skill_runner import SkillRunner

runner = SkillRunner(client=client)

# 注册检测插件（priority 越大越先执行）
runner.registry.register_detection(MyDetector(), priority=100)

# 注册恢复插件
runner.registry.register_recovery(MyRecovery(), priority=50)

# 注册验证插件
runner.registry.register_verification(MyVerification(), priority=10)
```

## 配置参数说明

### SkillRunner 参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `client` | - | OpenCodeClient 实例 |
| `agent_name` | - | Agent 名称 |
| `max_restarts` | 3 | 最大重启次数 |
| `stuck_threshold` | 120 | 卡死检测阈值（秒） |
| `max_execution_time` | 600 | 最大执行时间（秒） |
| `poll_interval` | 5 | 轮询间隔（秒） |
| `verification_stable_count` | 2 | 稳定完成验证次数 |

### pytest.ini 配置

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `timeout` | 600 | 测试超时时间（秒） |
| `log_cli` | true | 控制台实时日志 |
| `log_cli_level` | INFO | 控制台日志级别 |
| `log_file` | test_logs/pytest.log | 日志文件路径 |
| `log_file_level` | DEBUG | 文件日志级别 |

## 常见问题

### Q: 测试失败怎么办？

A: 检查以下几点：
1. 确保 `opencode serve` 正在运行（集成/E2E 测试需要）
2. 检查日志文件 `test_logs/` 中的详细输出
3. 只跑单元测试：`pytest tests/test_opencode_client.py tests/test_agent_session.py tests/plugins/ -v`

### Q: 如何添加自定义检测逻辑？

A: 创建自定义检测插件并注册到 SkillRunner：
```python
runner.registry.register_detection(MyDetector(), priority=100)
```

### Q: 如何调整超时时间？

A: 修改 SkillRunner 初始化参数：
```python
runner = SkillRunner(
    client=client,
    max_execution_time=300,  # 改为 5 分钟
    stuck_threshold=60,      # 改为 1 分钟
)
```
