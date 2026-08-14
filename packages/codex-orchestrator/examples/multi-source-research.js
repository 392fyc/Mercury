/**
 * 用编排层重建 `.claude/workflows/mercury-multi-source-research.js`（Issue #571 / G6-2、G4-1）。
 *
 * 它替代的是 Claude 侧的 autoresearch：**同一个问题从几个互不相同的检索角度并行去查，
 * 再把每条关键论断单独拿出来交叉核实**。比一路串行搜索强的地方不在于快，
 * 而在于单一角度会系统性地漏掉某类来源，而漏掉的部分你不会知道自己漏了。
 *
 * 保留原脚本的三条护栏：
 *   - 每条论断标注证据等级，**查不到就标 UNVERIFIED，不允许用推测填空**（项目硬规则）；
 *   - 角度数量有上限，超出显式记录；
 *   - 只读，不写任何文件。
 *
 * 跑法：
 *   node packages/codex-orchestrator/examples/multi-source-research.js "要查的问题"
 *   node packages/codex-orchestrator/examples/multi-source-research.js "问题" --angles 3
 */

import { RunLog, runAgent, parallel, capFanout } from '../src/index.js';

const argv = process.argv.slice(2);
const argOf = (k, d) => {
  const i = argv.indexOf(`--${k}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};
const question = argv.filter((a, i) => !a.startsWith('--') && !(i > 0 && argv[i - 1].startsWith('--')))[0];

if (!question) {
  console.error('用法: node examples/multi-source-research.js "要查的问题" [--angles N]');
  process.exit(2);
}

const ANGLE_CAP = Math.max(1, Math.min(Number(argOf('angles', 2)), 5));
const CLAIM_CAP = Math.max(1, Math.min(Number(argOf('claims', 3)), 12));

const log = new RunLog({ file: process.env.ORCH_LOG || '.mercury/state/orchestrator-research.jsonl', runId: 'research' });

const ALL_ANGLES = [
  { key: '官方文档', lens: '厂商官方文档、官方 changelog、官方仓库的 release notes。这是最高等级的证据。' },
  { key: '一手实测', lens: '本机可执行的验证：命令行 --help、版本查询、注册表/配置文件、实际跑一次看输出。' },
  { key: '社区与issue', lens: '官方仓库的 issue 与 discussion、社区报告的已知问题与绕法。注意区分「有人这么说」与「已证实」。' },
  { key: '同类对照', lens: '同类工具或前代版本是怎么做的，用来判断某个行为是通例还是特例。' },
];

const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['claims'],
  properties: {
    claims: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'evidence', 'level'],
        properties: {
          claim: { type: 'string' },
          evidence: { type: 'string' },      // 来源 URL 或可复现的命令
          level: { type: 'string' },         // OFFICIAL | MEASURED | COMMUNITY | UNVERIFIED
        },
      },
    },
  },
};

const CHECK_SCHEMA = {
  type: 'object',
  required: ['holds', 'level', 'note'],
  properties: {
    holds: { type: 'boolean' },
    level: { type: 'string' },
    note: { type: 'string' },
  },
};

const { kept: angles } = capFanout(ALL_ANGLES, ANGLE_CAP, {
  log, what: '检索角度', why: `超过 --angles ${ANGLE_CAP}`,
});

log.say(`问题：${question}`);
log.say(`${angles.length} 个角度并行检索`);

// —— 多角度并行检索：各角度互不可见，避免结论互相污染 ——
const swept = await parallel(
  angles.map((a) => () =>
    runAgent(
      `就下面这个问题做检索，**只走「${a.lens}」这一条路**，不要泛泛而谈。\n\n问题：${question}\n\n` +
        `每条论断都要给出 evidence（来源 URL，或本机可复现的命令）与 level：\n` +
        `OFFICIAL（厂商官方文档/changelog）> MEASURED（本机实测）> COMMUNITY（issue/社区报告）> UNVERIFIED（查不到）。\n` +
        `**查不到就老实标 UNVERIFIED，绝不允许用推测填空** —— 这是项目硬规则。最多 ${CLAIM_CAP} 条。只读。`,
      { label: `检索:${a.key}`, schema: FINDINGS_SCHEMA, sandbox: 'read-only', effort: 'medium', web: true, cwd: process.cwd(), timeoutMs: 900000, log }
    ).then((r) => (r ? { angle: a.key, claims: r.claims || [] } : null))
  ),
  { concurrency: angles.length }
);

const alive = swept.filter(Boolean);
if (!alive.length) {
  log.say('所有角度均未返回，中止');
  log.summary();
  process.exit(1);
}

// —— 逐条交叉核实：每条论断由一个独立 agent 用**别的**途径去验 ——
const allClaims = alive.flatMap((r) => r.claims.map((c) => ({ ...c, from: r.angle })));
const { kept: toCheck } = capFanout(allClaims, CLAIM_CAP * 2, {
  log, what: '待核实论断', why: `超过 ${CLAIM_CAP * 2}`,
});

const checked = await parallel(
  toCheck.map((c) => () =>
    runAgent(
      `有人给出这样一条论断，请你**独立核实**它 —— 不要沿用它给的来源，自己另找途径验证。\n\n` +
        `论断：${c.claim}\n对方给的依据：${c.evidence}（自评等级 ${c.level}）\n\n` +
        `按 schema 返回 holds（是否成立）、level（你核实后的等级）、note（核实经过与你找到的依据）。` +
        `核不到就把 level 标成 UNVERIFIED 并说明卡在哪，不要为了给结论而猜。只读。`,
      { label: `核实:${c.claim.slice(0, 18)}`, schema: CHECK_SCHEMA, sandbox: 'read-only', effort: 'medium', web: true, cwd: process.cwd(), timeoutMs: 900000, log }
    ).then((v) => (v ? { ...c, verified: v } : null))
  ),
  { concurrency: Math.min(4, toCheck.length) }
);

const ok = checked.filter(Boolean);
const rank = { OFFICIAL: 0, MEASURED: 1, COMMUNITY: 2, UNVERIFIED: 3 };
const confirmed = ok.filter((c) => c.verified.holds).sort((a, b) => (rank[a.verified.level] ?? 9) - (rank[b.verified.level] ?? 9));
const refuted = ok.filter((c) => !c.verified.holds);

const s = log.summary();
console.log(JSON.stringify({
  question,
  confirmed: confirmed.map((c) => ({ claim: c.claim, level: c.verified.level, from: c.from, evidence: c.verified.note })),
  refuted: refuted.map((c) => ({ claim: c.claim, from: c.from, why: c.verified.note })),
  unverifiedCount: confirmed.filter((c) => c.verified.level === 'UNVERIFIED').length,
  stats: s,
}, null, 2));
