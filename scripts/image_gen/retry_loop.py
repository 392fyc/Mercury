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
)
_FEEDBACK_TAIL_MAX = 200  # chars per error line forwarded into prompt


def sanitize_stderr(text: str) -> str:
    """Scrub credential-shaped substrings, truncate, return last non-empty line.

    Public so the JSON serializer in `__main__._serialize` can reuse the
    same scrubbing for `frame_results[*].stderr` — Codex Slice C audit
    Medium #1 flagged that the documented stderr troubleshooting path is
    impossible to follow if the field is dropped, but raw stderr cannot
    be exposed because adapter-side libraries may dump credential-shaped
    tokens. The underscore-prefixed alias below preserves backward
    compatibility for in-tree tests that imported the private name.
    """
    cleaned = text
    for pattern, replacement in _SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    lines = [ln for ln in cleaned.splitlines() if ln.strip()]
    last = lines[-1] if lines else "unknown error"
    return last.strip()[:_FEEDBACK_TAIL_MAX]


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
