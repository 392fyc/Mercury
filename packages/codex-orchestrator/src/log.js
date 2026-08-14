/**
 * 结构化运行日志（JSONL）。
 *
 * Claude Code 的 Dynamic Workflow 有 `/workflows` 面板做可观测面；Codex 上没有
 * 宿主会话可挂，日志是唯一能看到「跑了什么、花了多少、丢了什么」的地方。
 * 所以这一层不是可选的调试设施，是编排层的必需品。
 *
 * 两条输出分开：
 *   - JSONL 落盘，机器读，事后审计与对拍用；
 *   - 一行摘要走 stderr，人读，实时看进度。stdout 留给编排脚本自己的产出，
 *     这样 `node run.js > result.json` 不会被日志污染。
 */

import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

/** 把任意错误压成一行可读文本（SDK 抛的是裸 Error，只有 message）。 */
export function errText(err) {
  if (!err) return '';
  if (typeof err === 'string') return err;
  return err.message || String(err);
}

export class RunLog {
  /**
   * @param {object} opts
   * @param {string|null} opts.file  JSONL 落盘路径；null 表示只输出到 stderr
   * @param {boolean} opts.quiet     true 时不往 stderr 写摘要
   * @param {string} opts.runId      本次运行的标识，写进每条记录
   */
  constructor({ file = null, quiet = false, runId = 'run' } = {}) {
    this.file = file;
    this.quiet = quiet;
    this.runId = runId;
    this.counts = { started: 0, ok: 0, failed: 0, dropped: 0, retried: 0 };
    this.usage = { input: 0, cachedInput: 0, output: 0, reasoning: 0 };
    if (file) mkdirSync(dirname(file), { recursive: true });
  }

  /** 写一条结构化记录。ts 由调用方注入，便于测试时固定时间。 */
  emit(event, fields = {}) {
    const rec = { ts: new Date().toISOString(), runId: this.runId, event, ...fields };
    if (this.file) {
      try {
        appendFileSync(this.file, JSON.stringify(rec) + '\n', 'utf8');
      } catch (e) {
        // 日志写失败不能拖垮整个编排 —— 降级到 stderr 并继续。
        process.stderr.write(`[orchestrator] 日志写入失败: ${errText(e)}\n`);
      }
    }
    return rec;
  }

  /** 人读的一行摘要。 */
  say(line) {
    if (!this.quiet) process.stderr.write(`[orchestrator] ${line}\n`);
  }

  agentStarted(label, attempt) {
    this.counts.started++;
    this.emit('agent.started', { label, attempt });
    this.say(`▶ ${label}${attempt > 1 ? ` (第 ${attempt} 次尝试)` : ''}`);
  }

  agentOk(label, ms, usage) {
    this.counts.ok++;
    if (usage) {
      this.usage.input += usage.input_tokens || 0;
      this.usage.cachedInput += usage.cached_input_tokens || 0;
      this.usage.output += usage.output_tokens || 0;
      this.usage.reasoning += usage.reasoning_output_tokens || 0;
    }
    this.emit('agent.ok', { label, ms, usage: usage || null });
    this.say(`✔ ${label} (${(ms / 1000).toFixed(1)}s)`);
  }

  agentRetry(label, attempt, reason) {
    this.counts.retried++;
    this.emit('agent.retry', { label, attempt, reason });
    this.say(`↻ ${label} 重试（第 ${attempt} 次失败：${reason}）`);
  }

  agentFailed(label, reason, attempts) {
    this.counts.failed++;
    this.emit('agent.failed', { label, reason, attempts });
    this.say(`✘ ${label} 失败（${attempts} 次尝试）：${reason}`);
  }

  /**
   * 被丢弃的工作量必须显式报出来 —— 这是 Mercury #385 的硬护栏，
   * 现役 7 个 workflow 脚本里 log() 被调用 77 次、全部用在这件事上。
   * 静默截断会让「覆盖了全部」这个印象与事实脱节。
   */
  dropped(what, kept, total, why) {
    this.counts.dropped += total - kept;
    this.emit('work.dropped', { what, kept, total, dropped: total - kept, why });
    this.say(`⚠ ${what}: 只处理 ${kept}/${total}，丢弃 ${total - kept} 项（${why}）`);
  }

  summary() {
    const s = { ...this.counts, usage: { ...this.usage } };
    this.emit('run.summary', s);
    this.say(
      `完成：成功 ${s.ok} / 失败 ${s.failed} / 重试 ${s.retried} / 丢弃 ${s.dropped}；` +
        `token 输入 ${s.usage.input}（缓存 ${s.usage.cachedInput}）输出 ${s.usage.output}`
    );
    return s;
  }
}
