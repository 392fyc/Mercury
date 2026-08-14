/**
 * 并发池与背压。
 *
 * 为什么必须有：SDK 的每次 `thread.run()` 背后都 spawn 一个 `codex exec` 子进程
 * （SDK README 原文：spawns the CLI and exchanges JSONL events over stdin/stdout）。
 * 不限流就是直接按待办数量开进程 —— Claude Code 的 runtime 有 min(16, cpu-2) 兜底，
 * Codex 侧没有任何东西替你做这件事。
 */

import os from 'node:os';

/** 默认并发：与 Claude Code runtime 的兜底口径对齐，留两个核给系统与 CLI 自身。 */
export function defaultConcurrency() {
  const cores = os.cpus()?.length || 4;
  return Math.max(1, Math.min(16, cores - 2));
}

/**
 * 把并发参数收敛成一个正整数。
 *
 * 必须显式挡住 NaN：`Math.max(1, Math.min(NaN, n))` 的结果仍是 NaN，
 * 而 `Array.from({ length: NaN })` 会产生**零个** worker —— 于是整批任务
 * 一个都不执行、也不报错，上层只看到一串 null。这种「静默什么都没做」
 * 比直接抛错危险得多。
 */
function normalizeConcurrency(v) {
  const n = Math.floor(Number(v));
  if (!Number.isFinite(n) || n < 1) return defaultConcurrency();
  return Math.min(n, 1024);
}

/**
 * 按需拉取式并发映射。
 *
 * **真的按需**：worker 直接从迭代器 `next()` 取下一项，不预先 `Array.from` 物化。
 * 早先的写法先把整个 Iterable 展平成数组、再按总长度预分配结果槽位，那样
 * 「背压」只作用于任务执行、对输入完全没有约束：超大输入会在第一个 worker 启动前
 * 就全部驻留内存，无限迭代器则永远进不到执行阶段。JS 是单线程的，多个 worker
 * 调 `it.next()` 不会交错，所以这里不需要额外加锁。
 *
 * @param {Iterable} items
 * @param {(item:any, index:number) => Promise<any>} fn
 * @param {object} opts
 * @param {number} [opts.concurrency]
 * @param {AbortSignal} [opts.signal]
 * @returns {Promise<Array<{ok:boolean, value?:any, error?:any, index:number}>>}
 *   顺序与输入一致。**不会 reject** —— 单项失败以 ok:false 回报，
 *   失败隔离是这一层的职责，不能让一个坏项掀翻整批。
 */
export async function mapWithPool(items, fn, { concurrency, signal } = {}) {
  const it = items[Symbol.iterator]();
  const results = [];
  let cursor = 0;

  const worker = async () => {
    for (;;) {
      if (signal?.aborted) return;
      const step = it.next();
      if (step.done) return;
      const i = cursor++;
      try {
        results[i] = { ok: true, value: await fn(step.value, i), index: i };
      } catch (error) {
        results[i] = { ok: false, error, index: i };
      }
    }
  };

  const n = normalizeConcurrency(concurrency);
  await Promise.all(Array.from({ length: n }, worker));
  // 中途取消会在 results 里留下空洞，填成失败项，免得调用方拿到 undefined。
  for (let i = 0; i < results.length; i++) {
    if (results[i] === undefined) results[i] = { ok: false, error: new Error('已取消'), index: i };
  }
  return results;
}

/**
 * 扇出上限：超出部分**必须**被显式报出来，不能静默截断。
 * 对齐 Mercury #385 的护栏 —— 现役脚本里 log() 的 77 次调用全部用在这件事上。
 *
 * 注意这里**会**物化输入：上限本身就要求知道总量才能报出「丢了多少」。
 * 所以它不适合套在无限或超大迭代器上；那种情况应当在上游先切片，
 * 或者直接把迭代器交给 mapWithPool（它不物化）。
 *
 * @returns {{kept: any[], dropped: number}}
 */
export function capFanout(items, cap, { log, what = '扇出', why = '超出上限' } = {}) {
  const list = Array.isArray(items) ? items : Array.from(items);
  if (!cap || list.length <= cap) return { kept: list, dropped: 0 };
  const kept = list.slice(0, cap);
  log?.dropped(what, kept.length, list.length, why);
  return { kept, dropped: list.length - kept.length };
}
