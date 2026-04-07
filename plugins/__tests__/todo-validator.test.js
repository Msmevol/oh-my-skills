/**
 * todo-validator.ts 核心逻辑单元测试
 *
 * 由于 TS 插件没有独立的构建/测试工具链，这里直接提取纯函数并用 Node.js assert 测试。
 * 运行方式: node plugins/__tests__/todo-validator.test.js
 */

const assert = require("assert");
const fs = require("fs");
const path = require("path");

// ============================================================
// 从 todo-validator.ts 中提取纯函数（避免需要 TypeScript 编译）
// ============================================================

const MAX_BATCH_COMPLETE = 2;

function recordTodoChange(historyMap, sessionID, change) {
  if (!historyMap.has(sessionID)) {
    historyMap.set(sessionID, []);
  }
  const history = historyMap.get(sessionID);
  history.push({ ...change, timestamp: Date.now() });
  if (history.length > 100) {
    historyMap.set(sessionID, history.slice(-50));
  }
}

function getTodosHistory(historyMap, sessionID) {
  if (!historyMap.has(sessionID)) {
    historyMap.set(sessionID, []);
  }
  return historyMap.get(sessionID);
}

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
      violations.push({
        type: "SKIP_IN_PROGRESS",
        todoId: todo.id,
        message: `Task "${todo.id}" jumped from pending to completed without in_progress`,
      });
      corrections.push({ todoId: todo.id, todo });
    }
  }

  if (batchCount > MAX_BATCH_COMPLETE) {
    violations.push({
      type: "MULTI_COMPLETE",
      message: `Attempting to complete ${batchCount} tasks at once (max ${MAX_BATCH_COMPLETE})`,
      count: batchCount,
    });
  }

  return { violations, corrections, batchCount };
}

/**
 * 模拟 tool.execute.before hook 的完整行为
 * 返回 { blocked: boolean, error: string|null, correctedArgs: any|null }
 */
function simulateBeforeHook(historyMap, sessionID, args) {
  const history = getTodosHistory(historyMap, sessionID);
  const { violations, corrections, batchCount } = validateTodoChange(args, history);

  if (violations.length === 0) {
    return { blocked: false, error: null, correctedArgs: null };
  }

  const hasMultiComplete = violations.some((v) => v.type === "MULTI_COMPLETE");
  const skipViolations = violations.filter((v) => v.type === "SKIP_IN_PROGRESS");

  // 记录历史（在 throw 之前）
  recordTodoChange(historyMap, sessionID, {
    type: hasMultiComplete ? "violation_blocked" : "violation_corrected",
    violations,
    args,
  });

  if (hasMultiComplete) {
    const multiV = violations.find((v) => v.type === "MULTI_COMPLETE");
    return {
      blocked: true,
      error: `违规：一次最多完成 ${MAX_BATCH_COMPLETE} 个任务，你尝试完成 ${multiV.count} 个。请重新调用 todowrite，只将你实际已完成的任务标记为 completed，其余任务必须先标记为 in_progress。`,
      correctedArgs: null,
    };
  }

  // SKIP_IN_PROGRESS: 自动修正
  if (skipViolations.length > 0 && corrections.length > 0) {
    // 深拷贝 args 以避免修改原始对象
    const corrected = JSON.parse(JSON.stringify(args));
    for (const { todoId } of corrections) {
      const t = corrected.todos.find((t) => t.id === todoId);
      if (t) t.status = "in_progress";
    }
    return { blocked: false, error: null, correctedArgs: corrected };
  }

  return { blocked: false, error: null, correctedArgs: null };
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

// --- validateTodoChange 纯函数测试 ---

console.log("\nvalidateTodoChange:");

test("空历史 + 正常 pending→in_progress → 无违规", () => {
  const result = validateTodoChange(
    { todos: [{ id: "1", status: "in_progress" }] },
    [],
  );
  assert.strictEqual(result.violations.length, 0);
  assert.strictEqual(result.corrections.length, 0);
  assert.strictEqual(result.batchCount, 0);
});

test("pending→completed 跳过 in_progress → SKIP_IN_PROGRESS", () => {
  const history = [
    { args: { todos: [{ id: "1", status: "pending" }, { id: "2", status: "pending" }] } },
  ];
  const result = validateTodoChange(
    { todos: [{ id: "1", status: "completed" }, { id: "2", status: "pending" }] },
    history,
  );
  assert.strictEqual(result.violations.length, 1);
  assert.strictEqual(result.violations[0].type, "SKIP_IN_PROGRESS");
  assert.strictEqual(result.violations[0].todoId, "1");
  assert.strictEqual(result.corrections.length, 1);
  assert.strictEqual(result.batchCount, 1);
});

test("in_progress→completed → 无违规", () => {
  const history = [
    { args: { todos: [{ id: "1", status: "in_progress" }] } },
  ];
  const result = validateTodoChange(
    { todos: [{ id: "1", status: "completed" }] },
    history,
  );
  assert.strictEqual(result.violations.length, 0);
  assert.strictEqual(result.batchCount, 0);
});

test("3 个 pending 直跳 completed → MULTI_COMPLETE", () => {
  const history = [
    {
      args: {
        todos: [
          { id: "1", status: "pending" },
          { id: "2", status: "pending" },
          { id: "3", status: "pending" },
        ],
      },
    },
  ];
  const result = validateTodoChange(
    {
      todos: [
        { id: "1", status: "completed" },
        { id: "2", status: "completed" },
        { id: "3", status: "completed" },
      ],
    },
    history,
  );
  // 应该有 3 个 SKIP_IN_PROGRESS + 1 个 MULTI_COMPLETE
  assert.strictEqual(result.violations.length, 4);
  assert.strictEqual(result.batchCount, 3);
  const multi = result.violations.find((v) => v.type === "MULTI_COMPLETE");
  assert.ok(multi);
  assert.strictEqual(multi.count, 3);
});

test("2 个 pending 直跳 completed → 不触发 MULTI_COMPLETE（阈值允许）", () => {
  const history = [
    {
      args: {
        todos: [
          { id: "1", status: "pending" },
          { id: "2", status: "pending" },
        ],
      },
    },
  ];
  const result = validateTodoChange(
    {
      todos: [
        { id: "1", status: "completed" },
        { id: "2", status: "completed" },
      ],
    },
    history,
  );
  // 只有 2 个 SKIP_IN_PROGRESS，不超过 MAX_BATCH_COMPLETE=2
  assert.strictEqual(result.batchCount, 2);
  const multi = result.violations.find((v) => v.type === "MULTI_COMPLETE");
  assert.ok(!multi, "不应触发 MULTI_COMPLETE");
});

test("1 个 pending 直跳 completed → 不触发 MULTI_COMPLETE", () => {
  const history = [
    { args: { todos: [{ id: "1", status: "pending" }] } },
  ];
  const result = validateTodoChange(
    { todos: [{ id: "1", status: "completed" }] },
    history,
  );
  assert.strictEqual(result.batchCount, 1);
  const multi = result.violations.find((v) => v.type === "MULTI_COMPLETE");
  assert.ok(!multi, "不应触发 MULTI_COMPLETE");
});

test("混合状态：1 个正常 completed + 3 个跳过 → MULTI_COMPLETE", () => {
  const history = [
    {
      args: {
        todos: [
          { id: "1", status: "in_progress" },
          { id: "2", status: "pending" },
          { id: "3", status: "pending" },
          { id: "4", status: "pending" },
        ],
      },
    },
  ];
  const result = validateTodoChange(
    {
      todos: [
        { id: "1", status: "completed" },
        { id: "2", status: "completed" },
        { id: "3", status: "completed" },
        { id: "4", status: "completed" },
      ],
    },
    history,
  );
  // 只有 3 个 pending→completed 跳过（id: 2, 3, 4），超过阈值
  assert.strictEqual(result.batchCount, 3);
  assert.strictEqual(result.corrections.length, 3);
  const multi = result.violations.find((v) => v.type === "MULTI_COMPLETE");
  assert.ok(multi);
  assert.strictEqual(multi.count, 3);
});

test("空 args → 无违规", () => {
  const result = validateTodoChange({}, []);
  assert.strictEqual(result.violations.length, 0);
});

test("args.todos 为 null → 无违规", () => {
  const result = validateTodoChange({ todos: null }, []);
  assert.strictEqual(result.violations.length, 0);
});

test("新任务（history 为空）+ completed → 无违规（无 prev 可比对）", () => {
  const result = validateTodoChange(
    { todos: [{ id: "1", status: "completed" }] },
    [],
  );
  assert.strictEqual(result.violations.length, 0);
  assert.strictEqual(result.batchCount, 0);
});

// --- simulateBeforeHook 行为测试 ---

console.log("\nsimulateBeforeHook (完整行为):");

test("正常调用 → 不阻断，不修正", () => {
  const historyMap = new Map();
  const args = { todos: [{ id: "1", status: "in_progress" }] };
  const result = simulateBeforeHook(historyMap, "s1", args);
  assert.strictEqual(result.blocked, false);
  assert.strictEqual(result.correctedArgs, null);
  // history 不应有记录（无违规）
  const history = getTodosHistory(historyMap, "s1");
  assert.strictEqual(history.length, 0);
});

test("1 个 SKIP_IN_PROGRESS → 修正为 in_progress，放行", () => {
  const historyMap = new Map();
  // 先记录初始状态
  recordTodoChange(historyMap, "s2", {
    type: "todo_updated",
    args: { todos: [{ id: "t1", status: "pending" }, { id: "t2", status: "pending" }] },
  });

  const args = {
    todos: [{ id: "t1", status: "completed" }, { id: "t2", status: "pending" }],
  };
  const result = simulateBeforeHook(historyMap, "s2", args);
  assert.strictEqual(result.blocked, false);
  assert.strictEqual(result.error, null);
  assert.ok(result.correctedArgs);
  assert.strictEqual(result.correctedArgs.todos[0].status, "in_progress");
  assert.strictEqual(result.correctedArgs.todos[1].status, "pending");

  // 原始 args 不应被修改
  assert.strictEqual(args.todos[0].status, "completed");
});

test("3 个 SKIP_IN_PROGRESS → MULTI_COMPLETE 阻断", () => {
  const historyMap = new Map();
  recordTodoChange(historyMap, "s3", {
    type: "todo_updated",
    args: {
      todos: [
        { id: "a", status: "pending" },
        { id: "b", status: "pending" },
        { id: "c", status: "pending" },
      ],
    },
  });

  const args = {
    todos: [
      { id: "a", status: "completed" },
      { id: "b", status: "completed" },
      { id: "c", status: "completed" },
    ],
  };
  const result = simulateBeforeHook(historyMap, "s3", args);
  assert.strictEqual(result.blocked, true);
  assert.ok(result.error.includes("3"));
  assert.strictEqual(result.correctedArgs, null);

  // history 应记录 violation_blocked
  const history = getTodosHistory(historyMap, "s3");
  assert.strictEqual(history[history.length - 1].type, "violation_blocked");
});

test("2 个 SKIP_IN_PROGRESS → 不阻断，修正放行", () => {
  const historyMap = new Map();
  recordTodoChange(historyMap, "s4", {
    type: "todo_updated",
    args: {
      todos: [
        { id: "x", status: "pending" },
        { id: "y", status: "pending" },
      ],
    },
  });

  const args = {
    todos: [
      { id: "x", status: "completed" },
      { id: "y", status: "completed" },
    ],
  };
  const result = simulateBeforeHook(historyMap, "s4", args);
  assert.strictEqual(result.blocked, false);
  assert.ok(result.correctedArgs);
  assert.strictEqual(result.correctedArgs.todos[0].status, "in_progress");
  assert.strictEqual(result.correctedArgs.todos[1].status, "in_progress");
});

test("阻断后 history 连续性不受影响", () => {
  const historyMap = new Map();
  // 记录 3 次正常操作
  for (let i = 0; i < 3; i++) {
    recordTodoChange(historyMap, "s5", {
      type: "todo_updated",
      args: { todos: [{ id: `${i}`, status: "completed" }] },
    });
  }

  const history = getTodosHistory(historyMap, "s5");
  assert.strictEqual(history.length, 3);

  // 模拟阻断（MULTI_COMPLETE）
  recordTodoChange(historyMap, "s5", {
    type: "violation_blocked",
    violations: [{ type: "MULTI_COMPLETE" }],
    args: { todos: [] },
  });
  assert.strictEqual(history.length, 4);
  assert.strictEqual(history[3].type, "violation_blocked");

  // 下一次调用的 prevTodos 来自 history[3].args.todos
  const prevTodos = history[history.length - 1].args?.todos;
  assert.ok(Array.isArray(prevTodos)); // 阻断时记录了 args 快照
});

test("多个 session 独立维护 history", () => {
  const historyMap = new Map();
  recordTodoChange(historyMap, "sA", {
    type: "todo_updated",
    args: { todos: [{ id: "1", status: "pending" }] },
  });
  recordTodoChange(historyMap, "sB", {
    type: "todo_updated",
    args: { todos: [{ id: "1", status: "in_progress" }] },
  });

  // sA: pending→completed → SKIP_IN_PROGRESS
  const resultA = simulateBeforeHook(historyMap, "sA", {
    todos: [{ id: "1", status: "completed" }],
  });
  assert.strictEqual(resultA.blocked, false);
  assert.ok(resultA.correctedArgs);

  // sB: in_progress→completed → 无违规
  const resultB = simulateBeforeHook(historyMap, "sB", {
    todos: [{ id: "1", status: "completed" }],
  });
  assert.strictEqual(resultB.blocked, false);
  assert.strictEqual(resultB.correctedArgs, null);
});

test("10 个 pending 直跳 completed → MULTI_COMPLETE 阻断", () => {
  const historyMap = new Map();
  const todos = Array.from({ length: 10 }, (_, i) => ({
    id: `task-${i}`,
    status: "pending",
  }));
  recordTodoChange(historyMap, "s6", { type: "todo_updated", args: { todos } });

  const args = {
    todos: todos.map((t) => ({ ...t, status: "completed" })),
  };
  const result = simulateBeforeHook(historyMap, "s6", args);
  assert.strictEqual(result.blocked, true);
  assert.ok(result.error.includes("10"));
});

test("修正后的 args 不影响原始 args（深拷贝）", () => {
  const historyMap = new Map();
  recordTodoChange(historyMap, "s7", {
    type: "todo_updated",
    args: { todos: [{ id: "t", status: "pending" }] },
  });

  const args = { todos: [{ id: "t", status: "completed" }] };
  const result = simulateBeforeHook(historyMap, "s7", args);
  assert.strictEqual(result.correctedArgs.todos[0].status, "in_progress");
  assert.strictEqual(args.todos[0].status, "completed"); // 原始未变
});

// --- history 溢出测试 ---

console.log("\nrecordTodoChange (history 管理):");

test("超过 100 条记录时自动裁剪到 50 条", () => {
  const historyMap = new Map();
  // 添加 101 条记录：第 101 条触发裁剪
  for (let i = 0; i < 101; i++) {
    recordTodoChange(historyMap, "s8", {
      type: "todo_updated",
      args: { todos: [{ id: `${i}`, status: "pending" }] },
    });
  }
  const history = getTodosHistory(historyMap, "s8");
  assert.strictEqual(history.length, 50);
  // 保留最后 50 条（id: 51-100）
  assert.strictEqual(history[0].args.todos[0].id, "51");
  assert.strictEqual(history[49].args.todos[0].id, "100");
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
