/**
 * TODO Validator Plugin - TODO 验证器
 *
 * 核心功能：
 * 1. 拦截 todowrite 工具调用，防止模型作弊
 * 2. SKIP_IN_PROGRESS: 自动修正为 in_progress 并放行（存在合理场景）
 * 3. MULTI_COMPLETE: 一次调用中批量完成超过阈值个 pending 任务 → throw 强制阻断
 * 4. 记录所有 todowrite 操作历史
 * 5. 渐进式阻断 - 首次违规警告，后续阻断
 * 6. 详细的错误消息 - 包含具体任务 ID
 *
 * 解决的问题：
 * - 模型一次性把所有 pending 任务标记为 completed（作弊）
 * - 模型跳过某些任务不执行
 * - 模型不遵循 in_progress → completed 状态流转
 *
 * 设计决策：
 * - SKIP_IN_PROGRESS 不 throw，而是自动修正：因为小模型偶尔会"忘记"中间状态，
 *   但任务实际上确实执行了。throw 会消耗 reasoning step 且模型可能不理解错误。
 * - MULTI_COMPLETE 才 throw：一次完成多个任务几乎不可能是正常行为，
 *   且错误消息中包含明确指令，模型可以理解并重试。
 * - 渐进式阻断：累计 2 次违规后才会阻断，给模型改进机会
 * - throw 前 history 已记录（在 validateTodoChange 之前调用 recordViolation），
 *   确保 throw 后 tool.execute.after 不触发也不影响历史连续性。
 */

const TODOS_HISTORY = new Map();

/** 一次调用中最多允许完成的 pending 任务数 */
const MAX_BATCH_COMPLETE = 2;

/** 累计违规次数阈值，超过后阻断 */
const MAX_CUMULATIVE_VIOLATIONS = 2;

function getTodosHistory(sessionID) {
  if (!TODOS_HISTORY.has(sessionID)) {
    TODOS_HISTORY.set(sessionID, []);
  }
  return TODOS_HISTORY.get(sessionID);
}

/** 获取累计违规次数 */
function getCumulativeViolations(sessionID) {
  const history = getTodosHistory(sessionID);
  return history.filter(h => h.type === 'violation_blocked').length;
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

/**
 * 验证 todo 变更，返回 { violations, corrections, batchCount }
 *
 * violations: 严重违规列表（MULTI_COMPLETE）
 * corrections: 需要自动修正的任务列表（SKIP_IN_PROGRESS → in_progress）
 * batchCount: 本次调用中从 pending 直跳 completed 的任务总数
 */
function validateTodoChange(args, history) {
  const violations = [];
  const corrections = [];

  if (!args.todos || !Array.isArray(args.todos)) {
    return { violations, corrections, batchCount: 0 };
  }

  const prevTodos =
    history.length > 0 ? history[history.length - 1].args?.todos || [] : [];
  const prevMap = new Map(prevTodos.map((t) => [t.id, t]));

  let batchCount = 0;

  for (const todo of args.todos) {
    const prev = prevMap.get(todo.id);

    if (prev && prev.status === "pending" && todo.status === "completed") {
      batchCount++;
      // SKIP_IN_PROGRESS: 记录违规，但标记为需要修正而非阻断
      violations.push({
        type: "SKIP_IN_PROGRESS",
        todoId: todo.id,
        message: `Task "${todo.id}" jumped from pending to completed without in_progress`,
      });
      corrections.push({ todoId: todo.id, todo });
    }
  }

  // MULTI_COMPLETE: 一次调用中批量完成超过阈值的 pending 任务 → 严重违规
  if (batchCount > MAX_BATCH_COMPLETE) {
    violations.push({
      type: "MULTI_COMPLETE",
      message: `Attempting to complete ${batchCount} tasks at once (max ${MAX_BATCH_COMPLETE})`,
      count: batchCount,
    });
  }

  return { violations, corrections, batchCount };
}

export const TodoValidatorPlugin = async ({ client, directory }) => {
  client.app.log({
    body: {
      service: "todo-validator",
      level: "info",
      message: "TODO Validator Plugin initialized",
      extra: { directory, maxBatchComplete: MAX_BATCH_COMPLETE },
    },
  });

  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "todowrite") return;

      const sessionID = input.sessionID;
      const { violations, corrections, batchCount } = validateTodoChange(
        output.args,
        getTodosHistory(sessionID),
      );

      if (violations.length === 0) return;

      const hasMultiComplete = violations.some(
        (v) => v.type === "MULTI_COMPLETE",
      );
      const skipViolations = violations.filter(
        (v) => v.type === "SKIP_IN_PROGRESS",
      );

      // 先记录历史（在 throw 之前，确保 throw 后 after 不触发也不影响连续性）
      recordTodoChange(sessionID, {
        type: hasMultiComplete ? "violation_blocked" : "violation_corrected",
        violations,
        args: output.args,
      });

      // 渐进式阻断：检查累计违规次数
      const cumulativeViolations = getCumulativeViolations(sessionID);

      if (hasMultiComplete && cumulativeViolations >= MAX_CUMULATIVE_VIOLATIONS) {
        // 累计违规超过阈值 → 强制阻断
        const multiV = violations.find((v) => v.type === "MULTI_COMPLETE");
        const correctedTaskIds = corrections.map(c => c.todoId);
        
        client.app.log({
          body: {
            service: "todo-validator",
            level: "error",
            message: `BLOCKED: batch completion of ${multiV.count} tasks (cumulative violations: ${cumulativeViolations}) in session ${sessionID}`,
            extra: { sessionID, violations, cumulativeViolations },
          },
        });

        throw new Error(
          `违规：你已累计违规 ${cumulativeViolations} 次。\n` +
          `本次你尝试一次完成 ${multiV.count} 个任务（最多允许 ${MAX_BATCH_COMPLETE} 个）。\n\n` +
          `需要修改的任务 ID: ${correctedTaskIds.join(', ')}\n\n` +
          `正确做法：\n` +
          `1. 先将这些任务的 status 改为 "in_progress"\n` +
          `2. 逐个完成每个任务后再将 status 改为 "completed"\n` +
          `3. 每次调用 todowrite 最多只能修改 ${MAX_BATCH_COMPLETE} 个任务`
        );
      }

      // 累计违规未超过阈值 → 警告放行
      if (hasMultiComplete) {
        const multiV = violations.find((v) => v.type === "MULTI_COMPLETE");
        const correctedTaskIds = corrections.map(c => c.todoId);
        
        client.app.log({
          body: {
            service: "todo-validator",
            level: "warn",
            message: `WARNING: batch completion attempt ${multiV.count} tasks (${cumulativeViolations}/${MAX_CUMULATIVE_VIOLATIONS} violations) in session ${sessionID}`,
            extra: { sessionID, violations, cumulativeViolations },
          },
        });

        // 自动修正为 in_progress
        for (const { todo } of corrections) {
          todo.status = "in_progress";
        }

        // 返回警告消息（在错误消息中）
        const warningMsg = 
          `警告：本次你尝试一次完成 ${multiV.count} 个任务，这可能是作弊行为。\n` +
          `已自动修正需要修改的任务 ID: ${correctedTaskIds.join(', ')}\n\n` +
          `注意：累计违规达到 ${MAX_CUMULATIVE_VIOLATIONS} 次后将强制阻断。\n` +
          `正确做法：先将任务 status 改为 "in_progress"，完成后再改为 "completed"。`;

        // 修改 output 以包含警告消息
        if (output.error) {
          output.error = warningMsg;
        }
      }

      // SKIP_IN_PROGRESS（无 MULTI_COMPLETE）: 自动修正 → 放行
      if (skipViolations.length > 0 && corrections.length > 0) {
        for (const { todo } of corrections) {
          todo.status = "in_progress";
        }

        client.app.log({
          body: {
            service: "todo-validator",
            level: "warn",
            message: `CORRECTED: ${skipViolations.length} tasks auto-fixed to in_progress in session ${sessionID}`,
            extra: {
              sessionID,
              correctedTaskIds: corrections.map((c) => c.todoId),
            },
          },
        });
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

      // 追加规则文本到已有的第一个 text part，避免 id 冲突导致原始内容被丢弃
      const firstTextPart = output.parts.find((p) => p.type === "text");
      const ruleText =
        "\n\n⚠️ TODO 执行规则：\n" +
        "1. 每个任务必须按顺序执行：pending → in_progress → completed\n" +
        "2. 不能跳过 in_progress 状态直接将 pending 标记为 completed\n" +
        "3. 每次调用 todowrite 最多只能将 2 个任务标记为 completed\n" +
        "4. 如果跳过 in_progress 状态，系统会自动修正为 in_progress\n" +
        "5. 如果一次完成超过 2 个任务，工具调用会被拒绝\n" +
        "6. 只有真正完成一个任务后才能标记为 completed\n";

      if (firstTextPart) {
        firstTextPart.text += ruleText;
      } else {
        output.parts.push({
          type: "text",
          text: ruleText,
        });
      }
    },
  };
};
