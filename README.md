# Multi-Agent Orchestrator for OpenCode

使用 Python 编排器管理多个 OpenCode agent session，实现**真正的并行 + 自动容错 + 监督拉起**。

## 架构

```
┌─────────────────────────────────────────────────┐
│              Python Orchestrator                 │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Planner  │  │ Reviewer │  │  Supervisor   │  │
│  │ Session  │  │ Session  │  │   (Watchdog)  │  │
│  └────┬─────┘  └────┬─────┘  └───────┬───────┘  │
│       │              │               │           │
│       ▼              ▼               ▼           │
│  ┌──────────────────────────────────────────┐   │
│  │        opencode serve (HTTP Server)      │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

## 核心能力

| 能力 | 说明 |
|------|------|
| 卡死检测 | 检测 idle 但有未完成 todo 的 session，检测 busy 超时的 session |
| 自动拉起 | abort 卡住的 session → 创建新 session → 发送继续指令 |
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
├── opencode.json           # agent 配置
├── prompts/
│   ├── planner.txt         # planner system prompt
│   └── reviewer.txt        # reviewer system prompt
├── src/
│   ├── opencode_client.py  # HTTP API 封装
│   ├── agent_session.py    # Session 生命周期管理
│   ├── supervisor.py       # 看门狗监控器
│   └── orchestrator.py     # 主编排器
├── tests/
│   ├── mock_server.py      # Mock opencode server
│   ├── test_opencode_client.py
│   ├── test_agent_session.py
│   ├── test_supervisor.py
│   ├── test_integration.py
│   ├── test_e2e.py
│   └── test_fault_injection.py
├── config.py               # 配置项
├── main.py                 # 入口
└── requirements.txt
```

## 测试

```bash
# 全部测试
pytest tests/ -v

# 只跑单元测试（不需要 opencode）
pytest tests/test_opencode_client.py tests/test_agent_session.py tests/test_supervisor.py -v

# 集成测试（需要 mock server）
pytest tests/test_integration.py -v

# E2E 测试（标记为 slow）
pytest tests/test_e2e.py -v -m slow

# 故障注入测试
pytest tests/test_fault_injection.py -v
```
# oh-my-skills
