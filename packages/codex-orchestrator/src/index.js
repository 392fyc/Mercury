/**
 * Mercury 在 Codex 上的确定性多 agent 编排层。
 *
 * 替代的是 Claude Code 的 Dynamic Workflow —— Codex 没有等价机制：
 * `enable_fanout` 在 CLI 0.147.0 上已标 removed，而它的 subagent 由**模型自主决定**
 * 何时 spawn，不是脚本指定拓扑。所以确定性扇出只能由外层脚本自己起进程。
 *
 * 与 Dynamic Workflow 的对应关系：
 *   agent(prompt, opts)          → runAgent(prompt, opts)
 *   parallel(thunks)             → parallel(thunks, opts)
 *   pipeline(items, ...stages)   → pipeline(items, ...stages)
 *   log()                        → RunLog#dropped / #say
 *   runtime 的并发与总量兜底      → pool.js + capFanout
 *
 * 刻意不做的三样（现役 7 个脚本里使用次数均为 0，做了是白做）：
 *   - budget.total/spent()/remaining() 的 token 预算对象
 *   - 断点续跑的结果记忆化缓存
 *   - worktree 隔离（large-migration 自己写明刻意避开：runtime 会自动删除隔离
 *     worktree 且合并回写语义未文档化）
 * 需要时再补，不要为了「等价」而先写。
 *
 * 用法见 README.md。
 */

export { RunLog, errText } from './log.js';
export { classify, backoffMs, sleep } from './retry.js';
export { defaultConcurrency, mapWithPool, capFanout } from './pool.js';
export { runAgent } from './agent.js';

import { mapWithPool, defaultConcurrency } from './pool.js';

/** 参数尾部若不是函数，视为选项对象。 */
function splitStages(args) {
  const stages = [...args];
  let opts = {};
  if (stages.length && typeof stages[stages.length - 1] !== 'function') {
    opts = stages.pop() || {};
  }
  return { stages, opts };
}

/**
 * 并发执行一批 thunk 并等待全部完成 —— 这是**屏障**。
 * 失败的项返回 null 而不是抛出，与 Claude Code 的 parallel() 语义一致，
 * 所以调用方照旧 `.filter(Boolean)`。
 *
 * 只有当下一步真的需要「所有结果同时在手」时才用它（跨条目去重、合并、
 * 早退判断）。否则用 pipeline —— 屏障会让快的项白等慢的项。
 *
 * @param {Array<() => Promise<any>>} thunks
 * @param {{concurrency?:number, signal?:AbortSignal}} [opts]
 */
export async function parallel(thunks, opts = {}) {
  const settled = await mapWithPool(thunks, (t) => t(), {
    concurrency: opts.concurrency ?? defaultConcurrency(),
    signal: opts.signal,
  });
  return settled.map((r) => (r && r.ok ? r.value : null));
}

/**
 * 每个条目独立流过全部阶段，**阶段之间没有屏障**。
 *
 * 这一点常被误解成需要一个专门的调度器，其实不需要：它等价于
 * `mapWithPool(items, item => stage1(item).then(r => stage2(r, item)))` 配一个共享并发池。
 * 「无屏障」是共享池的自然结果 —— 条目 B 的第一阶段和条目 A 的第二阶段
 * 本来就在同一个池子里竞争槽位，谁先就绪谁先跑。
 *
 * 每个阶段收到 (上一阶段结果, 原始条目, 序号)。
 * 任一阶段返回 null/undefined 或抛错，该条目就此丢弃、后续阶段不再执行，
 * 最终结果里留一个 null（失败隔离，不影响其他条目）。
 *
 * @param {Iterable} items
 * @param {...(Function|object)} stagesAndOpts 末位可传 {concurrency, signal}
 */
export async function pipeline(items, ...stagesAndOpts) {
  const { stages, opts } = splitStages(stagesAndOpts);
  const settled = await mapWithPool(
    items,
    async (item, i) => {
      let cur = item;
      for (const stage of stages) {
        cur = await stage(cur, item, i);
        if (cur === null || cur === undefined) return null;
      }
      return cur;
    },
    { concurrency: opts.concurrency ?? defaultConcurrency(), signal: opts.signal }
  );
  return settled.map((r) => (r && r.ok ? r.value : null));
}
