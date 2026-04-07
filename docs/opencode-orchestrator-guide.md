# OpenCode 多任务执行问题解决方案：opencode-orchestrator 插件

## 问题描述

在使用 OpenCode 的 skill 时，经常遇到以下问题：
- 创建了 todolist，但任务只执行了第一个
- 任务没有执行完成就结束了会话
- 需要手动提示才能继续执行剩余任务

## 问题根因

OpenCode 的 Agent Loop 机制：
```
用户输入 → LLM推理 → Tool Calls → 执行结果 → LLM推理 → ... → 无Tool Call → Session Idle → 结束
```

当 LLM 没有生成新的 tool call 时（比如完成任务后），session 进入 `idle` 状态，Agent Loop 终止，**不会继续检查剩余的 todo 任务**。

## 解决方案：opencode-orchestrator 插件

### 插件概述

| 属性 | 值 |
|------|-----|
| 仓库 | `agnusdei1207/opencode-orchestrator` |
| Stars | 113+ |
| 语言 | TypeScript |
| License | MIT |

### 核心功能

1. **Mission Loop（持久执行系统）**
   - 持续运行直到所有任务完成
   - 管理多步骤工作流
   - 处理任务状态转换

2. **Todo Continuation Handler（任务继续处理）**
   - 监听 `session.idle` 事件
   - 自动检测未完成的任务
   - 重新触发 LLM 继续执行

3. **Hook 系统**
   - 完整的插件钩子注册机制
   - 支持自定义扩展

---

## 安装步骤

### 方式一：npm 安装（推荐）

```bash
# 全局安装
npm install -g @agnusdei1207/opencode-orchestrator
```

### 方式二：本地安装

```bash
# 进入项目目录
cd your-project

# 安装依赖
npm install @agnusdei1207/opencode-orchestrator

# 复制到插件目录
mkdir -p .opencode/plugins
cp -r node_modules/@agnusdei1207/opencode-orchestrator .opencode/plugins/
```

### 方式三：手动克隆

```bash
# 克隆仓库
git clone https://github.com/agnusdei1207/opencode-orchestrator.git

# 复制到插件目录
mkdir -p .opencode/plugins
cp -r opencode-orchestrator .opencode/plugins/
```

---

## 配置

### 1. 启用插件

在 `.opencode/config.json` 中添加：

```json
{
  "plugins": [
    "opencode-orchestrator"
  ],
  "agent": {
    "mode": "build"
  }
}
```

### 2. 配置选项

```json
{
  "plugins": [
    {
      "name": "opencode-orchestrator",
      "options": {
        "missionLoop": {
          "enabled": true,
          "maxIterations": 100,
          "timeout": 3600
        },
        "todoContinuation": {
          "enabled": true,
          "checkInterval": 1000
        }
      }
    }
  ]
}
```

---

## 工作原理

### 执行流程

```
┌─────────────────────────────────────────────────────────────┐
│                     用户请求触发 Skill                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                  Skill 创建 todolist 任务列表               │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              Agent Loop 执行第一个任务                       │
└─────────────────────────────────────────────────────────────┘
                              ↓
                    ┌───────────────────┐
                    │  任务完成，无新    │
                    │  Tool Call 生成    │
                    └───────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              session.idle 事件触发                           │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│         Todo Continuation Handler 检查任务状态              │
└─────────────────────────────────────────────────────────────┘
                              ↓
                    ┌───────────────────┐
                    │  是否还有 pending │
                    │  任务？           │
                    └───────────────────┘
                    ↓           ↓
                   是          否
                    ↓           ↓
            ┌───────────────┐  ┌────────────┐
            │ 重新触发 LLM  │  │  结束会话  │
            │ 继续执行      │  │            │
            └───────────────┘  └────────────┘
```

### 关键代码逻辑

```typescript
// Todo Continuation Handler 伪代码
class TodoContinuationHandler {
  onSessionIdle(session) {
    const todos = session.todowrite.getAll();
    const pendingTodos = todos.filter(t => t.status === 'pending');
    
    if (pendingTodos.length > 0) {
      // 有未完成任务，重新触发 LLM
      session.continueWithContext({
        message: `继续执行剩余任务：${pendingTodos.map(t => t.content).join(', ')}`
      });
    } else {
      // 所有任务完成，允许结束
      session.allowTermination();
    }
  }
}
```

---

## 最佳实践

### Skill 编写规范

即使安装了插件，skill 也应该遵循以下规范以确保最佳效果：

```markdown
---
name: example-skill
description: 示例 skill
---

# 执行约束（强制）

1. **必须创建任务列表**：使用 todowrite 创建明确的任务
2. **逐个执行**：每次只执行一个任务
3. **状态更新**：完成一个任务后更新其状态为 completed
4. **继续检查**：检查是否还有 pending 任务，如有则继续

## 示例任务列表

```json
{
  "todos": [
    { "id": "task1", "content": "任务1", "status": "pending" },
    { "id": "task2", "content": "任务2", "status": "pending" },
    { "id": "task3", "content": "任务3", "status": "pending" }
  ]
}
```

## 执行流程

1. 创建任务列表
2. 执行 task1，状态更新为 in_progress
3. task1 完成，状态更新为 completed
4. 检查剩余任务（task2, task3 状态为 pending）
5. 继续执行 task2
6. ... 重复直到全部完成
```

---

## 对比：安装前后

| 特性 | 未安装插件 | 已安装插件 |
|------|-----------|-----------|
| 多任务执行 | ❌ 只执行第一个 | ✅ 全部执行 |
| 任务继续 | ❌ 需手动提示 | ✅ 自动继续 |
| 会话结束时机 | LLM 不生成 tool call 即结束 | 检查任务列表后再决定 |
| 配置复杂度 | 无 | 需要启用插件 |

---

## 故障排查

### 问题1：插件未生效

**检查**：
1. 插件是否正确安装到 `.opencode/plugins/` 目录
2. `config.json` 是否正确配置
3. 查看 OpenCode 日志输出

### 问题2：无限循环

**原因**：任务状态未正确更新，导致一直检测到 pending 任务

**解决**：
- 确保每个任务完成后调用 todowrite 更新状态为 completed
- 检查 skill 中的状态更新逻辑

### 问题3：任务仍只执行第一个

**检查**：
1. 是否正确安装插件
2. 任务列表是否正确创建
3. 模型是否支持长时间对话

---

## 相关资源

- GitHub 仓库：https://github.com/agnusdei1207/opencode-orchestrator
- 相关 Issue：
  - [#18636] Config option for continuous execution loop until task completion
  - [#16626] add session.stopping plugin hook
  - [#16589] Opencode won't run unattended for extended durations

---

## 替代方案

如果暂时无法安装插件，可以采用以下 workaround：

1. **在 Skill 中加强制指令**：明确要求模型检查剩余任务
2. **使用子代理**：将任务封装到子代理中执行
3. **手动提示**：完成一个任务后手动提示"继续执行下一个"

---

## 总结

`opencode-orchestrator` 插件通过 Hook 机制拦截 `session.idle` 事件，自动检测并继续执行未完成的 todolist 任务，从根本上解决了 OpenCode 多任务执行不完整的问题。

建议：
1. 优先安装插件
2. 同时优化 skill 编写规范
3. 双重保障确保任务完整执行