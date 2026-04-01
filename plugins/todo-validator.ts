/**
 * TODO Validator Plugin - TODO 验证器
 *
 * 核心功能：
 * 1. 拦截 todowrite 工具调用，防止模型作弊
 * 2. 验证不能批量标记未完成的任务为 completed
 * 3. 验证不能跳过 in_progress 状态
 * 4. 记录所有 todowrite 操作历史
 *
 * 解决的问题：
 * - 模型一次性把所有 pending 任务标记为 completed（作弊）
 * - 模型跳过某些任务不执行
 * - 模型不遵循 in_progress → completed 状态流转
 */

const TODOS_HISTORY = new Map();

function getTodosHistory(sessionID) {
  if (!TODOS_HISTORY.has(sessionID)) {
    TODOS_HISTORY.set(sessionID, []);
  }
  return TODOS_HISTORY.get(sessionID);
}

function recordTodoChange(sessionID, change) {
  const history = getTodosHistory(sessionID);
  history.push({
    ...change,
    timestamp: Date.now(),
  });
  if (history.length > 100) {
    TODOS_HISTORY.set(sessionID, history.slice(-50));
  }
}

function validateTodoChange(args, history) {
  const violations = [];

  if (!args.todos || !Array.isArray(args.todos)) {
    return violations;
  }

  const prevTodos = history.length > 0 ? history[history.length - 1].todos : [];
  const prevMap = new Map(prevTodos.map((t) => [t.id, t]));

  for (const todo of args.todos) {
    const prev = prevMap.get(todo.id);

    if (prev && prev.status === "pending" && todo.status === "completed") {
      violations.push({
        type: "SKIP_IN_PROGRESS",
        todoId: todo.id,
        message: `Task "${todo.id}" jumped from pending to completed without in_progress`,
      });
    }

    if (prev && prev.status === "pending" && todo.status === "completed") {
      const recentChanges = history.filter(
        (h) => Date.now() - h.timestamp < 5000
      );
      const completedInBatch = recentChanges.filter(
        (h) =>
          h.args?.todos?.some((t) => t.id === todo.id && t.status === "completed")
      );
      if (completedInBatch.length > 5) {
        violations.push({
          type: "BATCH_COMPLETE",
          todoId: todo.id,
          message: `Task "${todo.id}" appears to be batch-completed (possible cheating)`,
        });
      }
    }
  }

  return violations;
}

export const TodoValidatorPlugin = async ({ client, directory }) => {
  client.app.log({
    body: {
      service: "todo-validator",
      level: "info",
      message: "TODO Validator Plugin initialized",
      extra: { directory },
    },
  });

  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "todowrite") return;

      const sessionID = input.sessionID;
      const violations = validateTodoChange(
        output.args,
        getTodosHistory(sessionID)
      );

      if (violations.length > 0) {
        recordTodoChange(sessionID, {
          type: "violation_detected",
          violations,
          args: output.args,
        });

        client.app.log({
          body: {
            service: "todo-validator",
            level: "warn",
            message: `TODO validation violations detected in session ${sessionID}`,
            extra: { sessionID, violations },
          },
        });

        output.args._validationWarnings = violations.map((v) => v.message);
      }
    },

    "tool.execute.after": async (input, output) => {
      if (input.tool !== "todowrite") return;

      recordTodoChange(input.sessionID, {
        type: "todo_updated",
        args: input.args,
        result: output.output,
      });

      const todos = input.args?.todos;
      if (todos && Array.isArray(todos)) {
        const completed = todos.filter((t) => t.status === "completed").length;
        const total = todos.length;

        client.app.log({
          body: {
            service: "todo-validator",
            level: "info",
            message: `TODO update: ${completed}/${total} completed in session ${input.sessionID}`,
            extra: {
              sessionID: input.sessionID,
              completed,
              total,
            },
          },
        });
      }
    },

    "chat.message": async (input, output) => {
      if (input.agent !== "skill-executor" && input.agent !== "default") return;

      output.parts.push({
        type: "text",
        text:
          "\n\n⚠️ TODO 执行规则：\n" +
          "1. 每个任务必须按顺序执行：pending → in_progress → completed\n" +
          "2. 不能跳过 in_progress 状态直接将 pending 标记为 completed\n" +
          "3. 不能批量标记多个任务为 completed，必须逐个完成\n" +
          "4. 系统会监控你的 todowrite 操作，违规操作会被记录\n" +
          "5. 只有真正完成一个任务后才能标记为 completed\n",
      });
    },
  };
};
