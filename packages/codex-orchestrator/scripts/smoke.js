/**
 * 冒烟测试：用真实的 Codex 调用验证编排层的四件核心事。
 *
 * 刻意设计成尽量便宜 —— prompt 与 schema 都极简，因为每次调用都要重付一份
 * 基础上下文（实测一句琐碎 prompt 就吃掉约 1.4 万 input token），
 * 这是 Codex 上扇出的固定成本，验证脚本没必要在这上面浪费。
 *
 * 跑法：node packages/codex-orchestrator/scripts/smoke.js
 */

import { RunLog, runAgent, parallel, pipeline, classify, capFanout } from '../src/index.js';

const LOG_FILE = process.env.ORCH_SMOKE_LOG || null;
const log = new RunLog({ file: LOG_FILE, runId: 'smoke' });

const SCHEMA = {
  type: 'object',
  required: ['n', 'word'],
  properties: { n: { type: 'integer' }, word: { type: 'string' } },
};

const ask = (i) =>
  runAgent(
    `只输出一个 JSON 对象，形如 {"n": ${i}, "word": "alpha${i}"}。不要任何解释、不要代码围栏。`,
    { label: `probe-${i}`, schema: SCHEMA, sandbox: 'read-only', effort: 'low', timeoutMs: 180000, log }
  );

function check(name, cond, detail = '') {
  const mark = cond ? 'PASS' : 'FAIL';
  console.log(`  [${mark}] ${name}${detail ? ' — ' + detail : ''}`);
  return cond;
}

const results = [];

// —— 1. 纯本地单元检查（不花钱，先跑，坏了就没必要往下烧 token）——
console.log('\n本地检查：');
results.push(check('未识别错误默认不重试', classify(new Error('某种没见过的错误')).retryable === false));
results.push(check('限流被判为可重试', classify(new Error('rate limit exceeded')).retryable === true));
results.push(check('认证失败被判为不可重试', classify(new Error('unauthorized')).retryable === false));
{
  const { kept, dropped } = capFanout([1, 2, 3, 4, 5], 3, { log, what: '扇出上限自检' });
  results.push(check('扇出上限生效且丢弃被记录', kept.length === 3 && dropped === 2));
}
{
  const out = await pipeline([1, 2, 3], (x) => x * 2, (x) => (x === 4 ? null : x + 1));
  results.push(check('pipeline 逐条失败隔离', JSON.stringify(out) === '[3,null,7]', JSON.stringify(out)));
}
{
  const out = await parallel([async () => 'a', async () => { throw new Error('boom'); }, async () => 'c']);
  results.push(check('parallel 失败返 null 而非抛出', JSON.stringify(out) === '["a",null,"c"]', JSON.stringify(out)));
}

// —— 2. 真实调用 ——
console.log('\n真实 Codex 调用：');
const t0 = Date.now();
const three = await parallel([() => ask(1), () => ask(2), () => ask(3)], { concurrency: 3 });
const wall = Date.now() - t0;

const alive = three.filter(Boolean);
const allThree = alive.length === 3;
results.push(check('三路并发全部返回结构化对象', allThree, JSON.stringify(three)));
// 前置条件不成立时必须判 FAIL，不能对空数组做 every —— 那是永真，
// 会让一次全线失败看起来像通过。
results.push(
  check('schema 字段正确', allThree && three.every((r, i) => r && r.n === i + 1 && typeof r.word === 'string'),
    allThree ? '' : '前置未满足：三路未全部返回')
);

const single0 = Date.now();
const one = await ask(9);
const singleMs = Date.now() - single0;
const singleOk = !!(one && one.n === 9);
results.push(check('单次调用可用', singleOk, JSON.stringify(one)));
// 同理：三路与单次都得真跑成功，比较耗时才有意义；否则比的是两次失败的速度。
results.push(
  check('并发确有重叠（3 路耗时 < 单次 × 2.5）', allThree && singleOk && wall < singleMs * 2.5,
    allThree && singleOk
      ? `3 路 ${(wall / 1000).toFixed(1)}s vs 单次 ${(singleMs / 1000).toFixed(1)}s`
      : '前置未满足：调用未全部成功，耗时比较无意义')
);

const s = log.summary();
const passed = results.filter(Boolean).length;
console.log(`\n${passed}/${results.length} 项通过；token 输入 ${s.usage.input} / 输出 ${s.usage.output}`);
process.exit(passed === results.length ? 0 : 1);
