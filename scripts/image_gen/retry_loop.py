"""Retry loop with structured feedback into next attempt's prompt.

Per ADR §6.4: `max_retries = 3` (hard cap) describes the number of
RETRY attempts that follow an initial failed attempt. Total attempts
budget therefore = `1 + max_retries` (one initial + up to N retries).
Each retry forwards the previous attempt's `verify.fail_reasons` into
the bible's prompt composition as a `feedback` block — the model
conditions on what went wrong rather than re-rolling blind.

Verify is invoked on whatever frames were successfully generated each
attempt, even when some frames failed adapter invocation; the partial
verdict still informs the structured feedback. Generation errors are
sanitized (secret scrubbing + length cap) before being composed into
the next prompt to avoid leaking adapter-side credentials or noisy
internals to the next model call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .character_bible import CharacterBible
from .pipeline import (
    FrameResult,
    FrameSpec,
    GenerationOptions,
    generate_frames,
)
from .verify import VerifyConfig, VerifyResult, verify_frames

# Patterns scrubbed from adapter stderr before it enters the next prompt.
# Cover common credential shapes that an adapter-side library might dump
# (OpenAI keys, GitHub PATs, AWS access keys, Bearer tokens, generic
# `Authorization: ...` lines). New shapes can be added here without
# touching call sites. Patterns are intentionally permissive on tail
# length — better to over-scrub than to leak.
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-proj-[A-Za-z0-9_\-]{8,}"), "sk-proj-***"),
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "sk-***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{16,}"), "github_pat_***"),
    (re.compile(r"ghp_[A-Za-z0-9]{16,}"), "ghp_***"),
    (re.compile(r"AKIA[A-Z0-9]{12,}"), "AKIA***"),
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9_\-\.=]{8,}"), "Bearer ***"),
    (re.compile(r"(?im)^\s*Authorization\s*:\s*\S+.*$"), "Authorization: ***"),
    # Argus iter-1 Critical (security): generic key=value / key: value
    # redaction for adapter-side libraries that dump env or kwargs in
    # stderr (e.g. `OPENAI_API_KEY=sk-...`, `api_key: 'sk-...'`,
    # `password=hunter2`). The captured key keeps debuggability ("which
    # field leaked") while the value is replaced wholesale. We do NOT
    # anchor with `\b` because `_` is a word character, which would
    # block matches inside underscore-prefixed names like
    # `OPENAI_API_KEY`. The required `[=:]` anchor following the key
    # is itself a strong signal — bare keyword substrings without an
    # assignment operator (e.g. "secrets are good") will not match.
    (re.compile(
        r"(?i)(api[_-]?key|api[_-]?token|access[_-]?token|"
        r"refresh[_-]?token|client[_-]?secret|password|passwd|"
        r"secret|token)\s*[=:]\s*['\"]?[^\s'\"&,;]+"
    ), r"\1=***"),
)
_FEEDBACK_TAIL_MAX = 200  # chars per error line forwarded into prompt
_REPORT_TAIL_MAX = 2000   # chars per stderr blob forwarded into JSON report


def _scrub(text: str) -> str:
    """Apply every credential pattern to `text` (single source of truth).

    Returns the scrubbed string; layout (last-line vs. multi-line) is
    chosen by the caller via `sanitize_stderr` or `sanitize_stderr_full`.
    """
    cleaned = text
    for pattern, replacement in _SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def sanitize_stderr(text: str) -> str:
    """Scrub credentials and return the LAST non-empty line, ≤200 chars.

    Used by the retry-loop prompt feedback path: the next attempt's
    prompt includes only a short summary of the previous failure so we
    don't overwhelm model context. Last-line + length cap is an
    intentional contract for that consumer (Argus iter-2 Minor on
    information loss: confirmed intentional for the retry path; the
    JSON report path uses `sanitize_stderr_full` instead).
    """
    cleaned = _scrub(text)
    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    last = lines[-1] if lines else "unknown error"
    return last.strip()[:_FEEDBACK_TAIL_MAX]


def sanitize_stderr_full(text: str, *, max_chars: int = _REPORT_TAIL_MAX) -> str:
    """Scrub credentials and return the multi-line text up to `max_chars`.

    Used by the JSON serializer in `__main__._serialize` so the
    `frame_results[*].stderr` field preserves enough context to debug
    multi-line tracebacks / multi-stage adapter errors (Argus iter-2
    Minor: 200-char last-line truncation was insufficient for report
    consumers). Tail-truncates rather than head-truncates because real
    Python tracebacks put the most-actionable line LAST. Empty-line
    runs are collapsed to single newlines so the cap covers more
    actual content.
    """
    cleaned = _scrub(text)
    lines = [ln.rstrip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return "unknown error"
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    # Reserve marker length inside the budget so the returned string is
    # truly bounded by `max_chars` (Copilot iter-2: marker was prepended
    # ON TOP of the max_chars-truncated content, blowing past the cap).
    # Use len(joined) as an upper bound for the dropped-count digit
    # width — actual n is always ≤ len(joined), so reserve is always
    # large enough.
    marker_template = "…[truncated {n} chars]\n"
    reserve = len(marker_template.format(n=len(joined)))
    content_budget = max(0, max_chars - reserve)
    truncated = joined[-content_budget:] if content_budget else ""
    n_dropped = len(joined) - len(truncated)
    return f"…[truncated {n_dropped} chars]\n{truncated}"


# Backward-compat alias for `test_smoke.test_stderr_sanitization` and any
# other in-tree caller that imported the underscore-prefixed name.
_sanitize_stderr = sanitize_stderr


@dataclass
class RetryAttempt:
    attempt: int
    passed: bool
    frame_results: list[FrameResult]
    verify: VerifyResult
    feedback_used: str = ""


@dataclass
class RetryReport:
    passed: bool
    attempts: list[RetryAttempt] = field(default_factory=list)
    total_attempts: int = 0
    final_fail_reasons: list[str] = field(default_factory=list)


def _build_feedback(prev: RetryAttempt | None) -> str:
    if prev is None:
        return ""
    parts: list[str] = []
    gen_errors = [
        f"frame {fr.spec.index}: {sanitize_stderr(fr.stderr)}"
        for fr in prev.frame_results
        if not fr.success
    ]
    if gen_errors:
        parts.append("Previous generation errors: " + "; ".join(gen_errors))
    if prev.verify.fail_reasons:
        parts.append(
            "Previous verify failures: "
            + "; ".join(prev.verify.fail_reasons[:3])
        )
    return " ".join(parts)


def run_with_retry(bible: CharacterBible, frames: list[FrameSpec],
                   verify_cfg: VerifyConfig,
                   opts: GenerationOptions | None = None,
                   max_retries: int = 3) -> RetryReport:
    """Up to `1 + max_retries` total attempts (ADR §6.4 semantics).

    `max_retries` is the count of RETRY attempts after the initial one,
    so e.g. `max_retries=3` produces at most 4 total invocations of
    `generate_frames`. Negative values clamp to 0 retries (single
    initial attempt).
    """
    opts = opts or GenerationOptions()
    attempts: list[RetryAttempt] = []
    prev: RetryAttempt | None = None
    total_budget = 1 + max(0, max_retries)

    for attempt_num in range(1, total_budget + 1):
        feedback = _build_feedback(prev)
        results = generate_frames(bible, frames, opts=opts, feedback=feedback)
        good_paths: list[Path] = [r.out_path for r in results if r.success and r.out_path]
        verdict = verify_frames(good_paths, verify_cfg)
        all_generated = all(r.success for r in results)
        passed = verdict.passed and all_generated
        cur = RetryAttempt(
            attempt=attempt_num,
            passed=passed,
            frame_results=results,
            verify=verdict,
            feedback_used=feedback,
        )
        attempts.append(cur)
        if passed:
            return RetryReport(passed=True, attempts=attempts,
                               total_attempts=attempt_num)
        prev = cur

    last = attempts[-1] if attempts else None
    fail_reasons = list(last.verify.fail_reasons) if last else ["no attempts"]
    if last and any(not r.success for r in last.frame_results):
        miss = [r.spec.index for r in last.frame_results if not r.success]
        fail_reasons.append(f"generation failed for frames {miss}")
    return RetryReport(
        passed=False,
        attempts=attempts,
        total_attempts=len(attempts),
        final_fail_reasons=fail_reasons,
    )
