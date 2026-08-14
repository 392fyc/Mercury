/**
 * 重试策略与错误分类。
 *
 * ⚠️ 这是整个编排层最脆弱的一环，原因在 SDK 那边：`thread.run()` 收到
 * `turn.failed` 事件后执行的是 `throw new Error(turnFailure.message)` ——
 * **只有一个 message 字符串，没有错误码、没有类型**。所以判断「这个错误值不值得重试」
 * 只能靠匹配错误文案，而 Codex 每改一次文案，这里就可能静默失效。
 *
 * 因此设计上做两件事：
 *   1. **默认不重试**。只有明确匹配到已知可重试模式才重试；
 *      未识别的错误一律当作永久失败。反过来（默认重试）的后果是对着一个
 *      不可重试的错误反复烧配额。
 *   2. **未识别的错误必须留痕**（classify 返回 matched:false，调用方记进日志），
 *      这样可以定期回看日志补规则，而不是等到某天发现重试全线失效。
 */

/**
 * 已知可重试的错误模式。
 * 每条都注明为什么可重试 —— 加新规则时照这个格式，避免日后没人知道某条为何存在。
 */
const RETRYABLE = [
  { re: /\brate.?limit/i, why: '限流，退避后通常可恢复' },
  { re: /\btoo many requests\b/i, why: '限流（HTTP 429 文案）' },
  { re: /\btimed? ?out\b/i, why: '超时，重试有意义' },
  { re: /\bECONNRESET\b|\bECONNREFUSED\b|\bETIMEDOUT\b|\bEPIPE\b/i, why: '网络层瞬时故障' },
  { re: /\bsocket hang up\b/i, why: '连接被中断' },
  { re: /\b5\d\d\b.*\b(server|error)\b|\binternal server error\b/i, why: '服务端 5xx' },
  { re: /\bservice unavailable\b/i, why: '服务端暂时不可用' },
  { re: /\bserver (is )?overloaded\b/i, why: 'app-server 过载（JSON-RPC -32001 的文案）' },
  { re: /\bstream (closed|ended) unexpectedly\b/i, why: 'JSONL 流中断' },
];

/**
 * 明确不该重试的模式 —— 即使它碰巧命中了上面的某条，也直接判永久失败。
 * 这一层防的是「配置错了却被当成瞬时故障反复重试」。
 */
const FATAL = [
  { re: /\bnot (found|installed)\b.*\bcodex\b|\bcodex\b.*\bnot (found|installed)\b/i, why: 'CLI 不存在' },
  { re: /\bunauthorized\b|\b401\b|\binvalid api key\b/i, why: '认证失败，重试无意义' },
  { re: /\bforbidden\b|\b403\b/i, why: '权限不足' },
  { re: /\bnot a git repository\b/i, why: '工作目录不对' },
  { re: /\binvalid\b.*\bschema\b|\bschema\b.*\binvalid\b/i, why: 'schema 本身有问题' },
  // 实测（2026-08-14）：SDK 的 modelReasoningEffort 类型声明是
  // minimal|low|medium|high|xhigh，而服务端对 gpt-5.6-terra 回的是
  // "'minimal' is not supported ... Supported values are: 'none', 'low',
  // 'medium', 'high', 'xhigh', and 'max'" —— SDK 声明与服务端并不一致，
  // 且各模型支持的取值不同。这类参数错误重试多少次都一样。
  { re: /\bunsupported[_ ]value\b|\bis not supported with the\b/i, why: '参数取值不被该模型支持' },
  { re: /\binvalid[_ ]request[_ ]error\b/i, why: '请求参数有问题，重试无意义' },
];

/**
 * @returns {{retryable: boolean, matched: boolean, why: string}}
 *   matched=false 表示两张表都没命中 —— 调用方应当把它记进日志，作为补规则的线索。
 */
export function classify(err) {
  const msg = typeof err === 'string' ? err : (err && err.message) || String(err);
  for (const r of FATAL) {
    if (r.re.test(msg)) return { retryable: false, matched: true, why: r.why };
  }
  for (const r of RETRYABLE) {
    if (r.re.test(msg)) return { retryable: true, matched: true, why: r.why };
  }
  return { retryable: false, matched: false, why: '未识别的错误，按不可重试处理' };
}

/** 指数退避 + 抖动。抖动是为了避免一批同时失败的 agent 在同一时刻重试。 */
export function backoffMs(attempt, { base = 1000, factor = 2, max = 30000, jitter = 0.25 } = {}) {
  const raw = Math.min(max, base * Math.pow(factor, attempt - 1));
  const delta = raw * jitter;
  return Math.round(raw - delta + Math.random() * delta * 2);
}

export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new Error('aborted'));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(t);
      reject(new Error('aborted'));
    }, { once: true });
  });
}
