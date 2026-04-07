/**
 * Session Guard Plugin - 会话守卫
 *
 * 核心功能：
 * 1. 监听 session.idle 事件，检测会话是否提前结束
 * 2. 当 session 变为 idle/done 但 todos 未完成时，自动发送继续指令
 * 3. 支持配置白名单 agent（某些 agent 允许提前结束）
 * 4. 记录所有干预事件到日志
 * 5. 智能恢复策略 - 根据完成进度决定恢复方式
 * 6. 统计信息收集 - 收集干预成功率
 *
 * 解决的问题：
 * - 小模型自作主张提前结束会话
 * - 模型偷懒，idle 但任务未完成
 * - session 状态异常（error/done）但任务未完成
 */

const GUARDED_AGENTS = new Set([
  "skill-executor",
  "default",
]);

const MAX_AUTO_RESTARTS = 5;
const RESTART_COOLDOWN_MS = 10000;

const restartCounters = new Map();
const lastRestartTime = new Map();

/** 统计信息 */
const statistics = {
  totalRestarts: 0,
  successfulRestarts: 0,
  failedRestarts: 0,
  sessionsMonitored: new Set(),
};

/** 计算完成进度百分比 */
function calculateProgress(completedTodos, totalTodos) {
  if (totalTodos === 0) return 0;
  return Math.round((completedTodos / totalTodos) * 100);
}

/** 根据进度决定恢复策略 */
function getRecoveryStrategy(completedTodos, totalTodos) {
  const progress = calculateProgress(completedTodos, totalTodos);
  
  if (progress === 0) {
    return "full_restart"; // 完全没有进展，需要完全重新开始
  } else if (progress < 30) {
    return "restart_with_context"; // 进度很少，带着上下文重启
  } else {
    return "continue_from_checkpoint"; // 进度不错，从断点继续
  }
}

function shouldGuardAgent(agentName) {
  if (GUARDED_AGENTS.has("*")) return true;
  return GUARDED_AGENTS.has(agentName || "default");
}

function getRestartCount(sessionID) {
  return restartCounters.get(sessionID) || 0;
}

function incrementRestartCount(sessionID) {
  const count = getRestartCount(sessionID) + 1;
  restartCounters.set(sessionID, count);
  return count;
}

function canRestart(sessionID) {
  const count = getRestartCount(sessionID);
  if (count >= MAX_AUTO_RESTARTS) return false;

  const lastTime = lastRestartTime.get(sessionID) || 0;
  const now = Date.now();
  if (now - lastTime < RESTART_COOLDOWN_MS) return false;

  return true;
}

function recordRestart(sessionID) {
  lastRestartTime.set(sessionID, Date.now());
  incrementRestartCount(sessionID);
}

function buildContinueMessage(incompleteTodos, completedTodos, strategy = "continue_from_checkpoint") {
  const remainingList = incompleteTodos
    .map((t) => `- [ ] ${t.content}`)
    .join("\n");
  const completedList = completedTodos
    .map((t) => `- [x] ${t.content}`)
    .join("\n");

  const progress = calculateProgress(completedTodos.length, completedTodos.length + incompleteTodos.length);
  
  let strategyMsg = "";
  switch (strategy) {
    case "full_restart":
      strategyMsg = "⚠️ 检测到你完全没有完成任务，需要完全重新开始。\n";
      break;
    case "restart_with_context":
      strategyMsg = `⚠️ 检测到你的进度很少（${progress}%），需要带着上下文重新开始。\n`;
      break;
    case "continue_from_checkpoint":
    default:
      strategyMsg = `✅ 检测到你的进度为 ${progress}%，将从断点继续执行。\n`;
  }

  return (
    `${strategyMsg}\n` +
    `已完成的任务：\n${completedList || "无"}\n\n` +
    `剩余必须完成的任务：\n${remainingList}\n\n` +
    `重要要求：\n` +
    `1. 你必须继续完成以上所有剩余任务，不要跳过\n` +
    `2. 每完成一个任务就立即用 todowrite 标记为 completed\n` +
    `3. 不要提前结束，不要说"任务已完成"除非所有任务都真正完成\n` +
    `4. 如果遇到困难，尝试多种方法解决\n` +
    `5. 只有当所有任务都完成后才能结束\n\n` +
    `现在开始继续执行剩余任务。`
  );
}

export const SessionGuardPlugin = async ({ client, directory }) => {
  client.app.log({
    body: {
      service: "session-guard",
      level: "info",
      message: "Session Guard Plugin initialized",
      extra: { directory },
    },
  });

  return {
    event: async ({ event }) => {
      if (event.type !== "session.idle") return;

      // opencode 1.3.13: session.idle event 结构为 event.properties.sessionID (string)
      const sessionID =
        event.properties?.info?.id ||
        event.properties?.sessionID;

      if (!sessionID) return;

      // 获取 agent 名称：通过 client 从 session 信息中获取
      let agentName = event.properties?.info?.agent || "default";

      if (!shouldGuardAgent(agentName)) return;
      if (!canRestart(sessionID)) {
        client.app.log({
          body: {
            service: "session-guard",
            level: "warn",
            message: `Session ${sessionID} exceeded max restarts or in cooldown`,
            extra: { sessionID, restartCount: getRestartCount(sessionID) },
          },
        });
        return;
      }

      let todos = [];
      try {
        const todoResult = await client.session.todo(sessionID);
        todos = todoResult?.data || todoResult || [];
      } catch (e) {
        client.app.log({
          body: {
            service: "session-guard",
            level: "error",
            message: `Failed to get todos for session ${sessionID}: ${e.message}`,
            extra: { sessionID },
          },
        });
        return;
      }

      if (todos.length === 0) return;

      const incompleteTodos = todos.filter((t) => t.status !== "completed");
      const completedTodos = todos.filter((t) => t.status === "completed");

      if (incompleteTodos.length === 0) {
        client.app.log({
          body: {
            service: "session-guard",
            level: "info",
            message: `Session ${sessionID} idle with all todos completed`,
            extra: { sessionID },
          },
        });
        return;
      }

      client.app.log({
        body: {
          service: "session-guard",
          level: "warn",
          message: `Session ${sessionID} idle with ${incompleteTodos.length} incomplete todos. Auto-restarting.`,
          extra: {
            sessionID,
            incompleteCount: incompleteTodos.length,
            completedCount: completedTodos.length,
            restartCount: getRestartCount(sessionID) + 1,
          },
        },
      });

      recordRestart(sessionID);

      // 更新统计信息
      statistics.totalRestarts++;
      statistics.sessionsMonitored.add(sessionID);

      // 根据完成进度选择恢复策略
      const strategy = getRecoveryStrategy(completedTodos.length, todos.length);
      const continueMsg = buildContinueMessage(incompleteTodos, completedTodos, strategy);

      try {
        await client.session.message(sessionID, {
          message: continueMsg,
          agent: agentName,
        });

        // 记录成功恢复
        statistics.successfulRestarts++;

        client.app.log({
          body: {
            service: "session-guard",
            level: "info",
            message: `Auto-restart successful for session ${sessionID} (strategy: ${strategy})`,
            extra: {
              sessionID,
              strategy,
              progress: calculateProgress(completedTodos.length, todos.length),
              totalRestarts: statistics.totalRestarts,
              successfulRestarts: statistics.successfulRestarts,
            },
          },
        });

        client.app.log({
          body: {
            service: "session-guard",
            level: "info",
            message: `Sent continue message to session ${sessionID}`,
            extra: { sessionID },
          },
        });
      } catch (e) {
        // 记录失败
        statistics.failedRestarts++;
        
        client.app.log({
          body: {
            service: "session-guard",
            level: "error",
            message: `Failed to send continue message to session ${sessionID}: ${e.message}`,
            extra: { 
              sessionID,
              error: e.message,
              totalRestarts: statistics.totalRestarts,
              failedRestarts: statistics.failedRestarts,
            },
          },
        });
      }
    },

    // 统计信息端点（可选，用于外部查询）
    "server.started": async () => {
      client.app.log({
        body: {
          service: "session-guard",
          level: "info",
          message: `Session Guard Plugin initialized with stats: ${JSON.stringify(statistics)}`,
        },
      });
    },

    "chat.message": async (input, output) => {
      if (!shouldGuardAgent(input.agent)) return;

      // 追加警告文本到已有的第一个 text part，避免 id 冲突导致原始内容被丢弃
      const firstTextPart = output.parts.find((p) => p.type === "text");
      if (firstTextPart) {
        firstTextPart.text +=
          "\n\n⚠️ 系统强制要求：\n" +
          "1. 你必须严格按照任务列表逐条执行，不要跳步\n" +
          "2. 每完成一个任务就用 todowrite 标记为 completed\n" +
          "3. 不要提前结束会话，只有所有任务都完成后才能结束\n" +
          "4. 如果遇到困难，尝试多种方法解决，不要跳过任务\n" +
          "5. 系统会自动监控你的进度，如果提前结束会自动恢复\n";
      } else {
        output.parts.push({
          type: "text",
          text:
            "\n\n⚠️ 系统强制要求：\n" +
            "1. 你必须严格按照任务列表逐条执行，不要跳步\n" +
            "2. 每完成一个任务就用 todowrite 标记为 completed\n" +
            "3. 不要提前结束会话，只有所有任务都完成后才能结束\n" +
            "4. 如果遇到困难，尝试多种方法解决，不要跳过任务\n" +
            "5. 系统会自动监控你的进度，如果提前结束会自动恢复\n",
        });
      }
    },
  };
};
