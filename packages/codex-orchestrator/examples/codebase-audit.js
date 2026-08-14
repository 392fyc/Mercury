/**
 * 用编排层重建 `.claude/workflows/mercury-codebase-audit.js`（Issue #571 / G6-2）。
 *
 * 保留原脚本的全部结构性特征，用来证明这一层真能替代 Dynamic Workflow：
 *   - Discover 阶段先盘点，再按盘点结果扇出
 *   - **每个维度审计完立刻进入对抗验证，不等其他维度**（pipeline 的无屏障特性）
 *   - 每条 finding 由独立 agent 逐条反驳（嵌套 parallel）
 *   - 扇出上限 + 被丢弃工作量显式记录（Mercury #385 护栏）
 *   - 结构化 schema 贯穿始终
 *
 * 与原脚本的差异只有一处是本质的：原脚本跑在会话里、进度出现在 /workflows 面板，
 * 这里是一个外部 Node 脚本，可观测面是 JSONL 日志。这是架构位置的差异，不是能力差异。
 *
 * 跑法（默认只审 packages/codex-orchestrator 自己，成本可控）：
 *   node packages/codex-orchestrator/examples/codebase-audit.js
 *   node packages/codex-orchestrator/examples/codebase-audit.js --target scripts --dims 3
 */

import { RunLog, runAgent, parallel, pipeline, capFanout } from '../src/index.js';

const argv = process.argv.slice(2);
const argOf = (k, d) => {
  const i = argv.indexOf(`--${k}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};

const TARGET = argOf('target', 'packages/codex-orchestrator/src');
const DIM_CAP = Math.max(1, Math.min(Number(argOf('dims', 2)), 6));
const MODULE_CAP = Math.max(1, Math.min(Number(argOf('modules', 6)), 40));
const FINDING_CAP = Math.max(1, Math.min(Number(argOf('findings', 3)), 20));

const ALL_DIMENSIONS = [
  { key: 'correctness', ask: '逻辑错误、边界条件、错误处理遗漏、竞态' },
  { key: 'resource', ask: '资源泄漏、未清理的句柄与子进程、无界增长的集合' },
  { key: 'security', ask: '注入面、凭据处理、路径穿越、权限过宽' },
  { key: 'maintainability', ask: '重复逻辑、隐式耦合、命名与实现不符、死代码' },
  { key: 'perf', ask: '不必要的串行、重复计算、热路径上的同步阻塞' },
  { key: 'testability', ask: '难以测试的结构、隐藏的全局状态、缺少接缝' },
];

const log = new RunLog({ file: process.env.ORCH_LOG || '.mercury/state/orchestrator-audit.jsonl', runId: 'audit' });

const INVENTORY_SCHEMA = {
  type: 'object',
  required: ['modules'],
  properties: {
    modules: {
      type: 'array',
      items: {
        type: 'object',
        required: ['path', 'role'],
        properties: { path: { type: 'string' }, role: { type: 'string' } },
      },
    },
  },
};

const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['file', 'severity', 'summary', 'why'],
        properties: {
          file: { type: 'string' },
          severity: { type: 'string' },
          summary: { type: 'string' },
          why: { type: 'string' },
        },
      },
    },
  },
};

const VERDICT_SCHEMA = {
  type: 'object',
  required: ['real', 'reason'],
  properties: { real: { type: 'boolean' }, reason: { type: 'string' } },
};

// —— Discover ——
log.say(`盘点 ${TARGET}`);
const inventory = await runAgent(
  `列出 ${TARGET} 目录下的源文件及各自职责。只读，不要修改任何东西。按 schema 输出。`,
  { label: 'discover', schema: INVENTORY_SCHEMA, sandbox: 'read-only', effort: 'low', cwd: process.cwd(), log }
);

if (!inventory) {
  log.say('盘点失败，中止');
  log.summary();
  process.exit(1);
}

const { kept: modules } = capFanout(inventory.modules, MODULE_CAP, {
  log, what: '模块', why: `超过 --modules ${MODULE_CAP}`,
});
const { kept: dims } = capFanout(ALL_DIMENSIONS, DIM_CAP, {
  log, what: '审计维度', why: `超过 --dims ${DIM_CAP}`,
});

const moduleList = modules.map((m) => `- ${m.path}（${m.role}）`).join('\n');
log.say(`审计 ${modules.length} 个模块 × ${dims.length} 个维度`);

// —— 审计 → 对抗验证（每个维度独立流过两个阶段，维度之间不设屏障）——
const perDim = await pipeline(
  dims,
  (dim) =>
    runAgent(
      `审计下列文件，只看这一个维度：**${dim.ask}**。\n\n${moduleList}\n\n` +
        `逐条给出 file / severity（high|medium|low）/ summary / why。` +
        `最多 ${FINDING_CAP} 条，宁缺毋滥——找不到真问题就返回空数组。只读，不要修改任何文件。`,
      { label: `审计:${dim.key}`, schema: FINDINGS_SCHEMA, sandbox: 'read-only', effort: 'medium', cwd: process.cwd(), log }
    ),
  (review, dim) => {
    const { kept } = capFanout(review.findings || [], FINDING_CAP, {
      log, what: `${dim.key} 的 finding`, why: `超过 --findings ${FINDING_CAP}`,
    });
    if (!kept.length) return [];
    // 每条 finding 派一个独立 agent 去**反驳**它 —— 默认立场是「这条不成立」，
    // 这样能滤掉「读起来有道理但其实不成立」的结论。
    return parallel(
      kept.map((f) => () =>
        runAgent(
          `有人在 ${f.file} 提出这样一条问题：「${f.summary}」，理由是「${f.why}」。\n\n` +
            `请去读这个文件，尽力**反驳**它。默认立场是这条不成立，只有确实找到证据才承认。` +
            `按 schema 返回 real（是否真实存在）与 reason。只读。`,
          { label: `验证:${dim.key}:${f.file.split(/[\\/]/).pop()}`, schema: VERDICT_SCHEMA, sandbox: 'read-only', effort: 'medium', cwd: process.cwd(), log }
        ).then((v) => (v && v.real ? { ...f, dimension: dim.key, reason: v.reason } : null))
      )
    );
  }
);

// —— Report ——
const confirmed = perDim.flat().filter(Boolean);
const rank = { high: 0, medium: 1, low: 2 };
confirmed.sort((a, b) => (rank[a.severity] ?? 9) - (rank[b.severity] ?? 9));

const s = log.summary();
console.log(JSON.stringify({ target: TARGET, confirmed, stats: s }, null, 2));
