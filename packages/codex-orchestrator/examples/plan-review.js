/**
 * 用编排层重建 `.claude/workflows/mercury-adversarial-plan-review.js`（Issue #571 / G6-2）。
 *
 * 保留原脚本的三段结构与其核心主张：**一次起草 + 反复自我修改，不如多路独立起草 + 对抗评审**，
 * 因为评审团能暴露单个起草者看不见的失败模式。
 *
 * 与原脚本一致的护栏：
 *   - 精简派发：给起草者任务陈述 + 要读的文件路径，**不注入文件全文**（#385）。
 *   - 角度数量有上限，超出显式记录。
 *   - 只产计划、不实现 —— 实现仍回主循环派发（不自我批准）。
 *
 * 跑法：
 *   node packages/codex-orchestrator/examples/plan-review.js "要决策的问题"
 *   node packages/codex-orchestrator/examples/plan-review.js "问题" --angles 3 --read a.md,b.md
 */

import { RunLog, runAgent, parallel, capFanout } from '../src/index.js';

const argv = process.argv.slice(2);
const argOf = (k, d) => {
  const i = argv.indexOf(`--${k}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const task = argv.filter((a, i) => !a.startsWith('--') && !(i > 0 && argv[i - 1].startsWith('--')))[0];

if (!task) {
  console.error('用法: node examples/plan-review.js "要决策的问题" [--angles N] [--read 路径,路径]');
  process.exit(2);
}

const ANGLE_CAP = Math.max(1, Math.min(Number(argOf('angles', 2)), 6));
const readPaths = String(argOf('read', '')).split(',').filter(Boolean);
const ctxLine = readPaths.length
  ? `\n\n可以读这些文件了解背景（自己去读，不要假设内容）：${readPaths.join('、')}`
  : '';

const log = new RunLog({ file: process.env.ORCH_LOG || '.mercury/state/orchestrator-plan.jsonl', runId: 'plan' });

const ALL_ANGLES = [
  { key: '最小可行', lens: '先交付最小的正确解，能推迟的一律推迟' },
  { key: '风险优先', lens: '把影响半径和不可逆性降到最低，优先考虑回滚与护栏' },
  { key: '使用者优先', lens: '优化最终使用者与运维者的体验，以及可观察到的行为' },
  { key: '可维护优先', lens: '优化长期可维护性、模块化与可拆卸性' },
];

const PLAN_SCHEMA = {
  type: 'object',
  required: ['approach', 'steps', 'risks'],
  properties: {
    approach: { type: 'string' },
    steps: { type: 'array', items: { type: 'string' } },
    risks: { type: 'array', items: { type: 'string' } },
  },
};

const SCORE_SCHEMA = {
  type: 'object',
  required: ['score', 'fatal', 'critique', 'salvage'],
  properties: {
    score: { type: 'integer' },
    fatal: { type: 'boolean' },
    critique: { type: 'string' },
    salvage: { type: 'string' },
  },
};

const { kept: angles } = capFanout(ALL_ANGLES, ANGLE_CAP, {
  log, what: '起草角度', why: `超过 --angles ${ANGLE_CAP}`,
});

log.say(`问题：${task}`);
log.say(`${angles.length} 个角度独立起草`);

// —— Draft：各角度互不可见地起草 ——
const drafts = await parallel(
  angles.map((a) => () =>
    runAgent(
      `你要为下面这个问题拟一个方案，**只从「${a.lens}」这一个视角出发**。\n\n问题：${task}${ctxLine}\n\n` +
        `给出 approach（一句话说清主张）、steps（具体步骤）、risks（这个方案自身的风险，要诚实）。只读，不要修改任何文件。`,
      { label: `起草:${a.key}`, schema: PLAN_SCHEMA, sandbox: 'read-only', effort: 'medium', cwd: process.cwd(), log }
    ).then((p) => (p ? { angle: a.key, ...p } : null))
  )
);

const valid = drafts.filter(Boolean);
if (!valid.length) {
  log.say('没有任何角度产出有效方案，中止');
  log.summary();
  process.exit(1);
}

// —— Judge：每份方案由独立评审对抗式打分（默认立场是挑毛病，不是捧场）——
const judged = await parallel(
  valid.map((d) => () =>
    runAgent(
      `有人为这个问题提了一个方案，请你**对抗式评审**它 —— 默认立场是「它有问题」，尽力找出它会在哪里失败。\n\n` +
        `问题：${task}\n\n方案（来自「${d.angle}」视角）：\n主张：${d.approach}\n步骤：\n` +
        d.steps.map((s, i) => `  ${i + 1}. ${s}`).join('\n') +
        `\n自陈风险：${d.risks.join('；')}${ctxLine}\n\n` +
        `按 schema 返回：score（0-10）、fatal（是否有致命缺陷）、critique（最要害的问题）、salvage（即使整体不可取，哪一部分值得保留）。只读。`,
      { label: `评审:${d.angle}`, schema: SCORE_SCHEMA, sandbox: 'read-only', effort: 'medium', cwd: process.cwd(), log }
    ).then((v) => (v ? { ...d, ...v } : null))
  )
);

const scored = judged.filter(Boolean).sort((a, b) => b.score - a.score);
if (!scored.length) {
  log.say('评审全部失败，只能给出未评审的草案');
  console.log(JSON.stringify({ task, drafts: valid, note: '评审阶段全部失败' }, null, 2));
  log.summary();
  process.exit(1);
}

// —— Synthesize：以优胜方案为底，嫁接落选方案里值得保留的部分 ——
const winner = scored[0];
const others = scored.slice(1);
const synthesis = await runAgent(
  `下面是同一个问题的多个方案与各自的对抗式评审结论。请综合出**一个**最终方案。\n\n问题：${task}\n\n` +
    `得分最高的（${winner.angle}，${winner.score}/10${winner.fatal ? '，但评审认为有致命缺陷' : ''}）：\n` +
    `主张：${winner.approach}\n步骤：\n${winner.steps.map((s, i) => `  ${i + 1}. ${s}`).join('\n')}\n` +
    `评审的要害批评：${winner.critique}\n\n` +
    (others.length
      ? `其余方案与评审认为值得保留的部分：\n` +
        others.map((o) => `- ${o.angle}（${o.score}/10）：${o.approach}\n  值得保留：${o.salvage}\n  要害批评：${o.critique}`).join('\n') + '\n\n'
      : '') +
    `以最高分方案为底，**把评审指出的要害批评正面处理掉**，并嫁接其余方案里值得保留的部分。\n` +
    `给出 approach / steps / risks。risks 里要写清综合之后仍然存在的残余风险 —— 不要假装都解决了。只读。`,
  { label: '综合', schema: PLAN_SCHEMA, sandbox: 'read-only', effort: 'high', cwd: process.cwd(), log }
);

const s = log.summary();
console.log(JSON.stringify({ task, winner: { angle: winner.angle, score: winner.score }, scored: scored.map((x) => ({ angle: x.angle, score: x.score, fatal: x.fatal })), synthesis, stats: s }, null, 2));
