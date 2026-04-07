/**
 * session-guard.ts 核心逻辑单元测试
 *
 * 运行方式: node plugins/__tests__/session-guard.test.js
 */

const assert = require("assert");

// ============================================================
// 从 session-guard.ts 中提取纯函数
// ============================================================

const GUARDED_AGENTS = new Set(["skill-executor", "default"]);

const MAX_AUTO_RESTARTS = 5;
const RESTART_COOLDOWN_MS = 10000;

const restartCounters = new Map();
const lastRestartTime = new Map();

const statistics = {
  totalRestarts: 0,
  successfulRestarts: 0,
  failedRestarts: 0,
  sessionsMonitored: new Set(),
};

function shouldGuardAgent(agentName) {
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

function calculateProgress(completedTodos, totalTodos) {
  if (totalTodos === 0) return 0;
  return Math.round((completedTodos / totalTodos) * 100);
}

function getRecoveryStrategy(completedTodos, totalTodos) {
  const progress = calculateProgress(completedTodos, totalTodos);

  if (progress === 0) {
    return "full_restart";
  } else if (progress < 30) {
    return "restart_with_context";
  } else {
    return "continue_from_checkpoint";
  }
}

function buildContinueMessage(incompleteTodos, completedTodos, strategy = "continue_from_checkpoint") {
  const remainingList = incompleteTodos.map((t) => `- [ ] ${t.content}`).join("\n");
  const completedList = completedTodos.map((t) => `- [x] ${t.content}`).join("\n");

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
    `${strategyMsg}\n已完成的任务：\n${completedList || "无"}\n\n剩余必须完成的任务：\n${remainingList}`
  );
}

// ============================================================
// 测试用例
// ============================================================

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    failed++;
    console.log(`  ✗ ${name}`);
    console.log(`    ${e.message}`);
  }
}

// --- shouldGuardAgent 测试 ---

console.log("\nshouldGuardAgent:");

test("skill-executor → 守卫", () => {
  assert.strictEqual(shouldGuardAgent("skill-executor"), true);
});

test("default → 守卫", () => {
  assert.strictEqual(shouldGuardAgent("default"), true);
});

test("null → 守卫 default", () => {
  assert.strictEqual(shouldGuardAgent(null), true);
});

test("plan → 不守卫", () => {
  assert.strictEqual(shouldGuardAgent("plan"), false);
});

test("review → 不守卫", () => {
  assert.strictEqual(shouldGuardAgent("review"), false);
});

// --- calculateProgress 测试 ---

console.log("\ncalculateProgress:");

test("0/10 → 0%", () => {
  assert.strictEqual(calculateProgress(0, 10), 0);
});

test("5/10 → 50%", () => {
  assert.strictEqual(calculateProgress(5, 10), 50);
});

test("10/10 → 100%", () => {
  assert.strictEqual(calculateProgress(10, 10), 100);
});

test("0/0 → 0%", () => {
  assert.strictEqual(calculateProgress(0, 0), 0);
});

test("3/7 → 43%", () => {
  assert.strictEqual(calculateProgress(3, 7), 43);
});

// --- getRecoveryStrategy 测试 ---

console.log("\ngetRecoveryStrategy:");

test("0/5 → full_restart", () => {
  assert.strictEqual(getRecoveryStrategy(0, 5), "full_restart");
});

test("1/5 → restart_with_context", () => {
  assert.strictEqual(getRecoveryStrategy(1, 5), "restart_with_context");
});

test("1/10 → restart_with_context (10%)", () => {
  assert.strictEqual(getRecoveryStrategy(1, 10), "restart_with_context");
});

test("3/10 → continue_from_checkpoint (30%)", () => {
  assert.strictEqual(getRecoveryStrategy(3, 10), "continue_from_checkpoint");
});

test("5/10 → continue_from_checkpoint (50%)", () => {
  assert.strictEqual(getRecoveryStrategy(5, 10), "continue_from_checkpoint");
});

// --- buildContinueMessage 测试 ---

console.log("\nbuildContinueMessage:");

test("0% 进度 → full_restart 消息", () => {
  const msg = buildContinueMessage(
    [{ content: "任务1" }, { content: "任务2" }],
    [],
    "full_restart"
  );
  assert.ok(msg.includes("完全没有完成任务"));
});

test("10% 进度 → restart_with_context 消息", () => {
  const msg = buildContinueMessage(
    [{ content: "任务1" }, { content: "任务2" }],
    [{ content: "已完成任务" }],
    "restart_with_context"
  );
  assert.ok(msg.includes("进度很少"));
});

test("50% 进度 → continue_from_checkpoint 消息", () => {
  const msg = buildContinueMessage(
    [{ content: "任务1" }],
    [{ content: "已完成1" }, { content: "已完成2" }],
    "continue_from_checkpoint"
  );
  assert.ok(msg.includes("进度为"));
});

test("显示已完成任务列表", () => {
  const msg = buildContinueMessage(
    [{ content: "剩余1" }],
    [{ content: "完成1" }, { content: "完成2" }]
  );
  assert.ok(msg.includes("已完成的任务"));
  assert.ok(msg.includes("[x] 完成1"));
  assert.ok(msg.includes("[x] 完成2"));
});

test("显示剩余任务列表", () => {
  const msg = buildContinueMessage(
    [{ content: "剩余1" }, { content: "剩余2" }],
    [{ content: "完成1" }]
  );
  assert.ok(msg.includes("剩余必须完成的任务"));
  assert.ok(msg.includes("[ ] 剩余1"));
  assert.ok(msg.includes("[ ] 剩余2"));
});

// --- canRestart 测试 ---

console.log("\ncanRestart:");

test("新 session → 可以重启", () => {
  restartCounters.delete("new-session");
  lastRestartTime.delete("new-session");
  assert.strictEqual(canRestart("new-session"), true);
});

test("未超限 → 可以重启", () => {
  restartCounters.set("test-1", 3);
  lastRestartTime.delete("test-1");
  assert.strictEqual(canRestart("test-1"), true);
});

test("达到上限 → 不能重启", () => {
  restartCounters.set("max-session", MAX_AUTO_RESTARTS);
  lastRestartTime.delete("max-session");
  assert.strictEqual(canRestart("max-session"), false);
});

test("冷却期内 → 不能重启", () => {
  restartCounters.set("cooldown-session", 1);
  lastRestartTime.set("cooldown-session", Date.now() - 1000);
  assert.strictEqual(canRestart("cooldown-session"), false);
});

test("冷却期外 → 可以重启", () => {
  restartCounters.set("cool-session", 1);
  lastRestartTime.set("cool-session", Date.now() - RESTART_COOLDOWN_MS - 1000);
  assert.strictEqual(canRestart("cool-session"), true);
});

// --- recordRestart 测试 ---

console.log("\nrecordRestart:");

test("记录重启 → 计数器增加", () => {
  restartCounters.delete("record-test");
  lastRestartTime.delete("record-test");
  
  recordRestart("record-test");
  assert.strictEqual(getRestartCount("record-test"), 1);
  
  recordRestart("record-test");
  assert.strictEqual(getRestartCount("record-test"), 2);
});

// --- 结果汇总 ---

console.log(`\n${"=".repeat(50)}`);
console.log(`总计: ${passed + failed} 个测试, ${passed} 通过, ${failed} 失败`);
if (failed > 0) {
  console.log("\n有测试失败！");
  process.exit(1);
} else {
  console.log("\n全部通过！");
  process.exit(0);
}