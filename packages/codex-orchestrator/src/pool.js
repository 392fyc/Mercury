/**
 * 并发池与背压。
 *
 * 为什么必须有：SDK 的每次 `thread.run()` 背后都 spawn 一个 `codex exec` 子进程
 * （SDK README 原文：spawns the CLI and exchanges JSONL events over stdin/stdout）。
 * 不限流就是直接按待办数量开进程 —— Claude Code 的 runtime 有 min(16, cpu-2) 兜底，
 * Codex 侧没有任何东西替你做这件事。
 *
 * 背压同理：不能先 `items.map(() => run(...))` 造出几百个 pending Promise 再交给池子，
 * 那样所有任务的输入都提前驻留内存，而且取消时要收拾一大堆已创建的对象。
 * 这里用「按需从迭代器拉取」的写法，池子里始终只有 concurrency 个任务在飞。
 */

import os from 'node:os';

/** 默认并发：与 Claude Code runtime 的兜底口径对齐，留两个核给系统与 CLI 自身。 */
export function defaultConcurrency() {
  const cores = os.cpus()?.length || 4;
  return Math.max(1, Math.min(16, cores - 2));
}

/**
 * 按需拉取式并发映射。
 *
 * @param {Iterable} items
 * @param {(item:any, index:number) => Promise<any>} fn
 * @param {object} opts
 * @param {number} opts.concurrency
 * @param {AbortSignal} [opts.signal]
 * @returns {Promise<Array<{ok:boolean, value?:any, error?:any, index:number}>>}
 *   顺序与输入一致。**不会 reject** —— 单项失败以 ok:false 回报，
 *   失败隔离是这一层的职责，不能让一个坏项掀翻整批。
 */
export async function mapWithPool(items, fn, { concurrency = defaultConcurrency(), signal } = {}) {
  const list = Array.isArray(items) ? items : Array.from(items);
  const results = new Array(list.length);
  let cursor = 0;

  const worker = async () => {
    for (;;) {
      if (signal?.aborted) return;
      const i = cursor++;
      if (i >= list.length) return;
      try {
        results[i] = { ok: true, value: await fn(list[i], i), index: i };
      } catch (error) {
        results[i] = { ok: false, error, index: i };
      }
    }
  };

  const n = Math.max(1, Math.min(concurrency, list.length));
  await Promise.all(Array.from({ length: n }, worker));
  return results;
}

/**
 * 扇出上限：超出部分**必须**被显式报出来，不能静默截断。
 * 对齐 Mercury #385 的护栏 —— 现役脚本里 log() 的 77 次调用全部用在这件事上。
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
