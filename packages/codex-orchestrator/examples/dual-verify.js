/**
 * Codex 版 dual-verify（Issue #571 / G4-2）。
 *
 * 旧定义是「Claude Code 深审 ‖ Codex 审计」，那预设了 Claude Code 是宿主。
 * Codex 成为唯一 harness 之后同一 harness 内自审不成立，所以两路改成：
 * **两个独立 spawn 的 subagent，用不同的 model_reasoning_effort 盲审同一改动，再比对结论。**
 *
 * 「盲」体现在两处：
 *   1. 两路并行启动，互相看不到对方的 prompt 与结论；
 *   2. 两路的审查角度不同（一路盯正确性与破坏面，一路盯规则合规与可维护性），
 *      不是同一份提示词跑两遍 —— 那样只会得到两份高度相关的答案，
 *      对「一个人看漏的东西」没有帮助。
 *
 * 判定采用 fail-closed：任一路给出阻断级结论，整体即 NEEDS-CHANGES；
 * 任一路调用失败，整体判 INCONCLUSIVE 而不是放行。
 *
 * 跑法：
 *   node packages/codex-orchestrator/examples/dual-verify.js            # 审未提交改动
 *   node packages/codex-orchestrator/examples/dual-verify.js --range develop..HEAD
 */

import { execFileSync } from 'node:child_process';
import { RunLog, runAgent, parallel } from '../src/index.js';

const argv = process.argv.slice(2);
const argOf = (k, d) => {
  const i = argv.indexOf(`--${k}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const RANGE = argOf('range', '');
const MAX_DIFF = Number(argOf('maxDiff', 60000));

const log = new RunLog({ file: process.env.ORCH_LOG || '.mercury/state/orchestrator-dualverify.jsonl', runId: 'dual-verify' });

// 取 diff。给审查者看的是**改动本身**，不是整个仓库 —— 精简派发（#385）。
let diff;
try {
  // 用 execFileSync 传参数数组，不经 shell —— RANGE 来自命令行，
  // 拼进命令字符串会让 shell 元字符被解释执行。
  const args = ['diff', RANGE || 'HEAD'];
  diff = execFileSync('git', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
} catch (e) {
  console.error('取 diff 失败:', e.message);
  process.exit(2);
}
if (!diff.trim()) {
  console.error('没有改动可审。用 --range 指定范围，例如 --range develop..HEAD');
  process.exit(2);
}

let truncated = false;
if (diff.length > MAX_DIFF) {
  // 超长必须显式报出来，不能静默截断后当成审完了。
  log.dropped('diff 字符', MAX_DIFF, diff.length, `超过 --maxDiff ${MAX_DIFF}`);
  diff = diff.slice(0, MAX_DIFF);
  truncated = true;
}

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['verdict', 'findings', 'summary'],
  properties: {
    verdict: { type: 'string' },          // PASS | NEEDS-CHANGES
    summary: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['severity', 'where', 'what', 'why'],
        properties: {
          severity: { type: 'string' },   // critical | high | medium | low
          where: { type: 'string' },
          what: { type: 'string' },
          why: { type: 'string' },
        },
      },
    },
  },
};

const LANES = [
  {
    key: 'A·正确性',
    effort: 'high',
    lens:
      '只看**这份改动会不会坏事**：逻辑错误、边界条件、错误处理遗漏、竞态、资源泄漏、' +
      '对既有行为的破坏、以及「改了 A 却没同步 B」这类平行遗漏。',
  },
  {
    key: 'B·合规与可维护',
    effort: 'medium',
    lens:
      '只看**这份改动合不合规、将来好不好维护**：是否违反仓库自己写下的规则（先去读 AGENTS.md 与 ' +
      '.mercury/docs/DIRECTION.md，不要凭印象）、注释与实现是否一致、是否引入了会静默失效的东西、' +
      '是否有「错了也不报错」而又没有专门断言守着的地方。',
  },
];

log.say(`两路盲审，diff ${diff.length} 字符${truncated ? '（已截断，见上方记录）' : ''}`);

// 必须显式给并发数：defaultConcurrency() 在双核机器上算出 1，
// 那样两路会串行执行 —— 结果看起来一样，但「两个独立 agent 并行盲审」的前提没了。
const results = await parallel(
  LANES.map((lane) => () =>
    runAgent(
      `你是代码审查者。审查下面这份 diff，**${lane.lens}**\n\n` +
        `只报真实问题，按严重度排序；找不到就返回空的 findings 并给 PASS。` +
        `不要提风格偏好，不要建议重构，不要复述改动做了什么。\n` +
        `verdict 只能是 PASS 或 NEEDS-CHANGES —— 有 critical 或 high 就必须是 NEEDS-CHANGES。\n\n` +
        (truncated ? '⚠️ 这份 diff 已被截断，末尾不完整，据此不要对未见部分下结论。\n\n' : '') +
        `\`\`\`diff\n${diff}\n\`\`\``,
      { label: `审查${lane.key}`, schema: VERDICT_SCHEMA, sandbox: 'read-only', effort: lane.effort, cwd: process.cwd(), timeoutMs: 900000, log }
    ).then((r) => (r ? { lane: lane.key, effort: lane.effort, ...r } : null))
  ),
  { concurrency: LANES.length }
);

const ok = results.filter(Boolean);

// fail-closed：有一路没跑成，就不能宣称通过。
let overall;
if (ok.length < LANES.length) {
  overall = 'INCONCLUSIVE';
} else if (truncated) {
  // 只审了 diff 的前缀，对未见部分没有任何结论 —— 这种情况下 PASS 是假的。
  overall = 'INCONCLUSIVE';
} else if (ok.some((r) => r.verdict !== 'PASS' || r.findings.some((f) => ['critical', 'high'].includes(f.severity)))) {
  overall = 'NEEDS-CHANGES';
} else {
  overall = 'PASS';
}

// 两路都提到的问题更可能是真的；只有一路提到的要人工判一下。
const norm = (f) => `${f.where}`.split(/[\\/]/).pop() + '|' + `${f.what}`.slice(0, 24);
const seen = new Map();
for (const r of ok) for (const f of r.findings) {
  const k = norm(f);
  seen.set(k, [...(seen.get(k) || []), r.lane]);
}
const bothLanes = [...seen.entries()].filter(([, lanes]) => lanes.length > 1).map(([k]) => k);

const s = log.summary();
console.log(JSON.stringify({
  overall,
  truncated,
  lanes: ok.map((r) => ({ lane: r.lane, effort: r.effort, verdict: r.verdict, findings: r.findings, summary: r.summary })),
  agreedOn: bothLanes,
  stats: s,
}, null, 2));

process.exit(overall === 'PASS' ? 0 : 1);
