/**
 * 单个 agent 的执行封装。
 *
 * 这一层承担 Claude Code 的 `agent(prompt, opts)` 在 Codex 上缺失的全部职责：
 *
 * 1. **结构化返回**。SDK 的 `outputSchema` 只是把 schema 传给模型，
 *    回来的 `finalResponse` 仍然是**字符串**，SDK 既不 `JSON.parse` 也不校验。
 *    而现役 workflow 脚本里遍布 `(r && r.findings) || []` 这类写法 ——
 *    它们全都建立在「返回值一定是合法对象」的假设上。所以 parse + 校验 + 一次修复重试
 *    在这里是必需品，不是加分项。
 * 2. **超时**。`TurnOptions` 只有 `outputSchema` 和 `signal` 两个字段，
 *    好在 `signal` 直接吃 `AbortSignal.timeout(ms)`，所以超时很便宜。
 * 3. **重试与错误分类**（见 retry.js 的说明）。
 */

import { Codex } from '@openai/codex-sdk';
import { classify, backoffMs, sleep } from './retry.js';
import { errText } from './log.js';

/**
 * 把 schema 规范化成 OpenAI 结构化输出能接受的形状。
 *
 * 平台有两条硬要求，不满足直接被拒（实测报错：
 * "Invalid schema for response_format 'codex_output_schema': 'additionalProperties'
 * is required to be supplied and to be false."）：
 *   1. 每一层 type:object **必须**显式写 `additionalProperties: false`；
 *   2. 严格模式下 `required` 必须列出该层的**全部** properties。
 *
 * 这类平台约束应当由编排层吸收掉 —— 让每个调用方自己记得写，迟早会漏，
 * 而漏了的表现是整个 agent 调用失败、错误信息还藏在一层 JSON 里。
 */
export function normalizeSchema(node) {
  if (node === null || typeof node !== 'object') return node;
  if (Array.isArray(node)) return node.map(normalizeSchema);
  const out = {};
  for (const [k, v] of Object.entries(node)) out[k] = normalizeSchema(v);
  if (out.type === 'object' || out.properties) {
    if (out.additionalProperties === undefined) out.additionalProperties = false;
    if (out.properties && !out.required) out.required = Object.keys(out.properties);
  }
  return out;
}

/** 轻量 schema 校验：只查顶层必需字段与类型。 */
function validate(obj, schema) {
  if (!schema || typeof schema !== 'object') return null;
  if (obj === null || typeof obj !== 'object') return '返回值不是对象';
  const required = schema.required || [];
  const missing = required.filter((k) => !(k in obj));
  if (missing.length) return `缺少必需字段: ${missing.join(', ')}`;
  const props = schema.properties || {};
  for (const [k, spec] of Object.entries(props)) {
    if (!(k in obj) || spec == null || !spec.type) continue;
    const v = obj[k];
    const t = spec.type;
    const okType =
      t === 'array' ? Array.isArray(v)
      : t === 'object' ? v !== null && typeof v === 'object' && !Array.isArray(v)
      : t === 'integer' ? Number.isInteger(v)
      : t === 'number' ? typeof v === 'number'
      : t === 'string' ? typeof v === 'string'
      : t === 'boolean' ? typeof v === 'boolean'
      : true;
    if (!okType) return `字段 ${k} 类型应为 ${t}`;
  }
  return null;
}

/** 模型有时会把 JSON 包在 ``` 围栏里或前后加解释文字，先剥再 parse。 */
function extractJson(text) {
  const t = String(text ?? '').trim();
  if (!t) throw new Error('返回内容为空');
  const fenced = t.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const body = fenced ? fenced[1].trim() : t;
  try {
    return JSON.parse(body);
  } catch {
    // 退一步：抓第一个完整的 {...} 或 [...]
    const m = body.match(/[{[][\s\S]*[}\]]/);
    if (!m) throw new Error('返回内容不是 JSON');
    return JSON.parse(m[0]);
  }
}

/**
 * 跑一个 agent。
 *
 * @param {string} prompt
 * @param {object} opts
 * @param {string} opts.label            日志里的名字
 * @param {object} [opts.schema]         传给模型的 JSON Schema；给了就强制结构化返回
 * @param {string} [opts.model]          不给则继承会话模型
 * @param {string} [opts.effort]         minimal|low|medium|high|xhigh
 * @param {string} [opts.sandbox]        read-only|workspace-write|full-access
 * @param {string} [opts.cwd]            工作目录
 * @param {number} [opts.timeoutMs]      单次尝试的超时
 * @param {number} [opts.maxAttempts]    含首次在内的总尝试次数
 * @param {boolean} [opts.web]           是否开实时联网检索
 * @param {RunLog} opts.log
 * @param {AbortSignal} [opts.signal]
 * @returns {Promise<any|null>} 失败返回 null（失败隔离），不抛
 */
export async function runAgent(prompt, opts = {}) {
  const {
    label = 'agent', schema = null, model, effort, sandbox = 'read-only',
    cwd, timeoutMs = 600000, maxAttempts = 3, web = false, log, signal,
  } = opts;

  const wireSchema = schema ? normalizeSchema(schema) : null;
  const codex = new Codex();
  const threadOptions = { sandboxMode: sandbox, skipGitRepoCheck: true };
  if (model) threadOptions.model = model;
  if (effort) threadOptions.modelReasoningEffort = effort;
  if (cwd) threadOptions.workingDirectory = cwd;
  if (web) { threadOptions.webSearchMode = 'live'; threadOptions.networkAccessEnabled = true; }

  let lastReason = '未知';
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    if (signal?.aborted) return null;
    log?.agentStarted(label, attempt);
    const t0 = Date.now();
    try {
      const thread = codex.startThread(threadOptions);
      const timeout = AbortSignal.timeout(timeoutMs);
      const merged = signal ? AbortSignal.any([signal, timeout]) : timeout;

      // 第一次给原始 prompt；若上一轮是「格式不对」，追加一句修复指令再试一次。
      const text = attempt > 1 && lastReason.startsWith('格式')
        ? `${prompt}\n\n上一次回复不符合要求：${lastReason}。请只输出符合 schema 的 JSON，不要任何解释文字或代码围栏。`
        : prompt;

      const turn = await thread.run(text, wireSchema ? { outputSchema: wireSchema, signal: merged } : { signal: merged });
      const ms = Date.now() - t0;

      if (!schema) {
        log?.agentOk(label, ms, turn.usage);
        return turn.finalResponse;
      }

      let parsed;
      try {
        parsed = extractJson(turn.finalResponse);
      } catch (e) {
        lastReason = `格式错误（${errText(e)}）`;
        log?.agentRetry(label, attempt, lastReason);
        continue;
      }
      const bad = validate(parsed, schema);
      if (bad) {
        lastReason = `格式错误（${bad}）`;
        log?.agentRetry(label, attempt, lastReason);
        continue;
      }
      log?.agentOk(label, ms, turn.usage);
      return parsed;
    } catch (err) {
      const reason = errText(err);
      const c = classify(err);
      // 未识别的错误要留痕：它是补分类规则的唯一线索。
      if (!c.matched) log?.emit('error.unclassified', { label, message: reason });
      lastReason = reason;
      if (!c.retryable || attempt >= maxAttempts) {
        log?.agentFailed(label, `${reason}（${c.why}）`, attempt);
        return null;
      }
      log?.agentRetry(label, attempt, `${reason}（${c.why}）`);
      try {
        await sleep(backoffMs(attempt), signal);
      } catch {
        return null; // 退避期间被取消
      }
    }
  }
  log?.agentFailed(label, lastReason, maxAttempts);
  return null;
}
